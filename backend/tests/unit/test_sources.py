from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from app.config import Settings
from app.data_sources.sources import SourceDeclaration, SourceError, load_sources
from app.domain.catalog import SourceAcquisition
from app.domain.enums import SemanticType


def _declaration(**overrides) -> dict:
    data = {
        "source_id": "test_points",
        "title": "시험 시설 위치",
        "description": "시험용 시설 위치",
        "semantic_tags": ["시험"],
        "provider": "서울특별시",
        "source": "https://data.seoul.go.kr/",
        "license": "공공누리 제1유형",
        "connector": "seoul_openapi",
        "service": "TestPoints",
        "region": "서울특별시",
        "columns": [
            {"source": "NAME", "name": "시설명", "semantic": "facility_name", "description": "시설 이름"},
            {"source": "LAT", "name": "위도", "semantic": "latitude", "description": "위도"},
            {"source": "LNT", "name": "경도", "semantic": "longitude", "description": "경도"},
        ],
    }
    data.update(overrides)
    return data


def test_registered_source_files_are_valid():
    sources = load_sources(Settings().sources_dir)

    wifi = sources["seoul_public_wifi"]
    assert (wifi.connector, wifi.service, wifi.region) == ("seoul_openapi", "TbPublicWifiInfo", "서울특별시")
    assert wifi.coordinate_columns == ("위도", "경도")
    assert {column.source for column in wifi.columns} >= {"LAT", "LNT"}


def test_declaration_needs_exactly_one_latitude_and_longitude():
    columns = [column for column in _declaration()["columns"] if column["semantic"] != SemanticType.LONGITUDE]

    with pytest.raises(ValidationError, match="경도"):
        SourceDeclaration.model_validate(_declaration(columns=columns))


def test_declaration_rejects_duplicate_loaded_column_names():
    columns = [*_declaration()["columns"], {"source": "ADDR", "name": "시설명", "semantic": "text", "description": "-"}]

    with pytest.raises(ValidationError, match="시설명"):
        SourceDeclaration.model_validate(_declaration(columns=columns))


def test_load_sources_rejects_the_same_source_id_twice(tmp_path):
    text = yaml.safe_dump(_declaration(), allow_unicode=True)
    (tmp_path / "a.yaml").write_text(text, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(text, encoding="utf-8")

    with pytest.raises(SourceError, match="중복"):
        load_sources(tmp_path)


def test_fingerprint_changes_when_the_declaration_changes():
    original = SourceDeclaration.model_validate(_declaration())
    renamed = SourceDeclaration.model_validate(_declaration(title="다른 제목"))

    assert original.fingerprint == SourceDeclaration.model_validate(_declaration()).fingerprint
    assert original.fingerprint != renamed.fingerprint


@pytest.mark.parametrize(
    ("fetched_at", "fingerprint", "due"),
    [
        (None, "f1", True),  # 아직 받지 않음
        (date(2026, 11, 1), "f1", False),  # 주기 안
        (date(2026, 10, 1), "f1", True),  # 주기 지남
        (date(2026, 11, 1), "f2", True),  # 등록 파일이 바뀜
    ],
)
def test_acquisition_is_due_when_missing_stale_or_redeclared(fetched_at, fingerprint, due):
    acquisition = SourceAcquisition(
        source_id="test_points",
        connector="seoul_openapi",
        service="TestPoints",
        fingerprint="f1",
        refresh_days=30,
        fetched_at=fetched_at,
    )

    assert acquisition.is_due(date(2026, 11, 14), fingerprint) is due
