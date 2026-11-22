"""서울 OpenAPI 목록·입출력 인자로 자동 후보 등록 내용을 만드는 규칙 (네트워크 없음)."""

from app.config import Settings
from app.data_sources.seoul_catalog import candidate, dataset_id
from app.data_sources.sources import FamilyRule, load_families
from app.domain.enums import SemanticType

STANDARD = ["KEY", "TYPE", "SERVICE", "START_INDEX", "END_INDEX"]


def _service(name="TbPublicWifiInfo", title="서울시 공공와이파이 서비스 위치 정보", inf_id="OA-20883", host=None):
    return {
        "INF_ID": inf_id,
        "INF_NM": title,
        "API_SERVICE_NM": name,
        "REQ_URL": host or "http://openAPI.seoul.go.kr:8088",
    }


def _io(outputs, extra_requests=()):
    """(영문, 한글[, 설명]) 출력 인자와 (영문, 필수 여부) 추가 요청 인자로 입출력 인자 조회 결과를 만든다."""
    rows = [{"PTYPE_NM": "요청인자", "COL_ENG_NM": name, "REQ_VALTYPE": "STRING(필수)"} for name in STANDARD]
    rows += [
        {"PTYPE_NM": "요청인자", "COL_ENG_NM": name, "REQ_VALTYPE": f"STRING({need})"} for name, need in extra_requests
    ]
    for order, (eng, kor, *explanation) in enumerate(outputs, start=1):
        rows.append(
            {
                "PTYPE_NM": "출력값",
                "COL_ENG_NM": eng,
                "COL_KOR_NM": kor,
                "COL_EXP": explanation[0] if explanation else "",
                "V_ORDER": str(order),
            }
        )
    return rows


WIFI = _io([("X_SWIFI_MGR_NO", "관리번호"), ("X_SWIFI_MAIN_NM", "와이파이명"), ("LAT", "Y좌표"), ("LNT", "X좌표")])
LOCALDATA = _io(
    [
        ("MGTNO", "관리번호"),
        ("BPLCNM", "사업장명"),
        ("TRDSTATENM", "영업상태명"),
        ("APVPERMYMD", "인허가일자"),
        ("SITEAREA", "소재지면적"),
        ("X", "좌표정보(X)"),
        ("Y", "좌표정보(Y)"),
    ]
)
LOCALDATA_FAMILY = FamilyRule.model_validate(
    {
        "family_id": "localdata",
        "service_pattern": r"LOCALDATA_\d+",
        "crs": "EPSG:5174",
        "keep_rows": {"column": "TRDSTATENM", "values": ["영업/정상"]},
        "refresh_days": 7,
        "description_note": "영업 중인 사업장만 적재한다",
    }
)


def test_wifi_like_service_becomes_an_unreviewed_candidate_with_its_coordinate_pair():
    declaration, reason = candidate(_service(), WIFI, [])

    assert reason is None
    assert (declaration.source_id, declaration.service, declaration.region) == (
        "seoul_tbpublicwifiinfo",
        "TbPublicWifiInfo",
        "서울특별시",
    )
    assert declaration.reviewed is False and declaration.crs is None and declaration.keep_rows is None
    assert declaration.source == "https://data.seoul.go.kr/dataList/OA-20883/S/1/datasetView.do"
    assert declaration.coordinate_columns == ("Y좌표", "X좌표")  # 위도 자리 LAT, 경도 자리 LNT
    semantics = {column.source: column.semantic for column in declaration.columns}
    assert semantics == {
        "X_SWIFI_MGR_NO": SemanticType.IDENTIFIER,
        "X_SWIFI_MAIN_NM": SemanticType.FACILITY_NAME,
        "LAT": SemanticType.LATITUDE,
        "LNT": SemanticType.LONGITUDE,
    }
    assert declaration.semantic_tags == ["공공와이파이", "위치"]


def test_family_rule_sets_the_coordinate_system_and_rows_to_keep():
    declaration, _ = candidate(
        _service("LOCALDATA_020301", "서울시 동물병원 인허가 정보", "OA-16007"), LOCALDATA, [LOCALDATA_FAMILY]
    )

    assert declaration.crs == "EPSG:5174" and declaration.refresh_days == 7
    assert declaration.keep_rows.column == "TRDSTATENM" and declaration.keep_rows.values == ["영업/정상"]
    assert declaration.description.endswith("영업 중인 사업장만 적재한다")
    semantics = {column.source: column.semantic for column in declaration.columns}
    assert semantics["X"] == SemanticType.LONGITUDE and semantics["Y"] == SemanticType.LATITUDE
    assert semantics["TRDSTATENM"] == SemanticType.CATEGORY and semantics["APVPERMYMD"] == SemanticType.DATE
    assert semantics["SITEAREA"] == SemanticType.MEASURE and semantics["BPLCNM"] == SemanticType.FACILITY_NAME
    assert [column.name for column in declaration.columns][-2:] == ["좌표정보X", "좌표정보Y"]  # 기호를 뺀 이름


def test_family_rule_without_its_filter_column_is_not_applied_blindly():
    rows = _io([("BPLCNM", "사업장명"), ("X", "좌표정보(X)"), ("Y", "좌표정보(Y)")])

    declaration, reason = candidate(
        _service("LOCALDATA_020302", "서울시 동물약국 인허가 정보"), rows, [LOCALDATA_FAMILY]
    )

    assert declaration is None and "걸러 낼 컬럼" in reason


def test_services_without_a_clear_coordinate_pair_are_skipped():
    # 이름이 LOT·LNG로 시작해도 좌표가 아니다 (지번주소·장기체류 인구)
    misleading = _io([("LOTNO_ADDR", "지번주소"), ("LNGTR_STAY_FRGNR_CO", "장기체류외국인인구수")])
    two_latitudes = _io([("START_LAT", "시작 위도"), ("END_LAT", "끝 위도"), ("LNG", "경도")])

    for rows in (misleading, two_latitudes):
        declaration, reason = candidate(_service(), rows, [])
        assert declaration is None and "한 쌍" in reason


def test_korean_latitude_names_win_over_generic_x_y_names():
    rows = _io([("LA", "시설 위도(좌표값)"), ("LO", "시설 경도(좌표값)"), ("X", "좌표정보(X)"), ("Y", "좌표정보(Y)")])

    declaration, _ = candidate(_service(), rows, [])

    semantics = {column.source: column.semantic for column in declaration.columns}
    assert semantics["LA"] == SemanticType.LATITUDE and semantics["LO"] == SemanticType.LONGITUDE


def test_services_that_need_extra_request_parameters_are_skipped():
    outputs = [("LAT", "위도"), ("LNT", "경도")]
    optional = _io(outputs, extra_requests=[("X_SWIFI_WRDOFC", "선택")])
    required = _io(outputs, extra_requests=[("BOARD_ID", "필수")])

    assert candidate(_service(), optional, [])[0] is not None
    assert candidate(_service(), required, [])[1] == "기본 인자 밖의 필수 요청 인자가 있음"


def test_column_names_drop_symbols_avoid_reserved_names_and_stay_unique():
    rows = _io([("A", "명칭(국문)"), ("B", "명칭 국문"), ("GEOM", ""), ("LAT", "위도"), ("LNT", "경도")])

    declaration, _ = candidate(_service(), rows, [])

    assert [column.name for column in declaration.columns] == ["명칭국문", "명칭국문_2", "geom_원본", "위도", "경도"]


def test_dataset_ids_stay_within_postgres_name_limits():
    assert dataset_id("LOCALDATA_020301") == "seoul_localdata_020301"
    long_id = dataset_id("VeryLongServiceNameForSomeSeoulOpenApiDatasetWithManyWords")
    assert len(long_id) <= 42 and long_id.startswith("seoul_verylongservicename")
    assert long_id != dataset_id("VeryLongServiceNameForSomeSeoulOpenApiDatasetWithOtherWords")


def test_registered_family_files_are_valid():
    families = {rule.family_id: rule for rule in load_families(Settings().sources_dir / "families")}

    localdata = families["localdata"]
    assert localdata.matches("LOCALDATA_020301") and not localdata.matches("LOCALDATA_020301_GA")
    assert localdata.crs == "EPSG:5174" and localdata.keep_rows.values == ["영업/정상"]
