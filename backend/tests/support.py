import csv
import io
import json
from pathlib import Path
from typing import Any

from app.domain.plan import AnalysisPlan

FIXTURES = Path(__file__).parent / "fixtures"
QUESTION = "서울에서 어린이 인구 대비 어린이집이 부족한 행정동을 알려줘."


def load_plan_dict() -> dict[str, Any]:
    return json.loads((FIXTURES / "plan_daycare.json").read_text(encoding="utf-8"))


def load_plan(**overrides: Any) -> AnalysisPlan:
    data = load_plan_dict()
    data.update(overrides)
    return AnalysisPlan.model_validate(data)


def population_csv(rows: list[dict[str, object]]) -> bytes:
    """행정안전부 연령별 인구 CSV(가로형, cp949). 행마다 code, male·female({나이: 인구}), total(선택)."""
    ages = [f"{a}세" for a in range(110)] + ["110세이상 "]
    header = ["행정기관코드", "기준연월", "시도명", "시군구명", "읍면동명", "계", "남자", "여자"]
    header += [f"{a}남자" for a in ages] + [f"{a}여자" for a in ages]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        male, female = row["male"], row["female"]
        total = row.get("total", sum(male.values()) + sum(female.values()))
        values = [
            row["code"],
            "2026-09-30",
            "서울특별시",
            "가가구",
            "가동",
            total,
            sum(male.values()),
            sum(female.values()),
        ]
        values += [male.get(a, 0) for a in range(111)] + [female.get(a, 0) for a in range(111)]
        writer.writerow(values)
    return buffer.getvalue().encode("cp949")
