"""외부 출처 등록 파일(backend/sources/*.yaml)과 계열 규칙(backend/sources/families/*.yaml).

등록 파일은 사람이 한 번 검토하고 승인하는 선언형 매핑이다. 어느 커넥터로 무엇을 받고, 원본 컬럼을 어떤 이름과
의미로 적재할지 정한다. 적재할 때마다 LLM으로 컬럼을 맞추지 않는다 (데이터 전달 문서 5.3).
포털 정보로 자동으로 만든 후보(reviewed=False)도 같은 모양이고, 같은 형식의 서비스 묶음에 사람이 한 번 정한
계열 규칙(좌표계, 걸러 낼 행)을 적용한다.
"""

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from app.domain.base import StrictModel
from app.domain.enums import SemanticType

_RESERVED_COLUMNS = {"geom", "unit_key"}  # 적재기가 만드는 컬럼


class SourceError(RuntimeError):
    pass


class SourceColumn(StrictModel):
    source: str = Field(description="원본 컬럼 이름")
    name: str = Field(description="적재 컬럼 이름. 메타데이터와 SQL에 이 이름을 쓴다")
    semantic: SemanticType
    description: str


class RowFilter(StrictModel):
    """이 조건에 맞는 원본 행만 적재한다 (예: 영업 중인 사업장)."""

    column: str = Field(description="원본 컬럼 이름")
    values: list[str] = Field(min_length=1)


class SourceDeclaration(StrictModel):
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")  # Dataset ID이자 테이블 이름
    title: str
    description: str
    semantic_tags: list[str]
    provider: str
    source: str  # 원본 데이터셋 페이지
    license: str | None
    connector: Literal["seoul_openapi"]
    service: str
    region: str  # 대상 시도. 이 경계 밖에 떨어진 좌표는 위치를 만들지 않는다
    update_cycle: str | None = None
    refresh_days: int = Field(default=30, ge=1)  # 받은 지 이만큼 지나면 다시 받는다
    max_rows: int = Field(default=100_000, ge=1)  # 질문 처리 중에 바로 받는 최대 행 수
    # 좌표 컬럼의 좌표계. 평면좌표면 위도 자리에 Y(북쪽), 경도 자리에 X(동쪽) 컬럼을 둔다.
    # None이면 받은 값과 상세 화면 안내로 정한다 (포털 정보로 만든 자동 후보).
    crs: str | None = "EPSG:4326"
    keep_rows: RowFilter | None = None
    reviewed: bool = True  # 사람이 검토한 등록 파일이면 True, 포털 정보로 자동으로 만든 후보면 False
    columns: list[SourceColumn] = Field(min_length=2)  # 적재할 원본 컬럼. 나머지는 원본 사본에만 남는다

    @model_validator(mode="after")
    def _check_columns(self) -> "SourceDeclaration":
        for label, semantic in (("위도", SemanticType.LATITUDE), ("경도", SemanticType.LONGITUDE)):
            count = sum(column.semantic == semantic for column in self.columns)
            if count != 1:
                raise ValueError(f"{label}({semantic}) 컬럼이 정확히 1개여야 합니다 (현재 {count}개)")
        for attribute in ("source", "name"):
            values = [getattr(column, attribute) for column in self.columns]
            duplicated = sorted({value for value in values if values.count(value) > 1})
            if duplicated:
                raise ValueError(f"중복된 컬럼 {attribute}: {duplicated}")
        reserved = sorted({column.name for column in self.columns} & _RESERVED_COLUMNS)
        if reserved:
            raise ValueError(f"적재기가 쓰는 컬럼 이름입니다: {reserved}")
        if self.keep_rows and self.keep_rows.column not in {column.source for column in self.columns}:
            raise ValueError(f"keep_rows의 컬럼 '{self.keep_rows.column}'이 등록한 원본 컬럼에 없습니다")
        return self

    @property
    def coordinate_columns(self) -> tuple[str, str]:
        """(위도, 경도) 적재 컬럼 이름."""
        latitude = next(column.name for column in self.columns if column.semantic == SemanticType.LATITUDE)
        longitude = next(column.name for column in self.columns if column.semantic == SemanticType.LONGITUDE)
        return latitude, longitude

    @property
    def fingerprint(self) -> str:
        """등록 파일 내용 해시. 매핑이 바뀌면 이미 받은 데이터도 다시 적재해야 한다."""
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()[:16]


class FamilyRule(StrictModel):
    """같은 형식의 서비스 묶음(계열)에 사람이 한 번 정한 규칙. 자동 후보를 만들 때 적용한다."""

    family_id: str
    service_pattern: str  # 서비스명 정규식 (전체 일치)
    crs: str | None = None
    keep_rows: RowFilter | None = None
    refresh_days: int | None = Field(default=None, ge=1)
    description_note: str | None = None  # 자동 후보 설명에 덧붙인다

    @field_validator("service_pattern")
    @classmethod
    def _check_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"service_pattern이 정규식이 아닙니다: {error}") from error
        return value

    def matches(self, service: str) -> bool:
        return re.fullmatch(self.service_pattern, service) is not None


def load_families(directory: Path) -> list[FamilyRule]:
    """계열 규칙 파일을 모두 읽어 검증한다. 폴더가 없으면 규칙도 없다."""
    rules = []
    for path in sorted(directory.glob("*.yaml")) if directory.is_dir() else []:
        try:
            rules.append(FamilyRule.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))))
        except (yaml.YAMLError, ValidationError) as error:
            raise SourceError(f"계열 규칙 파일을 읽을 수 없습니다: {path.name}: {error}") from error
    return rules


def load_sources(directory: Path) -> dict[str, SourceDeclaration]:
    """등록 파일을 모두 읽어 검증한다. YAML은 safe_load로 읽고 Pydantic으로 타입을 확인한다."""
    sources: dict[str, SourceDeclaration] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            source = SourceDeclaration.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (yaml.YAMLError, ValidationError) as error:
            raise SourceError(f"등록 파일을 읽을 수 없습니다: {path.name}: {error}") from error
        if source.source_id in sources:
            raise SourceError(f"source_id가 중복됩니다: {source.source_id} ({path.name})")
        sources[source.source_id] = source
    return sources
