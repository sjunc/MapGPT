"""CRS Policy. CRS는 LLM이 아니라 이 정책이 결정한다.

- 지도 반환: EPSG:4326
- 위상 연산(점-폴리곤 포함): 경계 Dataset의 CRS로 맞춘다.
- 거리·면적 연산(EPSG:5179 등)은 해당 Operation이 추가될 때 정책을 확장한다.
- 육각 격자: 같은 넓이의 칸을 미터로 만들어야 해서 EPSG:5179(UTM-K)에서 만든다.
"""

import re

from psycopg import sql

MAP_OUTPUT_SRID = 4326
AREA_SRID = 5179  # 미터 단위 격자·면적 (UTM-K)
SUPPORTED_SRIDS = frozenset({4326, 5179})
# 적재할 때 EPSG:4326으로 바꿀 수 있는 원본 좌표계 (국내 공공데이터에 흔한 것). 베셀 타원체 계열(2097·5174)도
# PostGIS(PROJ)가 측지계 변환을 함께 한다 (2026-11-22 확인: 국토지리정보원 7변수 정의와 0.05m 차이).
SOURCE_SRIDS = frozenset({4326, 2097, 5174, 5179, 5181, 5186})


class CrsError(ValueError):
    pass


def parse_srid(crs: str | None) -> int:
    if crs is None:
        raise CrsError("CRS가 명시되지 않았습니다")
    match = re.fullmatch(r"EPSG:(\d+)", crs)
    if match is None:
        raise CrsError(f"CRS 형식이 'EPSG:<코드>'가 아닙니다: {crs}")
    srid = int(match.group(1))
    if srid not in SUPPORTED_SRIDS:
        raise CrsError(f"지원하지 않는 CRS입니다: {crs} (지원: {sorted(SUPPORTED_SRIDS)})")
    return srid


def parse_source_srid(crs: str) -> int:
    """적재할 원본 좌표의 좌표계. 분석용 CRS(parse_srid)와 달리 적재 때 EPSG:4326으로 바꿀 수 있는 것을 받는다."""
    match = re.fullmatch(r"EPSG:(\d+)", crs)
    if match is None or int(match.group(1)) not in SOURCE_SRIDS:
        raise CrsError(f"적재할 수 없는 원본 좌표계입니다: {crs} (지원: {sorted(SOURCE_SRIDS)})")
    return int(match.group(1))


def transform_crs(geometry: sql.Composable, source_srid: int, target_srid: int) -> sql.Composable:
    if source_srid == target_srid:
        return geometry
    return sql.SQL("ST_Transform({}, {})").format(geometry, sql.Literal(target_srid))
