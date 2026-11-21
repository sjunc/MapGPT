import csv
import io

from app.workflows.export import UTF8_BOM, export_filename, result_csv, result_geojson


def _feature(key: str, name: str, rank: int | None, count: int | None, ratio: float | None) -> dict:
    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "unit_key": key,
            "unit_name": name,
            "parent_name": "종로구",
            "null_reason": None if ratio is not None else "zero_denominator",
            "label_point": [126.9, 37.5],
            "daycare_count": count,
            "daycare_per_1000_children": ratio,
            "shortage_rank": rank,
        },
    }


def _response() -> dict:
    return {
        "run_id": "0123456789abcdef",
        "metric_labels": {
            "daycare_count": "어린이집 수",
            "daycare_per_1000_children": "0~5세 1,000명당 어린이집 수",
            "shortage_rank": "부족 순위",
        },
        "rank_metric": "shortage_rank",
        "map_layer": {
            "geojson": {
                "type": "FeatureCollection",
                "features": [
                    _feature("1111000002", "나동", 2, 5, 12.5),
                    _feature("1111000003", "다동", None, 0, None),
                    _feature("1111000001", "가동", 1, 3, 4.25),
                ],
            }
        },
    }


def test_csv_has_bom_korean_headers_and_rows_in_rank_order():
    text = result_csv(_response())

    assert text.startswith(UTF8_BOM)
    rows = list(csv.reader(io.StringIO(text.removeprefix(UTF8_BOM))))
    assert rows[0] == ["순위", "지역 코드", "지역 이름", "상위 지역", "어린이집 수", "0~5세 1,000명당 어린이집 수"]
    assert rows[1:] == [
        ["1", "1111000001", "가동", "종로구", "3", "4.25"],
        ["2", "1111000002", "나동", "종로구", "5", "12.5"],
        # 순위가 없는(값 없음) 단위는 맨 뒤에 빈 칸으로 둔다.
        ["", "1111000003", "다동", "종로구", "0", ""],
    ]


def test_csv_without_ranking_keeps_rank_column_empty():
    response = _response()
    response["rank_metric"] = None
    del response["metric_labels"]["shortage_rank"]

    rows = list(csv.reader(io.StringIO(result_csv(response).removeprefix(UTF8_BOM))))

    assert [row[0] for row in rows[1:]] == ["", "", ""]
    assert [row[2] for row in rows[1:]] == ["가동", "나동", "다동"]  # 순위가 없으면 지역 코드순


def test_geojson_is_the_map_feature_collection():
    response = _response()

    assert result_geojson(response) is response["map_layer"]["geojson"]


def test_filename_uses_first_eight_characters_of_run_id():
    assert export_filename("0123456789abcdef", "csv") == "mapgpt-01234567.csv"
