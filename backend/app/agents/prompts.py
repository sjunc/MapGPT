"""LLM 단계별 프롬프트. LLM은 해석·선택·계획·설명만 하고 계산은 하지 않는다."""

import json
from typing import Any

from app.domain.catalog import DatasetMetadata, RegionRef
from app.domain.enums import SPATIAL_LEVEL_NAMES, SpatialLevel
from app.domain.policies import POLICIES
from app.domain.results import ValidationIssue


def _level(level: SpatialLevel) -> str:
    return f"{level}({SPATIAL_LEVEL_NAMES[level]})"


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _policy_text() -> str:
    lines = []
    for policy in POLICIES.values():
        lines.append(f"- {policy.intent}: {policy.title}. {policy.description}")
        lines.append(f"  · 해석: {policy.interpretation}")
        lines.extend(f"  · 정의: {definition}" for definition in policy.definitions)
    return "\n".join(lines)


def interpret_prompt(
    question: str,
    coverage: list[tuple[RegionRef, list[SpatialLevel]]],
    retry_reason: str | None = None,
) -> tuple[str, str]:
    available = "\n".join(
        f"- {region.name} ({region.level}): {', '.join(_level(u) for u in units) or '분석 가능한 단위 없음'}"
        for region, units in coverage
    )
    system = f"""너는 공간 분석 질문 해석기다. 사용자의 자연어 질문을 이 시스템이 지원하는 분석으로 해석할 수 있는지 판단하고 해석 결과만 반환한다. 계산이나 데이터 조회는 하지 않는다.

[지원하는 분석 유형(intent)과 분석 정책]
{_policy_text()}

[공간 단위(analysis_unit)]
{", ".join(_level(level) for level in SpatialLevel)}

[데이터가 있는 지역 — region.name (region.level): 분석 가능한 analysis_unit]
{available or "- (없음)"}

[규칙]
1. 지역별 개수·합계·평균·비율·순위 질문은 regional_summary다. 예를 들어 "대구에서 상가 수가 가장 많은 행정동 10곳"은 행정동별 상가 개수를 내림차순으로 비교하는 regional_summary다. supply_demand_gap은 시설(공급)이 인구(수요)에 비해 부족한지를 물을 때만 쓴다 (예: '~ 대비 ~가 부족한', '~당 시설 수', '시설이 부족한 곳'). 시설 수나 인구 수 한 가지만 세거나 순위를 매기는 질문은 시설·연령 이름이 들어 있어도 regional_summary다 (예: '구별 공공와이파이 수', '65세 이상 인구가 많은 동').
2. 질문이 위 분석 유형으로 표현되지 않으면 intent, region, analysis_unit을 null로 두고 unsupported_reason에 이유를 쓴다. 표현이 짧거나 구어체라는 이유만으로 지원하지 않는다고 판단하지 않는다.
3. region은 분석할 지역 자체다. region.name은 위 지역 목록의 이름으로 쓰고 (예: '서울' → '서울특별시', '부산 중구' → '부산광역시 중구'), region.level은 그 지역 자체의 행정 단위다 (예: 서울특별시 → sido). 결과를 나눌 단위는 region.level이 아니라 analysis_unit에 쓴다. 사용자가 말한 지역을 다른 지역으로 바꾸지 않는다. 같은 이름의 시군구가 여러 시도에 있을 수 있으므로, 질문에 시도가 없으면 시도를 추측해 붙이지 말고 질문의 시군구 이름만 쓴다 (예: '중구' → '중구'). region_phrase에는 질문에서 지역을 말한 표현을 질문 글자 그대로 옮겨 쓴다 (예: '서울', '부산 중구', '전국'). 질문이 지역을 말하지 않았으면 region_phrase와 region은 null이다 (분석 범위의 기준 지역은 따로 정해진다).
4. analysis_unit은 결과를 보여줄 공간 단위다 (예: '동별' → emd, '구별'·'자치구 중'·'가장 많은 구' → sigungu, '시도별'·'시도 중' → sido). 사용자가 말한 단위를 다른 단위로 바꾸지 않는다. '많은 곳'처럼 단위를 말하지 않았으면 그 지역보다 아래에서 분석 가능한 가장 세밀한 단위(emd > sigungu > sido 순)를 쓴다.
5. analysis_unit_phrase에는 질문에서 결과 단위를 직접 말한 표현을 질문 글자 그대로 옮겨 쓴다 (예: '인구가 가장 적은 구는?' → '구', '동별 인구' → '동별', '시도 중 1위' → '시도'). 지역 이름의 일부(예: '강남구'의 '구')는 단위가 아니다. 단위 이름(동·구·시도 등)이 든 표현만 쓰고, '많은 곳'처럼 단위를 말하지 않았으면 null이다.
6. assumptions에는 위 분석 정책 정의 외에 질문을 해석하며 추가로 둔 가정만 적는다. 정책 정의를 다시 쓰지 않으며, 없으면 빈 목록으로 둔다.
7. key_concepts에는 분석에 필요한 데이터 개념을 짧은 명사구로 적는다."""
    user = f"질문: {question}"
    if retry_reason:
        user += (
            "\n\n[재판정 요청]\n"
            f"이전 해석은 지원되지 않는다고 판단했다: {retry_reason}\n"
            "지역별 개수·합계·평균·비율·순위로 표현할 수 있는지 다시 확인하라. "
            "지원 가능한 해석이 하나라도 있으면 unsupported로 반환하지 말고 해당 intent와 지역·분석 단위를 채워라."
        )
    return system, user


def _dataset_summary(d: DatasetMetadata) -> dict[str, Any]:
    return {
        "dataset_id": d.dataset_id,
        "title": d.title,
        "description": d.description,
        "category": d.category,
        "semantic_tags": d.semantic_tags,
        "spatial": d.spatial.model_dump(mode="json", include={"level", "geometry_type", "coverage"}),
        "temporal": d.temporal.model_dump(mode="json"),
        "columns": {c.name: c.description for c in d.columns},
    }


def select_prompt(interpreted: dict[str, Any], candidates: list[DatasetMetadata]) -> tuple[str, str]:
    system = """너는 공간 분석에 필요한 Dataset을 고르는 선택기다. 해석된 분석 질문과 후보 Dataset 메타데이터를 보고 분석에 필요한 Dataset ID만 고른다.

[규칙]
1. 후보 목록에 있는 dataset_id만 쓴다. 후보에 없는 ID를 지어내지 않는다.
2. required_data에는 질문의 지표와 조건에 필요한 데이터를 하나씩, 짧은 한국어 이름(concept)과 그 데이터를 주는 후보 dataset_id로 적는다. 지역 이름과 분석 단위 경계는 적지 않는다 (지역 범위는 따로 확인한다).
   - 후보의 컬럼이나 필터로 만들 수 있으면 그 Dataset이 주는 데이터다 (예: 0~5세 인구 → 연령별 인구 Dataset).
   - 어느 후보로도 만들 수 없을 때만 dataset_id를 null로 둔다. 비슷해 보이는 다른 Dataset으로 대신하지 않는다.
3. datasets에는 분석 단위 경계와 required_data의 Dataset을 빠짐없이 넣고, 필요 없는 Dataset은 넣지 않는다.
4. rationale에 각 Dataset을 고른 이유를 짧게 쓴다."""
    user = f"[해석된 질문]\n{_json(interpreted)}\n\n[후보 Dataset]\n{_json([_dataset_summary(d) for d in candidates])}"
    return system, user


def _dataset_detail(d: DatasetMetadata) -> dict[str, Any]:
    return d.model_dump(
        mode="json",
        include={"dataset_id", "title", "description", "spatial", "temporal", "columns", "join_keys"},
        exclude={"spatial": {"bbox", "feature_count"}},
    )


def plan_prompt(
    interpreted: dict[str, Any],
    datasets: list[DatasetMetadata],
    feedback: tuple[dict[str, Any] | None, list[ValidationIssue]] | None = None,
) -> tuple[str, str]:
    system = f"""너는 공간 분석 계획(AnalysisPlan)을 만드는 Planner다. SQL이나 코드를 쓰지 않고 JSON Schema에 맞는 AnalysisPlan만 만든다. 실제 계산은 별도의 GIS 엔진이 이 계획을 검증한 뒤 수행한다.

[분석 정책]
{_policy_text()}

[Operation 의미]
- aggregate_by_area: Geometry가 Point인 Dataset(input)만 Polygon 경계 Dataset(boundary)의 각 단위 안에 있는 개수로 센다 (aggregation=count). Geometry가 없거나 Polygon인 Dataset에는 절대 쓰지 않는다.
- aggregate: Dataset(input)을 group_by 키(분석 단위 경계와 결합되는 join key 1개)별로 aggregation.field를 합계(sum), 평균(avg), 개수(count)로 집계한다. 행정코드가 있고 Geometry가 없는 Dataset은 이 operation을 쓴다. 주민등록 인구수는 population 컬럼을 sum하며 행 개수를 count하지 않는다.
- calculate_ratio: numerator ÷ denominator × scale. numerator와 denominator는 앞에서 만든 output 이름이다.
- rank: input(앞에서 만든 output)에 order 방향으로 순위를 매긴다.

[작성 규칙]
1. intent, region, analysis_unit은 해석 결과를 그대로 쓴다.
2. datasets에는 operation과 filter에서 쓰는 Dataset ID를 모두 넣는다. 주어진 Dataset만 쓴다.
3. 질문이나 분석 정책이 요구하는 행 조건만 filters에 Dataset별로 둔다 (예: 정책이 정의한 연령대). 기준일·유형 전체 나열처럼 요구되지 않은 조건은 추가하지 않는다 — Dataset은 이미 기준 시점의 데이터다. field는 그 Dataset의 컬럼명이고 값의 타입은 컬럼 data_type과 맞아야 한다. 분석 단위 경계 Dataset에는 filter를 두지 않는다 (지역은 region으로 정해진다).
4. operations는 실행 순서대로 쓰고, 뒤의 operation은 앞에서 만든 output만 참조한다.
5. output 이름은 snake_case 영문으로 서로 겹치지 않고 Dataset ID·컬럼명과도 겹치지 않는다. label은 짧은 한국어 이름이다.
6. output.metric에는 지도에 색으로 표시할 지표 값의 output 이름을 쓴다 (예: calculate_ratio의 output). rank의 output(순위)은 output.metric이 될 수 없다.
7. ranking.enabled가 true면 output.metric을 input으로 하는 rank operation이 있어야 한다.
8. regional_summary의 rank order는 질문이 적은·낮은 쪽부터(가장 적은 곳, 낮은 순 등)를 묻지 않는 한 desc다. 순위 방향을 말하지 않은 '순위'·'~별 개수' 질문도 desc다.
9. regional_summary에서는 질문이 비율('~당', '대비', '비율')을 묻지 않으면 calculate_ratio를 쓰지 않고, 질문이 말하지 않은 연령 조건을 더하지 않는다. 다른 분석 유형의 정의(예: 어린이 인구)는 질문이 그 개념을 말할 때만 쓴다."""
    user = f"[해석된 질문]\n{_json(interpreted)}\n\n[사용할 Dataset]\n{_json([_dataset_detail(d) for d in datasets])}"
    if feedback is not None:
        previous_plan, issues = feedback
        errors = "\n".join(f"- [{i.code}] {i.path or '-'}: {i.message}" for i in issues)
        user += f"\n\n[이전 계획의 검증 오류 — 모두 고쳐서 다시 작성]\n{errors}"
        if previous_plan is not None:
            user += f"\n\n[이전 계획]\n{_json(previous_plan)}"
    return system, user


def explain_prompt(facts: dict[str, Any]) -> tuple[str, str]:
    system = """너는 공간 분석 결과를 시민과 정책 담당자에게 설명하는 분석가다. 주어진 결과 JSON의 수치만 사용해 한국어로 3~5문장 요약(summary)을 쓴다.

[규칙]
1. 결과 JSON에 없는 수치나 사실을 만들지 않는다.
2. 지표의 의미와 질문 방향에 맞는 상위·하위 지역 몇 곳을 구체적인 수치와 함께 말한다.
3. 중요한 한계(기준 시점 차이, 제외된 데이터 등)가 있으면 한 문장으로 언급한다.
4. 순위 방향은 ranking_order를 따른다. 1위가 가장 큰 값이라고 가정하지 말고, 가장 많은·적은 지역은 value와 summary_stats(min·max)로 판단한다."""
    return system, f"[분석 결과]\n{_json(facts)}"
