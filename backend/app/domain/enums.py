from enum import StrEnum


class AnalysisIntent(StrEnum):
    SUPPLY_DEMAND_GAP = "supply_demand_gap"
    REGIONAL_SUMMARY = "regional_summary"


class SpatialLevel(StrEnum):
    COUNTRY = "country"
    SIDO = "sido"
    SIGUNGU = "sigungu"
    EMD = "emd"


SPATIAL_LEVEL_NAMES: dict[SpatialLevel, str] = {
    SpatialLevel.COUNTRY: "국가",
    SpatialLevel.SIDO: "시도",
    SpatialLevel.SIGUNGU: "시군구",
    SpatialLevel.EMD: "행정동",
}


class AnalysisScope(StrEnum):
    """분석할 수 있는 지역 범위. 화면 설정에서 고르고 질문마다 함께 보낸다."""

    SEOUL = "seoul"  # 서울특별시와 그 안의 시군구·행정동 (기본)
    NATIONAL = "national"  # 등록된 전국 지역


ANALYSIS_SCOPE_NAMES: dict[AnalysisScope, str] = {AnalysisScope.SEOUL: "서울", AnalysisScope.NATIONAL: "전국"}


class GeometryType(StrEnum):
    POINT = "Point"
    POLYGON = "Polygon"
    MULTIPOLYGON = "MultiPolygon"


class DataType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    GEOMETRY = "geometry"


class SemanticType(StrEnum):
    ADMINISTRATIVE_CODE = "administrative_code"
    ADMINISTRATIVE_NAME = "administrative_name"
    SIDO_CODE = "sido_code"
    SIDO_NAME = "sido_name"
    SIGUNGU_CODE = "sigungu_code"
    SIGUNGU_NAME = "sigungu_name"
    AGE = "age"
    POPULATION = "population"
    FACILITY_ID = "facility_id"
    FACILITY_NAME = "facility_name"
    FACILITY_TYPE = "facility_type"
    LATITUDE = "latitude"
    LONGITUDE = "longitude"
    REFERENCE_DATE = "reference_date"
    GEOMETRY = "geometry"
    IDENTIFIER = "identifier"
    CATEGORY = "category"
    MEASURE = "measure"
    DATE = "date"
    TEXT = "text"


class FilterOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    BETWEEN = "between"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class LogicalOperator(StrEnum):
    AND = "and"
    OR = "or"
    NOT = "not"


class AggregationFunction(StrEnum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class MapType(StrEnum):
    CHOROPLETH = "choropleth"


class MetricKind(StrEnum):
    """지도 지표의 성격. 결과 지도를 어떻게 그릴지 정한다."""

    RATE = "rate"  # 비율 (calculate_ratio)
    AVERAGE = "average"  # 평균 (aggregate avg)
    TOTAL = "total"  # 개수·합계 (aggregate_by_area count, aggregate sum·count)


class MapView(StrEnum):
    """결과 지도를 그리는 방식."""

    CHOROPLETH = "choropleth"  # 단계 구분도: 값 구간별 색으로 지역을 칠한다
    PROPORTIONAL_SYMBOL = "proportional_symbol"  # 비례 원: 값에 비례한 넓이의 원을 지역 안에 그린다
    POINT = "point"  # 점 지도: 지표로 센 시설 하나하나의 위치 (가까운 점은 묶어 개수로 보인다)
    HEATMAP = "heatmap"  # 밀도 지도: 지표로 센 점이 몰린 정도를 연속된 색 번짐으로 보인다


class MapFocus(StrEnum):
    """질문이 지도에서 보려는 것. 해석 LLM이 옮겨 적은 표현을 코드가 확인해 정한다 (domain/maps.py)."""

    LOCATION = "location"  # 위치·분포 ('위치를 보여줘', '어디에 있는지')
    DENSITY = "density"  # 밀집 ('밀집한 곳', '몰려 있는')


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WorkflowStep(StrEnum):
    INTERPRET_QUERY = "interpret_query"
    SELECT_DATASETS = "select_datasets"
    ACQUIRE_DATA = "acquire_data"
    BUILD_ANALYSIS_PLAN = "build_analysis_plan"
    VALIDATE_PLAN = "validate_plan"
    LOAD_DATA = "load_data"
    EXECUTE_OPERATIONS = "execute_operations"
    VALIDATE_RESULT = "validate_result"
    CREATE_MAP_LAYER = "create_map_layer"
    GENERATE_EXPLANATION = "generate_explanation"
