"""서울 열린데이터광장 OpenAPI 목록으로 '자동 후보' 출처를 만든다 (등록 파일 없이, 사람이 검토하기 전).

목록(SearchOpenAPIService, OA-2191)과 서비스별 입출력 인자(SearchOpenAPIIOValueService, OA-2192)로 등록 내용
(SourceDeclaration, reviewed=False)을 만들어 카탈로그에 '받기 전' 항목으로 올린다. 데이터는 분석에 처음 쓰일 때 받는다.

- 서울 전체 범위의 서비스만 올린다. 자치구 서비스(이름에 자치구가 있거나 자치구 호스트)는 서울 전체를 묻는 질문에
  한 구의 데이터만 쓰는 오답을 막으려고 뺀다.
- 위도·경도(또는 X·Y) 출력 컬럼이 한 쌍 있고, 기본 인자 밖의 필수 요청 인자가 없는 서비스만 올린다.
- 좌표계와 걸러 낼 행은 계열 규칙(sources/families)에서 정한다. 규칙이 없으면 받을 때 값과 상세 화면 안내로 정한다.
"""

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
from pydantic import ValidationError

from app.data_sources.acquisition import register_sources
from app.data_sources.adapters import seoul_childcare
from app.data_sources.seoul_openapi import SeoulApiError
from app.data_sources.sources import FamilyRule, SourceColumn, SourceDeclaration, load_families, load_sources
from app.domain.enums import SemanticType
from app.repositories.catalog_repository import delete_auto_datasets

MAIN_HOST = "openapi.seoul.go.kr:8088"
LIST_SERVICE = "SearchOpenAPIService"  # OA-2191 서울시 열린데이터광장 Open API 현황
COLUMNS_SERVICE = "SearchOpenAPIIOValueService"  # OA-2192 Open API 입출력 인자 조회
PORTAL_PAGE = "https://data.seoul.go.kr/dataList/{inf_id}/S/1/datasetView.do"
PROVIDER = "서울특별시 (서울 열린데이터광장)"
SEOUL_GU = (
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구", "은평구",
    "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구",
    "송파구", "강동구",
)  # fmt: skip
_STANDARD_PARAMS = frozenset({"KEY", "TYPE", "SERVICE", "START_INDEX", "END_INDEX"})
_BUILT_IN_SERVICES = frozenset({seoul_childcare.SERVICE})  # 내장 Dataset이 이미 받는 서비스 (어린이집)
# 좌표 컬럼은 이름이 제각각이다 (LAT·LNT·LA·LO·X·Y·XCRD 등). 이름은 후보만 고르고, 위경도인지는 받은 값으로 정한다.
_LATITUDE_NAMES = frozenset({"LAT", "LA", "LATITUDE", "Y", "YCRD", "YCOORD", "YPOINT", "YCODE", "Y_CRD", "Y_COORD"})
_LONGITUDE_NAMES = frozenset({"LNG", "LNT", "LOT", "LO", "LON", "LONGITUDE", "X", "XCRD", "XCOORD", "XPOINT", "XCODE"})
_LATITUDE_TEXT = re.compile(r"Y\s*좌표|\(\s*Y\s*\)|y_value", re.IGNORECASE)
_LONGITUDE_TEXT = re.compile(r"X\s*좌표|\(\s*X\s*\)|x_value", re.IGNORECASE)
_TAG_STOPWORDS = frozenset({"서울시", "서울특별시", "서울", "정보", "현황", "조회", "서비스", "목록", "데이터"})
_RESERVED_COLUMNS = frozenset({"geom", "unit_key"})
# '행정동별 전력 사용량 위치정보'처럼 행정구역마다 값이 하나인 통계는 점 데이터가 아니어서 지금 분석으로 셀 수 없다.
_ADMIN_STATISTICS = re.compile(r"(자치구|구|행정동|동|시군구|읍면동|지역)별")
_MAX_ID_LENGTH = 42  # 테이블 이름 뒤에 '__loading'·'_geom_idx'가 붙어도 PostgreSQL 이름 한도(63자) 안에 들게 한다


@dataclass
class SyncReport:
    services: int = 0
    candidates: list[str] = field(default_factory=list)  # 이번 동기화로 확인한 자동 후보 전체
    registered: list[str] = field(default_factory=list)  # 새로 올리거나 등록 내용이 바뀐 후보
    removed: list[str] = field(default_factory=list)  # 목록에서 사라졌거나 조건에 맞지 않게 된 후보
    skipped: Counter = field(default_factory=Counter)  # 후보로 올리지 않은 이유별 서비스 수


def sync_seoul_catalog(
    conn: psycopg.Connection,
    *,
    fetch_list: Callable[[], list[dict[str, Any]]],
    fetch_columns: Callable[[str], list[dict[str, Any]]],
    sources_dir: Path,
    cache_dir: Path,
    refresh: bool = False,
    pause: float = 0.0,
    log: Callable[[str], None] | None = None,
) -> SyncReport:
    """목록을 받아 자동 후보를 카탈로그와 맞춘다. 검색 임베딩은 호출하는 쪽에서 갱신한다.

    서비스별 입출력 인자는 cache_dir에 남겨 다시 받지 않는다(refresh=True면 새로 받는다). 입출력 인자를 받지 못한
    서비스의 기존 후보는 지우지 않는다 (일시적인 오류로 후보가 사라지지 않게).
    """
    services = fetch_list()
    if not services:
        raise SeoulApiError("OpenAPI 목록이 비어 있습니다")
    report = SyncReport(services=len(services))
    reviewed = {source.service for source in load_sources(sources_dir).values()} | _BUILT_IN_SERVICES
    families = load_families(sources_dir / "families")
    declarations: dict[str, SourceDeclaration] = {}
    unreadable: set[str] = set()
    for index, service in enumerate(services, start=1):
        reason = _skip_reason(service, reviewed)
        if reason is None:
            try:
                rows = _column_rows(str(service["INF_ID"]), fetch_columns, cache_dir, refresh, pause)
            except (SeoulApiError, OSError, ValueError):
                unreadable.add(dataset_id(str(service["API_SERVICE_NM"])))
                reason = "입출력 인자를 받지 못함"
            else:
                declaration, reason = candidate(service, rows, families)
                if declaration is not None and declaration.source_id in declarations:
                    declaration, reason = None, "같은 ID의 후보가 이미 있음"
                if declaration is not None:
                    declarations[declaration.source_id] = declaration
        if reason is not None:
            report.skipped[reason] += 1
        if log and index % 500 == 0:
            log(f"  목록 {index:,}/{len(services):,} 확인, 후보 {len(declarations):,}개")
    report.candidates = sorted(declarations)
    report.registered = sorted(register_sources(conn, declarations))
    report.removed = delete_auto_datasets(conn, keep=set(declarations) | unreadable)
    return report


def candidate(
    service: Mapping[str, Any], rows: list[Mapping[str, Any]], families: list[FamilyRule]
) -> tuple[SourceDeclaration | None, str | None]:
    """목록 행 하나와 그 입출력 인자로 자동 후보 등록 내용을 만든다. 만들지 않으면 (None, 이유)."""
    name = str(service["API_SERVICE_NM"]).strip()
    title = str(service["INF_NM"]).strip()
    requests = [row for row in rows if "요청" in str(row.get("PTYPE_NM"))]
    outputs = [row for row in rows if "출력" in str(row.get("PTYPE_NM")) and str(row.get("COL_ENG_NM") or "").strip()]
    if any(
        "필수" in str(row.get("REQ_VALTYPE")) and str(row.get("COL_ENG_NM")).strip().upper() not in _STANDARD_PARAMS
        for row in requests
    ):
        return None, "기본 인자 밖의 필수 요청 인자가 있음"
    pair = _coordinate_pair(outputs)
    if pair is None:
        return None, "위도·경도(X·Y) 컬럼 한 쌍이 없음"
    columns = _columns(outputs, pair)
    family = next((rule for rule in families if rule.matches(name)), None)
    if family and family.keep_rows and family.keep_rows.column not in {column.source for column in columns}:
        return None, f"계열 규칙({family.family_id})의 걸러 낼 컬럼이 없음"
    description = f"{title}. 서울 열린데이터광장 OpenAPI({name})의 위치(점) 데이터"
    if family and family.description_note:
        description += f". {family.description_note}"
    try:
        declaration = SourceDeclaration(
            source_id=dataset_id(name),
            title=title,
            description=description,
            semantic_tags=_tags(title),
            provider=PROVIDER,
            source=PORTAL_PAGE.format(inf_id=service["INF_ID"]),
            license=None,
            connector="seoul_openapi",
            service=name,
            region="서울특별시",
            refresh_days=(family.refresh_days if family and family.refresh_days else 30),
            crs=family.crs if family else None,
            keep_rows=family.keep_rows if family else None,
            reviewed=False,
            columns=columns,
        )
    except ValidationError:
        return None, "등록 내용을 만들 수 없음"
    return declaration, None


def dataset_id(service: str) -> str:
    """서비스명으로 만든 Dataset ID (예: LOCALDATA_020301 → seoul_localdata_020301)."""
    slug = re.sub(r"[^a-z0-9]+", "_", service.lower()).strip("_") or "service"
    identifier = f"seoul_{slug}"
    if len(identifier) > _MAX_ID_LENGTH:
        digest = hashlib.sha1(service.encode("utf-8")).hexdigest()[:6]
        identifier = f"{identifier[: _MAX_ID_LENGTH - 7]}_{digest}"
    return identifier


def _skip_reason(service: Mapping[str, Any], reviewed: set[str]) -> str | None:
    if str(service.get("API_SERVICE_NM")) in reviewed:
        return "등록 파일·내장 Dataset이 있는 서비스"
    if urlparse(str(service.get("REQ_URL"))).netloc.lower() != MAIN_HOST:
        return "자치구 호스트"
    if _names_district(str(service.get("INF_NM") or "")):
        return "자치구 데이터"
    if _ADMIN_STATISTICS.search(str(service.get("INF_NM") or "")):
        return "행정구역별 통계 (점 개수로 셀 수 없음)"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(service.get("INF_ID") or "")):
        return "서비스 ID 형식이 다름"
    return None


def _names_district(title: str) -> bool:
    """이름에 자치구가 있는 서비스 (예: '서울시 관악구 병원 인허가 정보'). '중구'는 다른 말에 섞이기 쉬워 단어로만 본다."""
    words = set(re.findall(r"[가-힣]+", title))
    return "중구" in words or any(gu in title for gu in SEOUL_GU if gu != "중구")


def _column_rows(
    inf_id: str, fetch_columns: Callable[[str], list[dict[str, Any]]], cache_dir: Path, refresh: bool, pause: float
) -> list[dict[str, Any]]:
    path = cache_dir / f"{inf_id}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    rows = fetch_columns(inf_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    if pause:
        time.sleep(pause)
    return rows


def _coordinate_pair(outputs: list[Mapping[str, Any]]) -> tuple[str, str] | None:
    """출력 컬럼 중 위도(Y)·경도(X) 한 쌍의 원본 이름. 한국어 이름에 위도·경도가 있으면 우선하고, 애매하면 None."""
    named = [(str(row["COL_ENG_NM"]).strip(), str(row.get("COL_KOR_NM") or "").strip()) for row in outputs]

    def pick(word: str, names: frozenset[str], text: re.Pattern[str]) -> str | None:
        scores = {
            eng: 2 if word in kor else 1
            for eng, kor in named
            if word in kor or eng.upper() in names or text.search(kor)
        }
        best = max(scores.values(), default=0)
        top = [eng for eng, score in scores.items() if score == best]
        return top[0] if len(top) == 1 else None

    latitude = pick("위도", _LATITUDE_NAMES, _LATITUDE_TEXT)
    longitude = pick("경도", _LONGITUDE_NAMES, _LONGITUDE_TEXT)
    if latitude is None or longitude is None or latitude == longitude:
        return None
    return latitude, longitude


def _columns(outputs: list[Mapping[str, Any]], pair: tuple[str, str]) -> list[SourceColumn]:
    latitude, longitude = pair
    columns: list[SourceColumn] = []
    for row in sorted(outputs, key=_order):
        source = str(row["COL_ENG_NM"]).strip()
        if source in {column.source for column in columns}:
            continue
        korean = str(row.get("COL_KOR_NM") or "").strip()
        if source == latitude:
            semantic = SemanticType.LATITUDE
        elif source == longitude:
            semantic = SemanticType.LONGITUDE
        else:
            semantic = _semantic(source, korean)
        explanation = str(row.get("COL_EXP") or "").strip()
        description = korean or source
        if explanation and explanation != korean:
            description = f"{description} ({explanation})"
        name = _column_name(source, korean, {column.name for column in columns})
        columns.append(SourceColumn(source=source, name=name, semantic=semantic, description=description[:200]))
    return columns


def _order(row: Mapping[str, Any]) -> tuple[int, str]:
    order = str(row.get("V_ORDER") or "")
    return (int(order) if order.isdigit() else 10**6, str(row.get("COL_ENG_NM")))


def _column_name(source: str, korean: str, used: set[str]) -> str:
    """적재 컬럼 이름: 한국어 이름에서 기호를 뺀 것 (없으면 원본 이름 소문자). 겹치면 번호를 붙인다."""
    base = re.sub(r"[^0-9A-Za-z가-힣_]", "", korean) or source.lower()
    if base.lower() in _RESERVED_COLUMNS:
        base = f"{base}_원본"
    name, number = base, 2
    while name in used:
        name, number = f"{base}_{number}", number + 1
    return name


def _semantic(source: str, korean: str) -> SemanticType:
    """컬럼 이름으로 고른 의미 유형. 분석 계획에 쓰는 힌트이고, 타입은 적재할 때 값으로 정한다."""
    text, upper = korean.replace(" ", ""), source.upper()
    if text in {"자치구", "자치구명", "구명", "시군구명"}:
        return SemanticType.SIGUNGU_NAME
    if re.search(r"(일자|일시|날짜|년도|연도)$", text) or re.search(r"(YMD|DATE|DTTM|_DT|YEAR)$", upper):
        return SemanticType.DATE
    if re.search(r"(구분|유형|분류|종류|상태|등급)(명|코드)?$", text):
        return SemanticType.CATEGORY
    if re.search(r"(번호|코드|ID|아이디)$", text) or re.search(r"(_NO|_ID|_CD|MGTNO)$", upper):
        return SemanticType.IDENTIFIER
    if re.search(r"(면적|금액|인원|규모|길이|높이|수량|건수|수)$", text):
        return SemanticType.MEASURE
    if re.search(r"(명|명칭|이름)$", text):
        return SemanticType.FACILITY_NAME
    return SemanticType.TEXT


def _tags(title: str) -> list[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]+", title)
    return list(dict.fromkeys(word for word in words if word not in _TAG_STOPWORDS and len(word) > 1))
