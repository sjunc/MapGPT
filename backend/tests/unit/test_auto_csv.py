from pathlib import Path

import pytest

from app.data_sources.auto_csv import CsvProfile, _find_code_mapping, _semantic_type, discover_csv_groups
from app.domain.enums import DataType, SemanticType, SpatialLevel


def _write(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.write_text(text, encoding=encoding)


def test_discovery_groups_partitions_and_infers_types(tmp_path):
    header = "업소번호,시도명,경도,위도,매출\n"
    _write(tmp_path / "상가_서울_202601.csv", header + "001,서울특별시,126.9,37.5,100\n")
    _write(tmp_path / "상가_경기_202601.csv", header + "002,경기도,127.1,37.4,200\n")

    [profile] = discover_csv_groups(tmp_path)

    assert len(profile.files) == 2
    assert profile.columns == ("업소번호", "시도명", "경도", "위도", "매출")
    assert profile.data_types == (
        DataType.STRING,
        DataType.STRING,
        DataType.FLOAT,
        DataType.FLOAT,
        DataType.INTEGER,
    )


def test_discovery_supports_cp949_and_separates_schemas(tmp_path):
    _write(tmp_path / "시설.csv", "시설명,가입자수\n가나다,10\n", encoding="cp949")
    _write(tmp_path / "통계.csv", "지역,값,기준년월\n서울,3,202601\n")

    profiles = discover_csv_groups(tmp_path)

    assert {profile.encoding for profile in profiles} == {"cp949", "utf-8-sig"}
    assert {len(profile.columns) for profile in profiles} == {2, 3}


def test_discovery_does_not_treat_nan_or_inf_as_numbers(tmp_path):
    _write(tmp_path / "측정.csv", "지점,값\nA,1.5\nB,NaN\nC,inf\n")

    [profile] = discover_csv_groups(tmp_path)

    assert profile.data_types == (DataType.STRING, DataType.STRING)


def test_code_mapping_prefers_the_finest_matching_level():
    # 시도·시군구 코드도 100% 일치하지만, 가장 세밀한 행정동 코드를 분석 키로 쓴다.
    profile = CsvProfile(
        files=(Path("사업체통계_202609.csv"),),
        encoding="utf-8-sig",
        columns=("시도코드", "시군구코드", "행정동코드", "사업체수"),
        samples=(("11", "11110", "1111051500", "3"), ("26", "26110", "2611051000", "5")),
        data_types=(DataType.STRING, DataType.STRING, DataType.STRING, DataType.INTEGER),
    )
    code_sets = {
        SpatialLevel.EMD: {"1111051500", "2611051000"},
        SpatialLevel.SIGUNGU: {"11110", "26110"},
        SpatialLevel.SIDO: {"11", "26"},
    }

    mapping = _find_code_mapping(profile, code_sets)

    assert (mapping.source_column, mapping.level, mapping.transform) == ("행정동코드", SpatialLevel.EMD, "identity")


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("시도명", SemanticType.SIDO_NAME),
        ("시군구명", SemanticType.SIGUNGU_NAME),
        ("행정동명", SemanticType.ADMINISTRATIVE_NAME),
        ("상호명", SemanticType.FACILITY_NAME),
    ],
)
def test_region_name_columns_are_not_facility_names(column, expected):
    assert _semantic_type(column, DataType.STRING, None) == expected
