import io
import json

import pytest

from app.data_sources import seoul_openapi
from app.data_sources.seoul_openapi import SeoulApiError, SeoulRowLimitError, fetch_service


class _FakeOpener:
    """서울 OpenAPI처럼 경로의 시작·끝 위치로 페이지를 돌려준다. 호출한 URL을 기록한다.

    끝을 지나 요청하면 '데이터 없음'(INFO-200)을 준다. per_page면 따릉이(bikeList)처럼 서비스 이름이 아닌 키 아래에,
    전체 건수 대신 그 페이지의 건수를 주고, 오류를 RESULT 없이 준다.
    """

    def __init__(self, rows: list[dict], code: str = "INFO-000", per_page: bool = False):
        self.rows, self.code, self.per_page, self.urls = rows, code, per_page, []

    def __call__(self, url: str, timeout: float):
        self.urls.append(url)
        start, end = (int(part) for part in url.rstrip("/").split("/")[-2:])
        page = self.rows[start - 1 : end]
        if not page:
            empty = {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}
            return _response(empty if self.per_page else {"RESULT": empty})
        result = {"CODE": self.code, "MESSAGE": "정상 처리되었습니다" if self.code == "INFO-000" else "오류"}
        body = {"list_total_count": len(page) if self.per_page else len(self.rows), "RESULT": result, "row": page}
        return _response({"rentBikeStatus" if self.per_page else "TestPoints": body})


def _response(payload: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode())


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

    with pytest.raises(SeoulRowLimitError, match="한도"):
        fetch_service("secret-key", "TestPoints", max_rows=4)
    assert len(opener.urls) == 1


def test_fetch_service_stops_at_no_data_after_a_full_page_that_reports_only_itself(monkeypatch):
    opener = _FakeOpener([{"ID": "0"}, {"ID": "1"}])
    monkeypatch.setattr(seoul_openapi.urllib.request, "urlopen", opener)
    monkeypatch.setattr(seoul_openapi, "PAGE_SIZE", 2)

    assert [row["ID"] for row in fetch_service("secret-key", "TestPoints")] == ["0", "1"]
    assert len(opener.urls) == 2  # 전체 건수가 한 페이지와 같으면 다음 페이지를 한 번 더 본다


@pytest.mark.parametrize(("count", "calls"), [(5, 3), (4, 3), (1, 1)])
def test_fetch_service_pages_services_that_report_only_the_page_count(monkeypatch, count, calls):
    # 따릉이(bikeList): 응답이 rentBikeStatus 아래에 오고, list_total_count가 그 페이지의 건수다 (전체 2,746곳이 1,000·1,000·746)
    opener = _FakeOpener([{"ID": str(i)} for i in range(count)], per_page=True)
    monkeypatch.setattr(seoul_openapi.urllib.request, "urlopen", opener)
    monkeypatch.setattr(seoul_openapi, "PAGE_SIZE", 2)

    rows = fetch_service("secret-key", "bikeList")

    assert [row["ID"] for row in rows] == [str(i) for i in range(count)]
    assert len(opener.urls) == calls  # 5건: 2·2·1, 4건: 2·2 뒤 '데이터 없음', 1건: 짧은 첫 페이지


def test_fetch_service_applies_the_row_limit_to_services_that_report_only_the_page_count(monkeypatch):
    opener = _FakeOpener([{"ID": str(i)} for i in range(5)], per_page=True)
    monkeypatch.setattr(seoul_openapi.urllib.request, "urlopen", opener)
    monkeypatch.setattr(seoul_openapi, "PAGE_SIZE", 2)

    with pytest.raises(SeoulRowLimitError, match="한도"):
        fetch_service("secret-key", "bikeList", max_rows=3)
    assert len(opener.urls) == 2


def test_fetch_service_reports_errors_given_without_a_result_wrapper(monkeypatch):
    monkeypatch.setattr(seoul_openapi.urllib.request, "urlopen", _FakeOpener([], per_page=True))

    with pytest.raises(SeoulApiError, match="INFO-200"):
        fetch_service("secret-key", "bikeList")


def test_fetch_service_reports_api_errors_without_the_key(monkeypatch):
    monkeypatch.setattr(seoul_openapi.urllib.request, "urlopen", _FakeOpener([{"ID": "0"}], code="ERROR-500"))

    with pytest.raises(SeoulApiError) as error:
        fetch_service("secret-key", "TestPoints")
    assert "ERROR-500" in str(error.value) and "secret-key" not in str(error.value)


def test_fetch_service_appends_request_parameters_after_the_range(monkeypatch):
    urls = []

    def opener(url: str, timeout: float):
        urls.append(url)
        body = {
            "list_total_count": 1,
            "RESULT": {"CODE": "INFO-000", "MESSAGE": "정상"},
            "row": [{"COL_ENG_NM": "LAT"}],
        }
        return io.BytesIO(json.dumps({"SearchOpenAPIIOValueService": body}).encode())

    monkeypatch.setattr(seoul_openapi.urllib.request, "urlopen", opener)

    rows = fetch_service("secret-key", "SearchOpenAPIIOValueService", params=("OA-20883",))

    assert rows == [{"COL_ENG_NM": "LAT"}]
    assert urls == ["http://openapi.seoul.go.kr:8088/secret-key/json/SearchOpenAPIIOValueService/1/1000/OA-20883/"]
