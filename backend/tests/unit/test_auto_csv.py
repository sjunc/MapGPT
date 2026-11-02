from pathlib import Path

from app.data_sources.auto_csv import discover_csv_groups
from app.domain.enums import DataType


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
