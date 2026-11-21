"""REST API. 실행: uvicorn app.api.main:app --reload"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from psycopg_pool import ConnectionPool

from app.api.jobs import AnalysisJobs, format_sse
from app.api.schemas import AnalysisJobResponse, AnalysisRequest, AnalysisResponse
from app.config import Settings, get_settings
from app.data_sources.acquisition import DataAcquirer
from app.domain.catalog import DatasetMetadata
from app.llm.base import LLMError, StructuredLLM
from app.llm.embeddings import Embedder, EmbeddingError
from app.llm.factory import create_embedder, create_llm
from app.repositories.catalog_repository import list_datasets
from app.repositories.run_repository import get_run
from app.workflows.analysis_workflow import AnalysisWorkflow


def get_pool(request: Request) -> ConnectionPool:
    return request.app.state.pool


def get_llm(request: Request) -> StructuredLLM:
    try:
        return create_llm(request.app.state.settings)
    except LLMError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def get_embedder(request: Request) -> Embedder:
    try:
        return create_embedder(request.app.state.settings)
    except EmbeddingError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


Pool = Annotated[ConnectionPool, Depends(get_pool)]
LLM = Annotated[StructuredLLM, Depends(get_llm)]
Embedding = Annotated[Embedder, Depends(get_embedder)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with ConnectionPool(settings.database_url, min_size=1, max_size=5) as pool:
            app.state.pool = pool
            budget = settings.analysis_time_budget_seconds or None
            app.state.jobs = AnalysisJobs(settings.analysis_max_concurrency, budget)
            try:
                yield
            finally:
                app.state.jobs.shutdown()

    app = FastAPI(title="MapGPT 공간 분석 Agent", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.state.settings = settings

    @app.post("/api/analyses", response_model=AnalysisResponse)
    def create_analysis(body: AnalysisRequest, pool: Pool, llm: LLM, embedder: Embedding) -> AnalysisResponse:
        """질문을 분석 워크플로로 실행한다. 분석 실패도 run으로 기록되며 status=failed로 반환한다."""
        acquirer = DataAcquirer.from_settings(settings, pool, embedder)
        state = AnalysisWorkflow(llm, pool, embedder, acquirer).run(body.question, body.scope)
        return AnalysisResponse(
            run_id=state.run_id, status=state.status, result=state.final_response, errors=state.errors
        )

    @app.post("/api/analysis-jobs", response_model=AnalysisJobResponse, status_code=202)
    def create_analysis_job(
        body: AnalysisRequest, request: Request, pool: Pool, llm: LLM, embedder: Embedding
    ) -> AnalysisJobResponse:
        """질문을 백그라운드에서 실행하고 바로 run_id를 돌려준다. 진행은 events, 결과는 GET /api/analyses/{run_id}."""
        acquirer = DataAcquirer.from_settings(settings, pool, embedder)
        job = request.app.state.jobs.submit(AnalysisWorkflow(llm, pool, embedder, acquirer), body.question, body.scope)
        return AnalysisJobResponse(
            run_id=job.run_id,
            status="running",
            events_url=f"/api/analysis-jobs/{job.run_id}/events",
            result_url=f"/api/analyses/{job.run_id}",
        )

    @app.get("/api/analysis-jobs/{run_id}/events")
    def stream_analysis_job(run_id: uuid.UUID, request: Request, pool: Pool) -> StreamingResponse:
        """단계 진행을 Server-Sent Events로 보낸다 (queued → started → step… → done). 다시 연결하면 처음부터 다시 보낸다."""
        job = request.app.state.jobs.get(str(run_id))
        if job is not None:
            events = job.stream()
        else:
            # 메모리에 없는 작업(오래됐거나 서버가 다시 시작됨)은 실행 기록의 최종 상태만 알린다.
            with pool.connection() as conn:
                run = get_run(conn, str(run_id))
            if run is None:
                raise HTTPException(status_code=404, detail="분석 작업이 없습니다")
            errors = (run["provenance"] or {}).get("errors") or []
            done = {
                "run_id": run["run_id"],
                "status": run["status"],
                "error_code": errors[-1]["code"] if errors else None,
            }
            events = iter([format_sse("done", done)])
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}  # nginx 같은 프록시가 모아 보내지 않게
        return StreamingResponse(events, media_type="text/event-stream", headers=headers)

    @app.post("/api/analysis-jobs/{run_id}/cancel", status_code=202)
    def cancel_analysis_job(run_id: uuid.UUID, request: Request) -> dict[str, str]:
        """취소를 요청한다. 진행 중인 단계가 끝난 뒤 멈추고 실행 기록은 CANCELLED 오류로 끝난다."""
        job = request.app.state.jobs.get(str(run_id))
        if job is None:
            raise HTTPException(status_code=404, detail="진행 중인 분석 작업이 없습니다")
        if job.done:
            raise HTTPException(status_code=409, detail="이미 끝난 분석 작업입니다")
        job.cancel.set()
        return {"run_id": job.run_id, "status": "cancelling"}

    @app.get("/api/analyses/{run_id}")
    def read_analysis(run_id: uuid.UUID, pool: Pool) -> dict[str, Any]:
        with pool.connection() as conn:
            run = get_run(conn, str(run_id))
        if run is None:
            raise HTTPException(status_code=404, detail="분석 실행 기록이 없습니다")
        return run

    @app.get("/api/datasets", response_model=list[DatasetMetadata])
    def read_datasets(pool: Pool) -> list[DatasetMetadata]:
        with pool.connection() as conn:
            return list_datasets(conn)

    @app.get("/api/health")
    def health(pool: Pool) -> dict[str, str]:
        try:
            with pool.connection() as conn:
                conn.execute("SELECT 1")
            database = "ok"
        except psycopg.Error as error:
            database = f"error: {error}"
        model = settings.openai_model if settings.llm_provider == "openai" else settings.ollama_model
        return {
            "status": "ok" if database == "ok" else "degraded",
            "database": database,
            "llm_provider": settings.llm_provider,
            "llm_model": model or "",
            "embedding_model": settings.openai_embedding_model,
        }

    return app


app = create_app()
