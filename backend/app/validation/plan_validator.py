"""Plan Validator: LLM이 만든 AnalysisPlan을 실행 전에 결정론적으로 검증한다.

DB 없이 Dataset 메타데이터만으로 판정한다. 오류가 하나라도 있으면 실행하지 않는다.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.domain.catalog import DatasetMetadata, RegionRef
from app.domain.enums import (
    AggregationFunction,
    AnalysisIntent,
    DataType,
    FilterOperator,
    GeometryType,
    LogicalOperator,
    SemanticType,
    Severity,
)
from app.domain.filters import FilterExpression, FilterGroup, iter_conditions
from app.domain.operations import (
    AggregateByAreaOperation,
    AggregateOperation,
    CalculateRatioOperation,
    RankOperation,
)
from app.domain.plan import AnalysisPlan, resolve_region, unit_boundary_candidates
from app.domain.policies import CHILD_AGE_PATTERN, CHILD_AGE_RANGE, CHILD_WORDS, POLICIES
from app.domain.results import RESERVED_PROPERTY_NAMES, ValidationIssue, ValidationReport
from app.gis.crs import CrsError, parse_srid
from app.gis.filter_sql import find_filter_problems

_NUMERIC = {DataType.INTEGER, DataType.FLOAT}
# 지역 조건 컬럼. 지역은 region과 분석 단위 경계가 정하므로 데이터 표에 이런 필터를 두지 않는다.
_REGION_SEMANTICS = {
    SemanticType.ADMINISTRATIVE_CODE,
    SemanticType.ADMINISTRATIVE_NAME,
    SemanticType.SIDO_CODE,
    SemanticType.SIDO_NAME,
    SemanticType.SIGUNGU_CODE,
    SemanticType.SIGUNGU_NAME,
}
# 질문이 값을 말해야 하는 범주 컬럼 (예: 시설 유형 = 국공립)
_CATEGORY_SEMANTICS = {SemanticType.FACILITY_TYPE, SemanticType.FACILITY_NAME, SemanticType.CATEGORY, SemanticType.TEXT}
_VALUE_OPERATORS = {FilterOperator.EQ, FilterOperator.NEQ, FilterOperator.IN, FilterOperator.NOT_IN}
_NULL_OPERATORS = {FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL}


@dataclass(frozen=True)
class _Metric:
    operation: str  # 이 metric을 만든 operation type
    index: int


class _PlanChecker:
    def __init__(
        self,
        plan: AnalysisPlan,
        datasets: Mapping[str, DatasetMetadata],
        selected: Sequence[str],
        region: RegionRef | None = None,
    ):
        self.plan = plan
        self.datasets = datasets
        self.selected = set(selected)
        # 확정된 지역은 행정코드가 있어 상위 범위 Dataset(예: 서울)에 포함되는지 판단할 수 있다.
        self.region = region if region is not None else plan.region
        self.issues: list[ValidationIssue] = []
        self.metrics: dict[str, _Metric] = {}
        self.unit_boundary: DatasetMetadata | None = None

    def error(self, code: str, message: str, path: str | None = None) -> None:
        self.issues.append(ValidationIssue(code=code, severity=Severity.ERROR, message=message, path=path))

    def warning(self, code: str, message: str, path: str | None = None) -> None:
        self.issues.append(ValidationIssue(code=code, severity=Severity.WARNING, message=message, path=path))

    def known(self, dataset_id: str, path: str) -> DatasetMetadata | None:
        """참조한 Dataset이 카탈로그와 plan.datasets에 모두 있는지 확인한다."""
        if dataset_id not in self.datasets:
            self.error("UNKNOWN_DATASET", f"'{dataset_id}'는 등록된 Dataset이 아닙니다", path)
            return None
        if dataset_id not in self.plan.datasets:
            self.error("DATASET_NOT_DECLARED", f"'{dataset_id}'가 plan.datasets에 선언되지 않았습니다", path)
        return self.datasets[dataset_id]

    def run(self) -> list[ValidationIssue]:
        self.check_datasets()
        self.check_filters()
        for index, op in enumerate(self.plan.operations):
            self.check_operation(index, op)
        self.check_output()
        self.check_policy()
        return self.issues

    def check_datasets(self) -> None:
        for i, dataset_id in enumerate(self.plan.datasets):
            path = f"datasets[{i}]"
            if dataset_id not in self.datasets:
                self.error("UNKNOWN_DATASET", f"'{dataset_id}'는 등록된 Dataset이 아닙니다", path)
                continue
            if dataset_id not in self.selected:
                self.error("DATASET_NOT_SELECTED", f"'{dataset_id}'는 선택 단계에서 고른 Dataset이 아닙니다", path)
            metadata = self.datasets[dataset_id]
            coverage = metadata.spatial.coverage
            if resolve_region(self.region, coverage) is None:
                where = f"'{coverage.name}'({coverage.level})" if coverage else "지정되지 않은 지역"
                self.error(
                    "REGION_NOT_COVERED",
                    f"분석 지역 '{self.region.name}'({self.region.level})은 '{dataset_id}'의 범위({where})가 아닙니다",
                    "region",
                )
            if metadata.geometry_column is not None:
                try:
                    parse_srid(metadata.spatial.crs)
                except CrsError as error:
                    self.error("CRS_UNSUPPORTED", f"'{dataset_id}': {error}", path)

        candidates = unit_boundary_candidates(self.plan, self.datasets)
        if not candidates:
            self.error(
                "UNIT_BOUNDARY_MISSING",
                f"분석 단위 '{self.plan.analysis_unit}'의 Polygon 경계 Dataset이 plan.datasets에 없습니다",
                "analysis_unit",
            )
        elif len(candidates) > 1:
            self.error(
                "UNIT_BOUNDARY_AMBIGUOUS",
                f"분석 단위 경계 Dataset이 여러 개입니다: {[d.dataset_id for d in candidates]}",
                "datasets",
            )
        else:
            self.unit_boundary = candidates[0]

        used = {f.dataset for f in self.plan.filters}
        for op in self.plan.operations:
            if isinstance(op, AggregateByAreaOperation):
                used |= {op.input, op.boundary}
            elif isinstance(op, AggregateOperation):
                used.add(op.input)
        for i, dataset_id in enumerate(self.plan.datasets):
            if dataset_id not in used and not (self.unit_boundary and dataset_id == self.unit_boundary.dataset_id):
                self.warning(
                    "UNUSED_DATASET", f"'{dataset_id}'는 어떤 operation에서도 쓰이지 않습니다", f"datasets[{i}]"
                )

    def check_filters(self) -> None:
        seen: set[str] = set()
        for i, dataset_filter in enumerate(self.plan.filters):
            path = f"filters[{i}]"
            metadata = self.known(dataset_filter.dataset, f"{path}.dataset")
            if metadata is None:
                continue
            if dataset_filter.dataset in seen:
                self.error("DUPLICATE_FILTER", f"'{dataset_filter.dataset}'에 필터가 두 번 정의되었습니다", path)
            seen.add(dataset_filter.dataset)
            if self.unit_boundary and dataset_filter.dataset == self.unit_boundary.dataset_id:
                self.error(
                    "BOUNDARY_FILTER_UNSUPPORTED",
                    "분석 단위 경계 Dataset에는 필터를 둘 수 없습니다. 지역 범위는 region으로 지정하세요",
                    path,
                )
            else:
                region_fields = [
                    condition.field
                    for condition in iter_conditions(dataset_filter.where)
                    if (column := metadata.column(condition.field)) is not None
                    and column.semantic_type in _REGION_SEMANTICS
                ]
                if region_fields:
                    self.error(
                        "REGION_FILTER_UNSUPPORTED",
                        f"'{dataset_filter.dataset}'에 지역 조건({', '.join(region_fields)}) 필터를 둘 수 없습니다. "
                        "지역 범위는 region과 분석 단위 경계가 정합니다",
                        path,
                    )
            for problem in find_filter_problems(dataset_filter.where, metadata):
                self.error(problem.code, problem.message, f"{path}.where")

    def check_operation(self, index: int, op) -> None:
        path = f"operations[{index}]"
        if isinstance(op, AggregateByAreaOperation):
            self.check_aggregate_by_area(op, path)
        elif isinstance(op, AggregateOperation):
            self.check_aggregate(op, path)
        elif isinstance(op, CalculateRatioOperation):
            self.check_ratio(op, path)
        elif isinstance(op, RankOperation):
            self.require_metric(op.input, f"{path}.input")

        conflict = (
            op.output in self.metrics
            or op.output in self.datasets
            or op.output in RESERVED_PROPERTY_NAMES
            or (self.unit_boundary is not None and self.unit_boundary.column(op.output) is not None)
        )
        if conflict:
            self.error("OUTPUT_NAME_CONFLICT", f"output 이름 '{op.output}'이 다른 이름과 충돌합니다", f"{path}.output")
        else:
            self.metrics[op.output] = _Metric(operation=op.type, index=index)

    def check_aggregate_by_area(self, op: AggregateByAreaOperation, path: str) -> None:
        points = self.known(op.input, f"{path}.input")
        boundary = self.known(op.boundary, f"{path}.boundary")
        if points is not None:
            if op.aggregation == AggregationFunction.COUNT:
                if op.field is not None:
                    self.error("FIELD_UNEXPECTED", "count는 컬럼이 아니라 점의 개수를 셉니다", f"{path}.field")
            else:
                column = points.column(op.field) if op.field is not None else None
                if column is None:
                    self.error("FIELD_UNKNOWN", f"'{op.input}'에 '{op.field}' 숫자 컬럼이 없습니다", f"{path}.field")
                elif column.data_type not in _NUMERIC:
                    self.error("FIELD_NOT_NUMERIC", f"'{op.field}'은 숫자 컬럼이 아닙니다", f"{path}.field")
                elif column.semantic_type == SemanticType.POPULATION and op.aggregation != AggregationFunction.SUM:
                    self.error("POPULATION_NOT_SUMMED", "인구 수는 합계(sum)로 집계합니다", f"{path}.aggregation")
            if points.geometry_column is None:
                self.error(
                    "GEOMETRY_REQUIRED", f"'{op.input}'에 Geometry가 없어 공간 집계할 수 없습니다", f"{path}.input"
                )
            elif points.spatial.geometry_type != GeometryType.POINT:
                self.error("GEOMETRY_TYPE_INVALID", f"'{op.input}'는 Point Dataset이 아닙니다", f"{path}.input")
        if boundary is not None:
            if not boundary.is_polygon:
                self.error("GEOMETRY_TYPE_INVALID", f"'{op.boundary}'는 Polygon Dataset이 아닙니다", f"{path}.boundary")
            elif self.unit_boundary is not None and op.boundary != self.unit_boundary.dataset_id:
                self.error(
                    "BOUNDARY_NOT_ANALYSIS_UNIT",
                    f"'{op.boundary}'는 분석 단위 경계('{self.unit_boundary.dataset_id}')가 아닙니다",
                    f"{path}.boundary",
                )

    def check_aggregate(self, op: AggregateOperation, path: str) -> None:
        metadata = self.known(op.input, f"{path}.input")
        if metadata is None:
            return
        if len(op.group_by) != 1:
            self.error("JOIN_KEY_INCOMPATIBLE", "group_by는 분석 단위 join key 1개여야 합니다", f"{path}.group_by")
        else:
            self.check_join_key(metadata, op.group_by[0], f"{path}.group_by[0]")
        column = metadata.column(op.aggregation.field)
        if column is None:
            self.error(
                "FIELD_UNKNOWN", f"'{op.input}'에 '{op.aggregation.field}' 컬럼이 없습니다", f"{path}.aggregation"
            )
        elif (
            op.aggregation.function in (AggregationFunction.SUM, AggregationFunction.AVG)
            and column.data_type not in _NUMERIC
        ):
            self.error("FIELD_NOT_NUMERIC", f"'{column.name}'은 숫자 컬럼이 아닙니다", f"{path}.aggregation")
        elif column.semantic_type == SemanticType.POPULATION and op.aggregation.function != AggregationFunction.SUM:
            # 인구 행의 개수나 평균(예: 나이별 인구의 평균)은 지역 인구가 아니어서 다른 지표처럼 보이는 틀린 답이 된다.
            self.error(
                "POPULATION_NOT_SUMMED",
                f"'{column.name}'은 인구 수라 합계(sum)로만 집계합니다. "
                f"{op.aggregation.function}는 행의 개수나 평균일 뿐 지역 인구가 아닙니다",
                f"{path}.aggregation",
            )

    def check_join_key(self, metadata: DatasetMetadata, field: str, path: str) -> None:
        if metadata.column(field) is None:
            self.error("FIELD_UNKNOWN", f"'{metadata.dataset_id}'에 '{field}' 컬럼이 없습니다", path)
            return
        if self.unit_boundary is None:
            return
        key = metadata.join_key(field)
        compatible = (
            key is not None
            and metadata.spatial.level == self.plan.analysis_unit
            and any(
                k.semantic_type == key.semantic_type and k.code_system == key.code_system
                for k in self.unit_boundary.join_keys
            )
        )
        if not compatible:
            self.error(
                "JOIN_KEY_INCOMPATIBLE",
                f"'{metadata.dataset_id}.{field}'는 분석 단위 경계('{self.unit_boundary.dataset_id}')와 결합할 수 있는 키가 아닙니다",
                path,
            )

    def check_ratio(self, op: CalculateRatioOperation, path: str) -> None:
        numerator = self.require_metric(op.numerator, f"{path}.numerator")
        denominator = self.require_metric(op.denominator, f"{path}.denominator")
        if op.numerator == op.denominator:
            self.error("RATIO_OPERAND_INVALID", "분자와 분모가 같습니다", path)
        elif (
            numerator is not None
            and denominator is not None
            and _same_computation(self.plan.operations[numerator.index], self.plan.operations[denominator.index])
        ):
            # 필터는 Dataset마다 하나라, 같은 Dataset을 같은 방식으로 집계한 두 지표는 이름만 달라도 값이 같다.
            self.error(
                "RATIO_OPERAND_INVALID",
                f"'{op.numerator}'와 '{op.denominator}'는 같은 Dataset을 같은 필터·집계로 계산해 값이 같으므로 "
                "비율이 모든 지역에서 같아집니다",
                path,
            )
        for metric, name in ((numerator, "numerator"), (denominator, "denominator")):
            if metric is not None and metric.operation == "rank":
                self.error(
                    "RATIO_OPERAND_INVALID", "순위(rank) 결과는 비율의 피연산자가 될 수 없습니다", f"{path}.{name}"
                )

    def require_metric(self, name: str, path: str) -> _Metric | None:
        metric = self.metrics.get(name)
        if metric is None:
            self.error("OPERATION_INPUT_UNKNOWN", f"'{name}'은 앞에서 계산된 operation output이 아닙니다", path)
        return metric

    def check_output(self) -> None:
        output = self.plan.output
        producer = self.metrics.get(output.metric)
        if producer is None:
            self.error(
                "OUTPUT_METRIC_NOT_COMPUTED",
                f"output.metric '{output.metric}'을 계산하는 operation이 없습니다",
                "output.metric",
            )
        elif producer.operation == "rank":
            ranked = self.plan.operations[producer.index].input
            self.error(
                "OUTPUT_METRIC_IS_RANK",
                f"output.metric '{output.metric}'은 순위(rank) 결과입니다. 지도에는 순위를 매긴 지표 '{ranked}'를 써야 합니다",
                "output.metric",
            )
        elif output.ranking.enabled and not self._rank_of(output.metric):
            self.error("RANKING_NOT_DEFINED", f"'{output.metric}'에 대한 rank operation이 없습니다", "output.ranking")

    def check_policy(self) -> None:
        policy = POLICIES.get(self.plan.intent)
        if policy is None:
            return
        if not policy.enforce_ratio_policy:
            return
        metric = self.plan.output.metric
        ratio = next(
            (op for op in self.plan.operations if isinstance(op, CalculateRatioOperation) and op.output == metric),
            None,
        )
        if ratio is None or ratio.scale != policy.ratio_scale:
            self.error(
                "POLICY_VIOLATION",
                f"'{policy.title}' 정책: output.metric은 scale {policy.ratio_scale:g}인 calculate_ratio 결과여야 합니다",
                "output.metric",
            )
        rank_op = self._rank_of(metric)
        if not self.plan.output.ranking.enabled or rank_op is None or rank_op.order != policy.rank_order:
            self.error(
                "POLICY_VIOLATION",
                f"'{policy.title}' 정책: output.metric에 대해 {policy.rank_order} 순위를 매기고 ranking을 켜야 합니다",
                "output.ranking",
            )

    def _rank_of(self, metric: str) -> RankOperation | None:
        return next((op for op in self.plan.operations if isinstance(op, RankOperation) and op.input == metric), None)


def question_issues(
    plan: AnalysisPlan, datasets: Mapping[str, DatasetMetadata], question: str
) -> list[ValidationIssue]:
    """질문에 근거가 없는 필터. 질문이 있어야 판단할 수 있어 validate_plan과 따로 둔다.

    고치지 않고 오류로 알려 다시 만들게 한다. 필요한 조건을 잘못 지우면 다른 질문의 답이 되기 때문이다.
    """
    issues = []
    text = "".join(question.split())
    words = [word for word in question.split() if len(word) >= 2]
    for i, dataset_filter in enumerate(plan.filters):
        metadata = datasets.get(dataset_filter.dataset)
        if metadata is None:
            continue
        path = f"filters[{i}]"
        for condition in iter_conditions(dataset_filter.where):
            column = metadata.column(condition.field)
            if column is None or column.semantic_type not in _CATEGORY_SEMANTICS:
                continue
            if condition.operator in _NULL_OPERATORS:
                # 값이 있는지 없는지는 질문이 말할 일이 거의 없다 (실제 gpt-4o-mini가 '시설 유형 값 없음'을 붙였다).
                issues.append(
                    ValidationIssue(
                        code="FILTER_NOT_IN_QUESTION",
                        severity=Severity.ERROR,
                        message=f"'{dataset_filter.dataset}.{condition.field}' 값 유무 조건은 질문에 없습니다. "
                        "질문이 묻지 않은 조건은 빼세요",
                        path=f"{path}.where",
                    )
                )
                continue
            if condition.operator not in _VALUE_OPERATORS:
                continue
            values = condition.value if isinstance(condition.value, list) else [condition.value]
            missing = [str(value) for value in values if not _value_in_question(str(value), text, words)]
            if missing:
                issues.append(
                    ValidationIssue(
                        code="FILTER_NOT_IN_QUESTION",
                        severity=Severity.ERROR,
                        message=f"'{dataset_filter.dataset}.{condition.field}' 조건의 값 {missing}은(는) 질문에 없습니다. "
                        "질문이 묻지 않은 조건은 빼세요",
                        path=f"{path}.where",
                    )
                )
        if (
            plan.intent == AnalysisIntent.REGIONAL_SUMMARY
            and _age_range(dataset_filter.where, metadata) == CHILD_AGE_RANGE
            and not _child_in_question(question)
        ):
            issues.append(
                ValidationIssue(
                    code="FILTER_NOT_IN_QUESTION",
                    severity=Severity.ERROR,
                    message=f"질문이 어린이(0~5세)를 말하지 않았는데 '{dataset_filter.dataset}'에 나이 0~5세 조건이 있습니다. "
                    "질문이 묻지 않은 연령 조건은 빼세요",
                    path=f"{path}.where",
                )
            )
    return issues


def _value_in_question(value: str, text: str, words: list[str]) -> bool:
    """값이 질문에 있거나, 질문의 낱말이 값의 일부다 (예: '공립' → '국공립'). 공백은 무시한다."""
    compact = "".join(value.split())
    return bool(compact) and (compact in text or any(word in compact for word in words))


def _age_range(where: FilterExpression, metadata: DatasetMetadata) -> tuple[object, object] | None:
    """나이 컬럼 조건이 나타내는 범위 (between 또는 ≥·≤ 한 쌍). 그 밖의 형태는 None."""
    if isinstance(where, FilterGroup) and where.logic != LogicalOperator.AND:
        return None
    ages = [
        condition
        for condition in iter_conditions(where)
        if (column := metadata.column(condition.field)) is not None and column.semantic_type == SemanticType.AGE
    ]
    if len(ages) == 1 and ages[0].operator == FilterOperator.BETWEEN:
        return tuple(ages[0].value)
    bounds = {condition.operator: condition.value for condition in ages}
    if len(ages) == 2 and FilterOperator.GTE in bounds and FilterOperator.LTE in bounds:
        return (bounds[FilterOperator.GTE], bounds[FilterOperator.LTE])
    return None


def _child_in_question(question: str) -> bool:
    return any(word in question for word in CHILD_WORDS) or re.search(CHILD_AGE_PATTERN, question) is not None


def _same_computation(first: object, second: object) -> bool:
    """같은 입력을 같은 방식으로 집계해 항상 같은 값을 내는 두 operation인지 본다."""
    if isinstance(first, AggregateOperation) and isinstance(second, AggregateOperation):
        return (first.input, first.group_by, first.aggregation) == (second.input, second.group_by, second.aggregation)
    if isinstance(first, AggregateByAreaOperation) and isinstance(second, AggregateByAreaOperation):
        return (first.input, first.boundary, first.aggregation, first.field) == (
            second.input,
            second.boundary,
            second.aggregation,
            second.field,
        )
    return False


def validate_plan(
    plan: AnalysisPlan,
    datasets: Mapping[str, DatasetMetadata],
    selected: Sequence[str],
    region: RegionRef | None = None,
) -> ValidationReport:
    """region은 해석 단계에서 확정한 분석 지역(행정코드 포함)이다. 없으면 계획의 지역 이름으로 판단한다."""
    return ValidationReport(issues=_PlanChecker(plan, datasets, selected, region).run())
