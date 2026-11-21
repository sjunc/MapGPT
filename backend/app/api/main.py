"""REST API. 실행: uvicorn app.api.main:app --reload"""

import math
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.schemas import AnalysisRequest, AnalysisResponse
from app.config import Settings, get_settings
from app.data_sources.acquisition import DataAcquirer
from app.domain.catalog import DatasetMetadata
from app.llm.base import LLMError, StructuredLLM
from app.llm.embeddings import Embedder, EmbeddingError
from app.llm.factory import create_embedder, create_llm
from app.repositories.catalog_repository import list_datasets
from app.repositories.run_repository import count_runs_since, get_run
from app.workflows.analysis_workflow import AnalysisWorkflow


def get_pool(request: Request) -> ConnectionPool:
    return request.app.state.pool


KST = ZoneInfo("Asia/Seoul")


def get_llm(request: Request) -> StructuredLLM:
    # 클라이언트는 앱 시작 때 한 번 만든다. 설정이 없으면 그때의 오류를 요청마다 503으로 알린다.
    if request.app.state.llm is None:
        raise HTTPException(status_code=503, detail=request.app.state.llm_error)
    return request.app.state.llm


def get_embedder(request: Request) -> Embedder:
    if request.app.state.embedder is None:
        raise HTTPException(status_code=503, detail=request.app.state.embedder_error)
    return request.app.state.embedder


Pool = Annotated[ConnectionPool, Depends(get_pool)]
LLM = Annotated[StructuredLLM, Depends(get_llm)]
Embedding = Annotated[Embedder, Depends(get_embedder)]


def _rate_limit_exceeded(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    limit, key = request.state.view_rate_limit
    reset_at, _ = request.app.state.limiter.limiter.get_window_stats(limit, *key)
    retry_after = max(1, math.ceil(reset_at - time.time()))
    return JSONResponse(
        status_code=429,
        content={"detail": f"분석 요청이 너무 잦습니다 (허용: {exc.detail}). {retry_after}초 뒤에 다시 시도하세요"},
        headers={"Retry-After": str(retry_after)},
    )


def _check_daily_limit(pool: ConnectionPool, limit: int) -> None:
    """한국 시간 자정부터 기록된 분석 실행이 한도에 이르면 429. 실행 중인 분석은 끝나야 기록되므로 동시 요청 수만큼 넘을 수 있다."""
    if limit <= 0:
        return
    now = datetime.now(KST)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with pool.connection() as conn:
        used = count_runs_since(conn, midnight)
    if used >= limit:
        retry_after = math.ceil((midnight + timedelta(days=1) - now).total_seconds())
        raise HTTPException(
            status_code=429,
            detail=f"오늘의 분석 한도({limit}건)를 모두 썼습니다. 한국 시간 자정 이후에 다시 시도하세요",
            headers={"Retry-After": str(retry_after)},
        )


def _build_client(factory, settings: Settings, error_type: type[Exception]) -> tuple[Any, str | None]:
    try:
        return factory(settings), None
    except error_type as error:
        return None, str(error)


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
    # 성공 응답은 행정동 GeoJSON을 포함해 수백 KB다. 작은 응답(오류, 헬스 체크)은 압축하지 않는다.
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.state.settings = settings
    app.state.llm, app.state.llm_error = _build_client(create_llm, settings, LLMError)
    app.state.embedder, app.state.embedder_error = _build_client(create_embedder, settings, EmbeddingError)
    # 클라이언트 IP별 제한. 저장소가 프로세스 메모리라 --workers N이면 프로세스마다 따로 센다.
    # 리버스 프록시 뒤에서는 uvicorn --forwarded-allow-ips(FORWARDED_ALLOW_IPS)에 프록시 주소를 넣어야
    # request.client가 실제 클라이언트가 된다. X-Forwarded-For를 앱이 직접 믿지 않는다(위조 가능).
    app.state.limiter = Limiter(key_func=get_remote_address)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded)
    rate_limit = settings.analysis_rate_limit.strip()

    def create_analysis(
        request: Request, body: AnalysisRequest, pool: Pool, llm: LLM, embedder: Embedding
    ) -> AnalysisResponse:
        """질문을 분석 워크플로로 실행한다. 분석 실패도 run으로 기록되며 status=failed로 반환한다."""
        _check_daily_limit(pool, settings.analysis_daily_limit)
        acquirer = DataAcquirer.from_settings(settings, pool, embedder)
        state = AnalysisWorkflow(llm, pool, embedder, acquirer).run(body.question, body.scope)
        return AnalysisResponse(
            run_id=state.run_id, status=state.status, result=state.final_response, errors=state.errors
        )

    if rate_limit and rate_limit != "0":
        create_analysis = app.state.limiter.limit(rate_limit)(create_analysis)
    app.post("/api/analyses", response_model=AnalysisResponse)(create_analysis)

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
