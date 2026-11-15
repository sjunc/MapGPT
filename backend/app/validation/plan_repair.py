"""Plan Repair: LLM 계획에서 반복되는 기계적인 실수를 검증 전에 결정론적으로 고친다.

LLM은 같은 실수(위치 없는 표를 공간 집계, 같은 Dataset에 필터 두 번 등)를 되풀이해 계획을 다시 만들게 하고,
두 번째도 틀리면 분석이 실패한다. 의도가 하나로 정해지는 경우만 고치며, 고친 계획도 Plan Validator를 그대로
통과해야 실행된다. 고친 내용은 PLAN_REPAIRED 경고로 계획 시도 기록에 남긴다.
"""

from collections.abc import Mapping

from app.domain.catalog import DatasetMetadata, JoinKey, RegionRef
from app.domain.enums import AggregationFunction, FilterOperator, LogicalOperator, SemanticType, Severity
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


def repair_plan(
    plan: AnalysisPlan, datasets: Mapping[str, DatasetMetadata], region: RegionRef | None
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
    if not isinstance(op, AggregateByAreaOperation) or boundary is None:
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
