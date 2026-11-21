from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql://mapgpt:mapgpt@localhost:5432/mapgpt"
    cors_allow_origins: list[str] = []

    llm_provider: Literal["openai", "ollama"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6.1-sol"
    # Dataset 후보 검색(pgvector) 임베딩. LLM_PROVIDER와 관계없이 OpenAI를 쓴다.
    openai_embedding_model: str = "text-embedding-3-small"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str | None = None
    # thinking 모델의 추론 강도 (예: none이면 끔). 비우면 모델 기본값을 쓴다.
    ollama_reasoning_effort: str | None = None

    # 백그라운드 분석 작업 (POST /api/analysis-jobs). 시간 제한은 단계 사이에서 확인한다 (0이면 제한 없음).
    analysis_max_concurrency: int = 4
    analysis_time_budget_seconds: float = 180

    seoul_open_api_key: SecretStr | None = None
    raw_data_dir: Path = PROJECT_ROOT / "data" / "raw"
    sources_dir: Path = PROJECT_ROOT / "sources"  # 외부 출처 등록 파일 (*.yaml)


@lru_cache
def get_settings() -> Settings:
    return Settings()
