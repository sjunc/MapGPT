import pytest

from app.gis.crs import CrsError, parse_source_srid


@pytest.mark.parametrize(("crs", "srid"), [("EPSG:4326", 4326), ("EPSG:5174", 5174), ("EPSG:5186", 5186)])
def test_source_crs_accepts_common_korean_systems(crs, srid):
    assert parse_source_srid(crs) == srid


@pytest.mark.parametrize("crs", ["EPSG:3857", "WGS84", "epsg:5174"])
def test_source_crs_rejects_other_or_malformed_systems(crs):
    with pytest.raises(CrsError, match="적재할 수 없는 원본 좌표계"):
        parse_source_srid(crs)
