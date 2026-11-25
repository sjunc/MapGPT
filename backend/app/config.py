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
    # LLM·임베딩 호출 하나의 타임아웃(초)과 SDK 재시도 횟수. 로컬 thinking 모델은 타임아웃을 늘려야 할 수 있다.
    llm_timeout_seconds: float = 100
    llm_max_retries: int = 1

    # 공개 데모 비용·남용 방지 (POST /api/analyses만 센다).
    # 클라이언트 IP별 요청 제한 (slowapi 형식, 예: 5/minute, "10/minute;100/day"). 비우거나 0이면 끈다.
    analysis_rate_limit: str = "5/minute"
    # 한국 시간 자정부터 센 분석 실행(analysis.run) 수의 하루 한도. 모든 클라이언트를 합해 센다. 0이면 제한 없음.
    analysis_daily_limit: int = 300

    # 백그라운드 분석 작업 (POST /api/analysis-jobs). 시간 제한은 단계 사이에서 확인한다 (0이면 제한 없음).
    analysis_max_concurrency: int = 4
    analysis_time_budget_seconds: float = 180

    seoul_open_api_key: SecretStr | None = None
    # 자동 후보(등록 파일 없이 포털 정보로 등록한 출처)는 위치가 대상 지역 안에 있는 행이 이 비율 이상일 때만 쓴다.
    auto_source_min_located_ratio: float = 0.9
    raw_data_dir: Path = PROJECT_ROOT / "data" / "raw"
    sources_dir: Path = PROJECT_ROOT / "sources"  # 외부 출처 등록 파일 (*.yaml)


@lru_cache
def get_settings() -> Settings:
    return Settings()
