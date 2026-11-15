"""Result Validator: 실행 결과를 지도·설명으로 넘기기 전에 결정론적으로 검증한다."""

import math
from collections.abc import Mapping

from app.domain.catalog import DatasetMetadata
from app.domain.enums import SPATIAL_LEVEL_NAMES, AggregationFunction, SemanticType, Severity, SpatialLevel
from app.domain.operations import AggregateByAreaOperation, AggregateOperation, CalculateRatioOperation
from app.domain.plan import AnalysisPlan, contains_region
from app.domain.results import ExecutionResult, ValidationIssue, ValidationReport

MAX_NULL_RATE = 0.2  # output metric의 null 비율이 이를 넘으면 결합 실패로 본다
UNIT_COUNT_TOLERANCE = 0.1  # 예상 단위 수 대비 허용 오차
BBOX_TOLERANCE = 0.01  # degree
_EXAMPLES = 5
_NULL_REASONS = {
    "denominator_zero": "분모 0",
    "denominator_missing": "분모 자료 없음",
    "numerator_missing": "분자 자료 없음",
    "no_matching_records": "자료 없음",
    "input_null": "입력 값 없음",
}


def validate_result(
    result: ExecutionResult, plan: AnalysisPlan, datasets: Mapping[str, DatasetMetadata]
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    def error(code: str, message: str) -> None:
        issues.append(ValidationIssue(code=code, severity=Severity.ERROR, message=message))

    def warning(code: str, message: str) -> None:
        issues.append(ValidationIssue(code=code, severity=Severity.WARNING, message=message))

    rows = result.rows
    if not rows:
        error("RESULT_EMPTY", "분석 결과가 비어 있습니다")
        return ValidationReport(issues=issues)

    keys = [row.key for row in rows]
    if len(set(keys)) != len(keys):
        error("DUPLICATE_UNITS", "결과에 중복된 분석 단위가 있습니다")
    if result.boundary.invalid_geometry_count or result.boundary.empty_geometry_count:
        error("INVALID_GEOMETRY", "분석 단위 경계에 유효하지 않거나 빈 Geometry가 있습니다")

    for name in _count_like_metrics(plan, datasets):
        negative = [row.name for row in rows if (row.values.get(name) or 0) < 0]
        if negative:
            error("NEGATIVE_VALUE", f"'{name}'에 음수 값이 있습니다: {negative[:_EXAMPLES]}")

    for op in plan.operations:
        if isinstance(op, CalculateRatioOperation):
            unhandled = [
                row.name
                for row in rows
                if row.values.get(op.denominator) in (0, None)
                and (row.values.get(op.output) is not None or op.output not in row.null_reasons)
            ]
            if unhandled:
                error(
                    "ZERO_DENOMINATOR_UNHANDLED",
                    f"'{op.output}': 분모가 0이거나 없는데 값이 있습니다: {unhandled[:_EXAMPLES]}",
                )

    non_finite = [
        f"{row.name}.{name}"
        for row in rows
        for name, value in row.values.items()
        if isinstance(value, float) and not math.isfinite(value)
    ]
    if non_finite:
        error("NON_FINITE_VALUE", f"NaN/Infinity 값이 있습니다: {non_finite[:_EXAMPLES]}")

    metric = plan.output.metric
    missing = [row for row in rows if row.values.get(metric) is None]
    if len(missing) / len(rows) > MAX_NULL_RATE:
        error(
            "HIGH_NULL_RATE",
            f"'{metric}' 값이 없는 단위가 {len(missing)}/{len(rows)}개로 너무 많습니다 (데이터 결합 실패 가능성)",
        )
    elif missing:
        examples = ", ".join(
            f"{row.name}({_NULL_REASONS.get(row.null_reasons.get(metric), '사유 미상')})" for row in missing[:_EXAMPLES]
        )
        warning("NULL_VALUES", f"'{metric}' 값을 계산할 수 없는 행정동 {len(missing)}개는 순위에서 제외: {examples}")

    # 모든 단위의 값이 같으면(예: 경계 행을 세어 전부 1) 비교·순위가 의미 없고, 질문에 맞는 데이터가 없다는 신호다.
    present = [row.values[metric] for row in rows if row.values.get(metric) is not None]
    if len(present) >= 2 and set(present) == {0}:
        # 모두 0이면 계획 실수가 아니라 그 지역에 조건에 맞는 데이터가 없다는 답이다 (예: 직장 어린이집이 없는 구).
        label = next((op.label for op in plan.operations if op.output == metric), metric)
        error(
            "NO_MATCHING_DATA",
            f"'{result.region.name}'의 {SPATIAL_LEVEL_NAMES[result.analysis_unit]} {len(present)}개 모두 "
            f"'{label}' 값이 0입니다. 이 지역에는 조건에 맞는 데이터가 없습니다",
        )
    elif len(present) >= 2 and len(set(present)) == 1:
        label = next((op.label for op in plan.operations if op.output == metric), metric)
        error(
            "CONSTANT_METRIC",
            f"분석 단위 {len(present)}개의 '{label}' 값이 모두 {present[0]:g}입니다. 값이 모두 같아 지역을 비교할 수 "
            "없고, 질문에 맞는 데이터가 없거나 계획이 질문과 맞지 않을 수 있습니다",
        )

    unit_metadata = datasets[result.unit_dataset]
    expected_bbox, actual_bbox = unit_metadata.spatial.bbox, result.boundary.bbox
    if expected_bbox is None or actual_bbox is None:
        warning("REGION_CHECK_SKIPPED", "범위(bbox) 정보가 없어 지역 범위 검증을 건너뜁니다")
    elif not _within(actual_bbox, expected_bbox):
        error("OUT_OF_REGION", f"결과 범위 {actual_bbox}가 '{result.region.name}' 범위 {expected_bbox}를 벗어납니다")

    coverage = unit_metadata.spatial.coverage
    exact_coverage = (
        coverage is not None and coverage.name == result.region.name and coverage.level == result.region.level
    )
    expected_units = unit_metadata.spatial.feature_count if exact_coverage else None
    if expected_units is None:
        reason = (
            "하위 지역 분석이라 Dataset 전체 객체 수와 비교할 수 없어"
            if coverage and not exact_coverage
            else "예상 단위 수 정보가 없어"
        )
        warning("UNIT_COUNT_CHECK_SKIPPED", f"{reason} 단위 수 검증을 건너뜁니다")
    elif abs(len(rows) - expected_units) > expected_units * UNIT_COUNT_TOLERANCE:
        error("UNIT_COUNT_MISMATCH", f"결과 행정동 수 {len(rows)}개가 예상 {expected_units}개와 크게 다릅니다")
    elif len(rows) != expected_units:
        warning("UNIT_COUNT_MISMATCH", f"결과 행정동 수 {len(rows)}개가 예상 {expected_units}개와 다릅니다")

    # 전국 Dataset을 지역 단위로 분석하면 다른 지역 코드는 결합되지 않는 것이 정상이다. 결합 키는 행안부
    # 계층 코드(시도 2·시군구 5·행정동 10자리)이므로, 분석 지역 코드로 시작하는 코드만 결합 실패로 알린다.
    region_prefix = "" if result.region.level == SpatialLevel.COUNTRY else result.region.code
    for name, unmatched in result.unmatched_keys.items():
        inside = [key for key in unmatched if key.startswith(region_prefix)]
        if inside:
            warning(
                "UNMATCHED_KEYS",
                f"'{name}' 계산에 쓰인 코드 {len(inside)}개가 분석 단위 경계에 없어 제외: {inside[:_EXAMPLES]}",
            )
    for log in result.operations:
        unassigned = log.stats.get("unassigned_points", 0)
        if not unassigned:
            continue
        # 점 데이터 범위가 분석 지역보다 넓으면(예: 서울 어린이집으로 강남구 분석) 지역 밖 점은 빠지는 것이 정상이다.
        op = plan.operations[log.index]
        source = datasets.get(op.input) if isinstance(op, AggregateByAreaOperation) else None
        if source is not None and source.spatial.coverage and contains_region(source.spatial.coverage, result.region):
            continue
        warning("UNASSIGNED_POINTS", f"'{log.output}': 분석 지역 경계 밖의 점 {unassigned}개는 집계에서 제외")

    return ValidationReport(issues=issues)


def _count_like_metrics(plan: AnalysisPlan, datasets: Mapping[str, DatasetMetadata]) -> list[str]:
    """개수 또는 인구 합계처럼 음수가 될 수 없는 metric."""
    names = []
    for op in plan.operations:
        if isinstance(op, AggregateByAreaOperation):
            names.append(op.output)
        elif isinstance(op, AggregateOperation):
            column = datasets[op.input].column(op.aggregation.field)
            if op.aggregation.function == AggregationFunction.COUNT or (
                column is not None and column.semantic_type == SemanticType.POPULATION
            ):
                names.append(op.output)
    return names


def _within(inner: list[float], outer: list[float]) -> bool:
    return (
        inner[0] >= outer[0] - BBOX_TOLERANCE
        and inner[1] >= outer[1] - BBOX_TOLERANCE
        and inner[2] <= outer[2] + BBOX_TOLERANCE
        and inner[3] <= outer[3] + BBOX_TOLERANCE
    )
