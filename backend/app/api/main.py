"""REST API. 실행: uvicorn app.api.main:app --reload"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from psycopg_pool import ConnectionPool

from app.api.schemas import AnalysisRequest, AnalysisResponse
from app.catalog.suggestions import suggested_questions
from app.config import Settings, get_settings
from app.data_sources.acquisition import DataAcquirer
from app.domain.catalog import DatasetMetadata
from app.domain.enums import AnalysisScope
from app.llm.base import LLMError, StructuredLLM
from app.llm.embeddings import Embedder, EmbeddingError
from app.llm.factory import create_embedder, create_llm
from app.repositories.catalog_repository import list_datasets
from app.repositories.run_repository import get_run
from app.workflows.analysis_workflow import AnalysisWorkflow
from app.workflows.export import export_filename, result_csv, result_geojson


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
            yield

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

    @app.get("/api/analyses/{run_id}")
    def read_analysis(run_id: uuid.UUID, pool: Pool) -> dict[str, Any]:
        with pool.connection() as conn:
            run = get_run(conn, str(run_id))
        if run is None:
            raise HTTPException(status_code=404, detail="분석 실행 기록이 없습니다")
        return run

    @app.get("/api/analyses/{run_id}/export")
    def export_analysis(
        run_id: uuid.UUID,
        pool: Pool,
        export_format: Annotated[Literal["csv", "geojson"], Query(alias="format")] = "csv",
    ) -> Response:
        """저장된 분석 결과를 파일로 내려받는다. CSV는 분석 단위마다 한 행, GeoJSON은 지도에 그린 FeatureCollection."""
        with pool.connection() as conn:
            run = get_run(conn, str(run_id))
        if run is None:
            raise HTTPException(status_code=404, detail="분석 실행 기록이 없습니다")
        if run["result"] is None:
            raise HTTPException(
                status_code=409, detail="이 실행에는 내려받을 분석 결과가 없습니다 (실패했거나 진행 중)"
            )
        headers = {"Content-Disposition": f'attachment; filename="{export_filename(run["run_id"], export_format)}"'}
        if export_format == "csv":
            return Response(result_csv(run["result"]), media_type="text/csv; charset=utf-8", headers=headers)
        return JSONResponse(result_geojson(run["result"]), media_type="application/geo+json", headers=headers)

    @app.get("/api/datasets", response_model=list[DatasetMetadata])
    def read_datasets(pool: Pool) -> list[DatasetMetadata]:
        with pool.connection() as conn:
            return list_datasets(conn)

    @app.get("/api/suggestions", response_model=list[str])
    def read_suggestions(pool: Pool, scope: AnalysisScope = AnalysisScope.SEOUL) -> list[str]:
        """화면 빈 상태의 예시 질문. 필요한 Dataset이 모두 등록된 질문만 돌려준다."""
        with pool.connection() as conn:
            registered = {d.dataset_id for d in list_datasets(conn)}
        return suggested_questions(scope, registered)

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
