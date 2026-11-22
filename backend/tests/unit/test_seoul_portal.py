"""서울 열린데이터광장 상세 화면에서 설명·이용 조건·갱신주기·좌표계 안내를 읽는 규칙 (네트워크 없음)."""

from app.data_sources import seoul_portal
from app.data_sources.seoul_portal import fetch_portal_page, parse_portal_page

# 실제 화면(OA-16007, 2026-11-22)의 구조를 줄인 것. 설명은 HTML이 한 번 더 이스케이프되어 있다.
PAGE = """
<meta name="title" content="서울시 동물병원 인허가 정보"/>
<meta name="keywords" content="동물, 병원, 의료"/>
<meta name="description" content="동물을 진료하는 기관정보 &lt;br /&gt;* 좌표안내 : 중부원점TM(EPSG:5174) 좌표계에 따른
 좌표정보이며 위경도 좌표는 제공하고 있지 않음 &lt;br /&gt;* 시군구코드명은 &#034;자치구 기관코드&#034;에서 확인"/>
<table>
  <tr> <th scope="row">갱신주기</th> <td>매일</td> <th scope="row">분류</th> <td>보건</td> </tr>
  <tr> <th scope="row">이용허락범위</th> <!-- <td colspan="3"> --> <td> <div class="ccl-txt"> 이용허락범위 제한 없음
  </div> </td> </tr>
</table>
"""


def test_portal_page_gives_description_keywords_license_and_update_cycle():
    page = parse_portal_page(PAGE)

    assert page.description.splitlines() == [
        "동물을 진료하는 기관정보",
        "* 좌표안내 : 중부원점TM(EPSG:5174) 좌표계에 따른",
        "좌표정보이며 위경도 좌표는 제공하고 있지 않음",
        '* 시군구코드명은 "자치구 기관코드"에서 확인',
    ]
    assert page.keywords == ["동물", "병원", "의료"]
    assert (page.license, page.update_cycle) == ("이용허락범위 제한 없음", "매일")
    assert page.crs == "EPSG:5174"


def test_coordinate_notes_map_to_crs_codes():
    wgs84 = parse_portal_page('<meta name="description" content="무선인터넷 위치 &lt;br /&gt;※ 좌표계 : WGS84"/>')
    silent = parse_portal_page('<meta name="description" content="위치 정보입니다"/>')

    assert wgs84.crs == "EPSG:4326" and silent.crs is None
    assert parse_portal_page("<html></html>").description is None


def test_only_the_seoul_portal_is_read(monkeypatch):
    calls = []
    monkeypatch.setattr(seoul_portal.urllib.request, "urlopen", lambda *args, **kwargs: calls.append(args))

    assert fetch_portal_page("https://example.com/dataList/OA-1/S/1/datasetView.do") is None
    assert calls == []


def test_unreadable_pages_are_skipped(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("연결 실패")

    monkeypatch.setattr(seoul_portal.urllib.request, "urlopen", fail)

    assert fetch_portal_page("https://data.seoul.go.kr/dataList/OA-16007/S/1/datasetView.do") is None
