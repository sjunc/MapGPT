"""저장된 분석 결과(analysis.result의 FinalResponse JSON)를 내려받을 파일로 바꾼다 (I/O 없음).

예전 실행 기록은 지금 FinalResponse 모델에 없는 필드가 빠져 있을 수 있어, 모델로 다시 검증하지 않고
내려받기에 쓰는 키(run_id, metric_labels, rank_metric, map_layer.geojson)만 읽는다.
"""

import csv
import io
from typing import Any

# 엑셀이 UTF-8 CSV의 한글을 깨뜨리지 않도록 파일 앞에 BOM을 둔다.
UTF8_BOM = "﻿"
UNIT_HEADERS = ("순위", "지역 코드", "지역 이름", "상위 지역")


def export_filename(run_id: str, extension: str) -> str:
    return f"mapgpt-{run_id[:8]}.{extension}"


def result_geojson(response: dict[str, Any]) -> dict[str, Any]:
    """지도에 그린 분석 단위 FeatureCollection (EPSG:4326). properties에 모든 지표 값이 있다."""
    return response["map_layer"]["geojson"]


def result_csv(response: dict[str, Any]) -> str:
    """분석 단위마다 한 행: 순위, 지역 코드·이름·상위 지역, 지표 값(열 이름은 지표 라벨). 순위순, 순위 없는 단위는 뒤."""
    rank_metric = response.get("rank_metric")
    metrics = [(name, label) for name, label in response["metric_labels"].items() if name != rank_metric]
    features = response["map_layer"]["geojson"]["features"]

    def rank_of(properties: dict[str, Any]) -> Any:
        return properties.get(rank_metric) if rank_metric else None

    rows = sorted(
        (feature["properties"] for feature in features),
        key=lambda p: (rank_of(p) is None, rank_of(p) or 0, p["unit_key"]),
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([*UNIT_HEADERS, *(label for _, label in metrics)])
    for properties in rows:
        rank = rank_of(properties)
        writer.writerow(
            [
                "" if rank is None else rank,
                properties["unit_key"],
                properties["unit_name"],
                properties.get("parent_name") or "",
                *("" if properties.get(name) is None else properties[name] for name, _ in metrics),
            ]
        )
    return UTF8_BOM + buffer.getvalue()
