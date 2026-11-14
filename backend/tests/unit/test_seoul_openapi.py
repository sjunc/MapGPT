import io
import json

import pytest

from app.data_sources import seoul_openapi
from app.data_sources.seoul_openapi import SeoulApiError, fetch_service


class _FakeOpener:
    """서울 OpenAPI처럼 경로의 시작·끝 위치로 페이지를 돌려준다. 호출한 URL을 기록한다."""

    def __init__(self, rows: list[dict], code: str = "INFO-000"):
        self.rows, self.code, self.urls = rows, code, []

    def __call__(self, url: str, timeout: float):
        self.urls.append(url)
        start, end = (int(part) for part in url.rstrip("/").split("/")[-2:])
        result = {"CODE": self.code, "MESSAGE": "정상 처리되었습니다" if self.code == "INFO-000" else "오류"}
        body = {"list_total_count": len(self.rows), "RESULT": result, "row": self.rows[start - 1 : end]}
        return io.BytesIO(json.dumps({"TestPoints": body}).encode())


def test_fetch_service_reads_every_page(monkeypatch):
    opener = _FakeOpener([{"ID": str(i)} for i in range(5)])
    monkeypatch.setattr(seoul_openapi.urllib.request, "urlopen", opener)
    monkeypatch.setattr(seoul_openapi, "PAGE_SIZE", 2)

    rows = fetch_service("secret-key", "TestPoints")

    assert [row["ID"] for row in rows] == ["0", "1", "2", "3", "4"]
    assert len(opener.urls) == 3


def test_fetch_service_stops_before_paging_when_over_the_row_limit(monkeypatch):
    opener = _FakeOpener([{"ID": str(i)} for i in range(5)])
    monkeypatch.setattr(seoul_openapi.urllib.request, "urlopen", opener)
    monkeypatch.setattr(seoul_openapi, "PAGE_SIZE", 2)

    with pytest.raises(SeoulApiError, match="한도"):
        fetch_service("secret-key", "TestPoints", max_rows=4)
    assert len(opener.urls) == 1


def test_fetch_service_reports_api_errors_without_the_key(monkeypatch):
    monkeypatch.setattr(seoul_openapi.urllib.request, "urlopen", _FakeOpener([{"ID": "0"}], code="ERROR-500"))

    with pytest.raises(SeoulApiError) as error:
        fetch_service("secret-key", "TestPoints")
    assert "ERROR-500" in str(error.value) and "secret-key" not in str(error.value)
