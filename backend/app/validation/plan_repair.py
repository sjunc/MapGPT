"""Plan Repair: LLM 계획에서 반복되는 기계적인 실수를 검증 전에 결정론적으로 고친다.

LLM은 같은 실수(위치 없는 표를 공간 집계, 같은 Dataset에 필터 두 번 등)를 되풀이해 계획을 다시 만들게 하고,
두 번째도 틀리면 분석이 실패한다. 의도가 하나로 정해지는 경우만 고치며, 고친 계획도 Plan Validator를 그대로
통과해야 실행된다. 고친 내용은 PLAN_REPAIRED 경고로 계획 시도 기록에 남긴다.
"""

import re
from collections.abc import Mapping

from app.domain.catalog import DatasetMetadata, JoinKey, RegionRef
from app.domain.enums import (
    AggregationFunction,
    AnalysisIntent,
    FilterOperator,
    LogicalOperator,
    SemanticType,
    Severity,
    SortOrder,
)
from app.domain.filters import FilterCondition, FilterExpression, FilterGroup, iter_conditions
from app.domain.operations import (
    AggregateByAreaOperation,
    AggregateOperation,
    AggregationSpec,
    Operation,
    RankOperation,
)
from app.domain.plan import AnalysisPlan, DatasetFilter, OutputSpec, unit_boundary_candidates
from app.domain.results import ValidationIssue

_REGION_FIELDS = {SemanticType.SIDO_NAME, SemanticType.SIDO_CODE, SemanticType.SIGUNGU_NAME, SemanticType.SIGUNGU_CODE}

# 순위 방향을 정하는 말. 공백을 지운 질문에서 찾는다. 부정·강조 표현은 먼저 뜻을 정하고 지운다
# ('적지 않은'은 많은 쪽, '많이 부족한'·'높지 않은'은 적은 쪽).
_FLIPPED_TO_HIGH = re.compile(r"(적|낮|작)지(는|도)?않|부족하지(는|도)?않")
_FLIPPED_TO_LOW = re.compile(r"(많|높|크|넓)지(는|도)?않|많이(부족|모자)")
_HIGH_WORDS = re.compile(r"많|높|큰|크[게고다]|최대|최고|상위|1위|내림차순|밀집|몰려")
# '면적', '누적', '목적' 같은 낱말 속 '적'은 방향이 아니다.
_LOW_WORDS = re.compile(
    r"(?<![면누목지실체합효성정역동량])적[은게을고다]|낮|작[은게을고다]|최소|최저|하위|꼴찌|부족|모자|덜|오름차순"
)


def question_rank_order(question: str) -> SortOrder | None:
    """regional_summary 질문이 묻는 순위 방향. 많은·적은 쪽을 함께 물으면 None.

    방향을 말하지 않으면 desc다. 프롬프트 규칙 8과 regional_summary 정책(rank_order)이 그렇게 정한다.
    """
    text = "".join(question.split())
    high = bool(_FLIPPED_TO_HIGH.search(text))
    low = bool(_FLIPPED_TO_LOW.search(text))
    text = _FLIPPED_TO_LOW.sub(" ", _FLIPPED_TO_HIGH.sub(" ", text))
    high = high or bool(_HIGH_WORDS.search(text))
    low = low or bool(_LOW_WORDS.search(text))
    if high and low:
        return None
    return SortOrder.ASC if low else SortOrder.DESC


def repair_plan(
    plan: AnalysisPlan, datasets: Mapping[str, DatasetMetadata], region: RegionRef | None, question: str = ""
) -> tuple[AnalysisPlan, list[ValidationIssue]]:
    """고친 계획과, 고친 내용마다 하나씩 만든 PLAN_REPAIRED 경고를 돌려준다. 고칠 것이 없으면 계획을 그대로 돌려준다."""
    notes: list[str] = []
    boundaries = unit_boundary_candidates(plan, datasets)
    boundary = boundaries[0] if len(boundaries) == 1 else None
    filters = _merge_duplicate_filters(plan.filters, notes)
    if boundary is not None and region is not None:
        filters = _drop_region_filters(filters, boundary, region, notes)
    operations = [_aggregate_without_geometry(op, plan, datasets, boundary, notes) for op in plan.operations]
    output = _metric_not_rank(plan.output, operations, notes)
    if plan.intent == AnalysisIntent.REGIONAL_SUMMARY and question:
        operations = _rank_order_of_question(operations, output.metric, question, notes)
    if not notes:
        return plan, []
    repaired = AnalysisPlan.model_validate(
        {
            **plan.model_dump(),
            "filters": [f.model_dump() for f in filters],
            "operations": [op.model_dump() for op in operations],
            "output": output.model_dump(),
        }
    )
    return repaired, [ValidationIssue(code="PLAN_REPAIRED", severity=Severity.WARNING, message=n) for n in notes]


def _merge_duplicate_filters(filters: list[DatasetFilter], notes: list[str]) -> list[DatasetFilter]:
    """같은 Dataset에 나뉜 필터를 AND 하나로 합친다.

    같은 조건의 반복은 하나로 줄이고, 서로 다른 컬럼의 조건과 한 컬럼의 하한·상한 한 쌍(예: 나이 ≥ 0, 나이 ≤ 5)만
    AND로 묶는다. 같은 컬럼의 다른 조건(예: 나이 0세와 5세)이나 OR·NOT 묶음은 'OR'을 뜻했을 수도 있어 고치지 않고
    다시 만들게 둔다.
    """
    grouped: dict[str, list[DatasetFilter]] = {}
    for dataset_filter in filters:
        grouped.setdefault(dataset_filter.dataset, []).append(dataset_filter)
    merged: list[DatasetFilter] = []
    for dataset, group in grouped.items():
        if len(group) == 1 or any(_is_or_not(f.where) for f in group):
            merged.extend(group)
            continue
        conditions: dict[str, FilterCondition] = {}
        for dataset_filter in group:
            for condition in iter_conditions(dataset_filter.where):
                conditions.setdefault(condition.model_dump_json(), condition)
        unique = list(conditions.values())
        if not _each_field_once_or_range(unique):
            merged.extend(group)
            continue
        where = unique[0] if len(unique) == 1 else FilterGroup(logic=LogicalOperator.AND, conditions=unique)
        merged.append(DatasetFilter(dataset=dataset, where=where))
        notes.append(f"'{dataset}'에 나뉘어 있던 필터 {len(group)}개를 하나(AND)로 합침")
    return merged


def _each_field_once_or_range(conditions: list[FilterCondition]) -> bool:
    """컬럼마다 조건이 하나이거나, 한 컬럼에 숫자 하한(>, ≥)·상한(<, ≤) 한 쌍만 있어 범위가 분명한지 본다."""
    by_field: dict[str, list[FilterCondition]] = {}
    for condition in conditions:
        by_field.setdefault(condition.field, []).append(condition)
    for same_field in by_field.values():
        if len(same_field) == 1:
            continue
        lower = [c for c in same_field if c.operator in (FilterOperator.GT, FilterOperator.GTE)]
        upper = [c for c in same_field if c.operator in (FilterOperator.LT, FilterOperator.LTE)]
        if len(same_field) != 2 or len(lower) != 1 or len(upper) != 1:
            return False
        low, high = lower[0].value, upper[0].value
        numbers = all(isinstance(v, int | float) and not isinstance(v, bool) for v in (low, high))
        if not numbers or low > high:
            return False
    return True


def _is_or_not(where: FilterExpression) -> bool:
    return isinstance(where, FilterGroup) and where.logic != LogicalOperator.AND


def _drop_region_filters(
    filters: list[DatasetFilter], boundary: DatasetMetadata, region: RegionRef, notes: list[str]
) -> list[DatasetFilter]:
    """분석 단위 경계에 건 필터가 확정한 지역을 다시 말할 뿐이면(예: 시도명 = 서울특별시) 뺀다. 지역은 region으로 정해진다."""
    kept = []
    for dataset_filter in filters:
        if dataset_filter.dataset == boundary.dataset_id and _restates_region(dataset_filter.where, boundary, region):
            notes.append(f"분석 지역 '{region.name}'을(를) 다시 말한 '{boundary.dataset_id}' 필터를 뺌")
            continue
        kept.append(dataset_filter)
    return kept


def _restates_region(where: FilterExpression, boundary: DatasetMetadata, region: RegionRef) -> bool:
    if _is_or_not(where):
        return False
    # 지역 이름의 각 부분(서울특별시, 강남구)과, 지역 코드와 그 시도·시군구 코드(11, 11680)만 같은 지역으로 본다.
    same_region = set(region.name.split()) | {region.code[:length] for length in (2, 5) if len(region.code) >= length}
    same_region.add(region.code)
    for condition in iter_conditions(where):
        column = boundary.column(condition.field)
        if column is None or column.semantic_type not in _REGION_FIELDS or condition.operator != FilterOperator.EQ:
            return False
        if str(condition.value) not in same_region:
            return False
    return True


def _aggregate_without_geometry(
    op: Operation,
    plan: AnalysisPlan,
    datasets: Mapping[str, DatasetMetadata],
    boundary: DatasetMetadata | None,
    notes: list[str],
) -> Operation:
    """위치가 없는 인구 표를 지역별로 '세려는' 공간 집계를 행정코드별 인구 합계로 바꾼다.

    인구 표의 행은 나이 구간이라 지역별로 셀 대상은 사람 수뿐이다. 인구 컬럼이 없는 표는 행이 무엇인지(시설 하나,
    연도별 통계 등) 알 수 없어 고치지 않고 다시 만들게 둔다.
    """
    if not isinstance(op, AggregateByAreaOperation) or op.aggregation != AggregationFunction.COUNT or boundary is None:
        return op
    source = datasets.get(op.input)
    if source is None or source.geometry_column is not None:
        return op
    population = source.column_with(SemanticType.POPULATION)
    key = _compatible_key(source, boundary, plan)
    if population is None or key is None:
        return op
    notes.append(f"위치가 없는 '{op.input}'의 공간 집계({op.output})를 {key.field}별 {population.name} 합계로 바꿈")
    return AggregateOperation(
        type="aggregate",
        label=op.label,
        input=op.input,
        group_by=[key.field],
        aggregation=AggregationSpec(field=population.name, function=AggregationFunction.SUM),
        output=op.output,
    )


def _compatible_key(source: DatasetMetadata, boundary: DatasetMetadata, plan: AnalysisPlan) -> JoinKey | None:
    """Plan Validator의 결합 키 규칙과 같다: 분석 단위가 같고 의미 유형·코드 체계가 경계의 키와 맞아야 한다."""
    if source.spatial.level != plan.analysis_unit:
        return None
    return next(
        (
            key
            for key in source.join_keys
            if any(
                key.semantic_type == other.semantic_type and key.code_system == other.code_system
                for other in boundary.join_keys
            )
        ),
        None,
    )


def _metric_not_rank(output: OutputSpec, operations: list[Operation], notes: list[str]) -> OutputSpec:
    """지도 지표로 순위 결과를 지정했으면 그 순위를 매긴 지표로 바꾼다."""
    rank = next((op for op in operations if isinstance(op, RankOperation) and op.output == output.metric), None)
    if rank is None:
        return output
    notes.append(f"지도 지표로 지정한 순위 '{output.metric}' 대신 순위를 매긴 지표 '{rank.input}'를 씀")
    return output.model_copy(update={"metric": rank.input})


def _rank_order_of_question(
    operations: list[Operation], metric: str, question: str, notes: list[str]
) -> list[Operation]:
    """지도 지표의 순위 방향이 질문과 반대이면 바꾼다. 많은·적은 쪽을 함께 묻는 질문은 고치지 않는다.

    gpt-4o-mini가 '가장 많은 곳'이나 방향을 말하지 않은 '자치구별 어린이집 수'를 asc로 계획해, 가장 적은 곳을
    1위로 보여 주고 설명이 그곳을 '가장 많다'고 말했다.
    """
    # 기온의 추운 순은 낮은 값부터다. '추운 날 수' 같은 개수 질문에는 적용하지 않는다.
    producer = next((op for op in operations if op.output == metric), None)
    is_average = (
        isinstance(producer, AggregateByAreaOperation) and producer.aggregation == AggregationFunction.AVG
    ) or (isinstance(producer, AggregateOperation) and producer.aggregation.function == AggregationFunction.AVG)
    wanted = question_rank_order(question)
    if is_average and re.search(r"추운|추위|춥", question) and not re.search(r"더운|더위|덥|따뜻", question):
        wanted = SortOrder.ASC
    if wanted is None:
        return operations
    repaired = []
    for op in operations:
        if isinstance(op, RankOperation) and op.input == metric and op.order != wanted:
            side = "많은·높은" if wanted == SortOrder.DESC else "적은·낮은"
            notes.append(f"질문이 {side} 쪽을 물어 '{op.output}' 순위를 {op.order}에서 {wanted}로 바꿈")
            op = op.model_copy(update={"order": wanted})
        repaired.append(op)
    return repaired
