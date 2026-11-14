"""어린이집 Adapter: 서울 열린데이터광장 OpenAPI ChildCareInfo."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.data_sources.seoul_openapi import fetch_service

SERVICE = "ChildCareInfo"


@dataclass(frozen=True)
class FacilityRecord:
    facility_id: str
    name: str
    facility_type: str | None
    status: str
    declared_sigungu_cd: str  # STCODE 앞 5자리 = 신고 시군구 코드
    latitude: float | None
    longitude: float | None
    reference_date: date


def fetch_rows(api_key: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """전체 원본 행을 페이지 단위로 수집한다. 인증키는 로그·예외 메시지에 남기지 않는다."""
    return fetch_service(api_key, SERVICE, timeout=timeout)


def parse_facilities(rows: list[dict[str, Any]]) -> list[FacilityRecord]:
    return [
        FacilityRecord(
            facility_id=row["STCODE"],
            name=row["CRNAME"].strip(),
            facility_type=row["CRTYPENAME"].strip() or None,
            status=row["CRSTATUSNAME"].strip(),
            declared_sigungu_cd=row["STCODE"][:5],
            latitude=_to_coordinate(row["LA"]),
            longitude=_to_coordinate(row["LO"]),
            reference_date=date.fromisoformat(row["DATASTDRDT"]),
        )
        for row in rows
    ]


def _to_coordinate(value: str | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number != 0 else None
