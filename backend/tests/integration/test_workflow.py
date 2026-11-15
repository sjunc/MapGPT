import json
from datetime import date

import pytest

from app.catalog.catalog import sync_catalog_embeddings
from app.catalog.definitions import DAYCARE_FACILITY, POPULATION_BY_AGE
from app.data_sources.ingest import ingest_boundary
from app.domain.enums import AnalysisScope, RunStatus, WorkflowStep
from app.domain.policies import AUTO_DATASET_LIMITATIONS
from app.domain.plan import AnalysisPlan, Region
from app.domain.state import DatasetSelection, InterpretedQuery, RequiredData
from app.llm.base import LLMError, LLMOutputError
from app.llm.embeddings import EmbeddingError
from app.repositories.catalog_repository import upsert_dataset
from app.repositories.run_repository import get_run
from app.gis.tools import point_locations
from app.workflows import analysis_workflow
from app.workflows.analysis_workflow import AnalysisWorkflow
from tests.fakes import FakeEmbedder, daycare_llm, interpreted
from tests.integration.synthetic import UNITS, load_synthetic
from tests.support import QUESTION, load_plan, load_plan_dict

pytestmark = pytest.mark.integration


def test_first_use_case_runs_end_to_end_and_records_provenance(pool, catalog, conn):
    llm = daycare_llm()

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    response = state.final_response
    assert [(item.rank, item.unit_name) for item in response.ranking] == [
        (1, "카동"),
        (2, "아동"),
        (3, "사동"),
        (3, "라동"),
        (3, "마동"),
        (6, "자동"),
        (7, "가동"),
        (7, "차동"),
    ]
    assert response.metric.label == "0~5세 어린이 1,000명당 어린이집 수"
    assert response.metric_labels["child_population"] == "0~5세 인구"
    assert response.rank_metric == "shortage_rank"
    assert response.summary == "카동이 가장 부족합니다."
    assert response.map_layer.feature_count == 10 and response.map_layer.null_count == 2
    # 비율 지표는 단계 구분도이고, 공급 부족(작은 값)을 진하게 칠한다.
    assert (response.map_layer.style.default_view, response.map_layer.style.emphasis) == ("choropleth", "low")
    assert response.map_layer.points is None  # 지도 지표가 점 개수가 아니라 비율이라 위치를 보내지 않는다
    # 비율의 분자(어린이집 수)·분모(0~5세 인구)를 함께 보는 이변량 지도
    assert response.map_layer.style.views == ["choropleth", "bivariate"]
    bivariate = response.map_layer.bivariate
    assert (bivariate.x.metric, bivariate.y.metric) == ("child_population", "daycare_count")
    assert [d.dataset_id for d in response.datasets] == ["emd_boundary", "population_by_age", "daycare_facility"]
    assert response.analysis_method[1] == "필터 — 행정동별 연령별 주민등록 인구(population_by_age): age 0~5"
    assert "'어린이'를 만 0~5세로 해석" in response.assumptions
    assert any("순위에서 제외" in item for item in response.limitations)
    assert any("분석 지역 경계 밖의 점 1개" in item for item in response.limitations)
    assert any(item.startswith("Dataset 기준 시점이 서로 다릅니다") for item in response.limitations)

    stored = get_run(conn, state.run_id)
    provenance = stored["provenance"]
    assert stored["status"] == "succeeded" and stored["result"]["result_id"] == response.result_id
    assert provenance["question"] == QUESTION
    assert provenance["dataset_search"]["query"] == (
        "서울특별시 행정동별 0~5세 인구 1,000명당 어린이집 수가 적은 곳 어린이집 위치 0~5세 인구 행정동 경계"
    )
    assert provenance["dataset_search"]["embedding_model"] == "fake-embedding"
    assert [hit["dataset_id"] for hit in provenance["dataset_search"]["hits"]] == [
        d.dataset_id for d in state.dataset_candidates
    ]
    assert provenance["dataset_selection"]["rationale"] == "경계, 수요(0~5세 인구), 공급(어린이집)"
    assert provenance["analysis_plan"]["output"]["metric"] == "daycare_per_1000_children"
    row = conn.execute(
        "SELECT datasets, crs, jsonb_array_length(operations), result_id::text FROM analysis.run WHERE run_id = %s",
        (state.run_id,),
    ).fetchone()
    assert row[0][1] == {"dataset_id": "population_by_age", "reference_date": "2026-09-30"}
    assert row[1:] == ("EPSG:4326", 4, response.result_id)


def test_invalid_plan_is_regenerated_with_validator_feedback(pool, catalog):
    data = load_plan_dict()
    data["operations"][2]["scale"] = 10000
    llm = daycare_llm(plans=[AnalysisPlan.model_validate(data), load_plan()])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert [attempt.validation.ok for attempt in state.plan_history] == [False, True]
    second_prompt = llm.prompts_for(AnalysisPlan)[1]
    assert "[POLICY_VIOLATION]" in second_prompt and '"scale": 10000' in second_prompt


def test_plan_that_stays_invalid_is_never_executed(pool, catalog, conn):
    data = load_plan_dict()
    data["filters"][0]["where"] = {"field": "ages", "operator": "between", "value": [0, 5]}
    llm = daycare_llm(plans=[AnalysisPlan.model_validate(data)])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.FAILED
    [error] = state.errors
    assert (error.step, error.code) == (WorkflowStep.VALIDATE_PLAN, "PLAN_INVALID")
    assert [issue.code for issue in error.issues] == ["FILTER_FIELD_UNKNOWN"]
    assert state.execution_results is None
    assert get_run(conn, state.run_id)["status"] == "failed"


def _invalid_population_plan(filters: list[dict]) -> AnalysisPlan:
    """인구를 나이별로 묶으려는(JOIN_KEY_INCOMPATIBLE) regional_summary 계획. 계획 보정으로도 고칠 수 없다."""
    data = load_plan_dict()
    data.update(
        intent="regional_summary",
        datasets=["population_by_age", "emd_boundary"],
        filters=filters,
        operations=[
            {
                "type": "aggregate",
                "input": "population_by_age",
                "group_by": ["age"],
                "aggregation": {"field": "population", "function": "sum"},
                "output": "population_total",
                "label": "인구수",
            },
            {
                "type": "rank",
                "input": "population_total",
                "order": "desc",
                "output": "population_rank",
                "label": "인구수 순위",
            },
        ],
        output={"map_type": "choropleth", "metric": "population_total", "ranking": {"enabled": True, "limit": 10}},
    )
    return AnalysisPlan.model_validate(data)


def _population_llm(plan: AnalysisPlan, concept: str):
    return daycare_llm(
        interpreted=[
            interpreted(
                intent="regional_summary",
                restated_question=f"서울특별시에서 {concept}가 많은 행정동",
                key_concepts=[concept],
                assumptions=[],
            )
        ],
        selection=[DatasetSelection(datasets=["population_by_age", "emd_boundary"], rationale="인구와 경계")],
        plans=[plan],
    )


# 경계 Dataset 필터는 지역 조건이라(지역은 region으로 정해진다) 대체 계획이 버려도 답이 바뀌지 않는다.
@pytest.mark.parametrize(
    "filters",
    [[], [{"dataset": "emd_boundary", "where": {"field": "sido_nm", "operator": "eq", "value": "서울특별시"}}]],
)
def test_invalid_regional_summary_uses_validated_metadata_fallback(pool, catalog, filters):
    llm = _population_llm(_invalid_population_plan(filters), "인구수")

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("서울에서 인구가 많은 행정동을 알려줘")

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert [attempt.validation.ok for attempt in state.plan_history] == [False, False, True]
    assert state.analysis_plan.operations[0].type == "aggregate"
    assert state.analysis_plan.operations[0].aggregation.field == "population"
    assert state.final_response.metric.label == "인구수"
    assert any("최소 집계 계획" in assumption for assumption in state.final_response.assumptions)
    # 인구 합계는 총량이라 비례 원이 기본이고, 단위마다 원을 그릴 대표점이 있다.
    layer = state.final_response.map_layer
    assert (layer.style.default_view, layer.style.emphasis) == ("proportional_symbol", "high")
    assert all(len(feature["properties"]["label_point"]) == 2 for feature in layer.geojson["features"])


def test_fallback_is_not_used_when_it_would_drop_a_data_condition(pool, catalog):
    # 대체 계획은 필터 없이 집계하므로 0~5세 조건이 있던 질문에 전체 인구로 답하게 된다. 틀린 답 대신 실패로 끝낸다.
    age = {"dataset": "population_by_age", "where": {"field": "age", "operator": "between", "value": [0, 5]}}
    llm = _population_llm(_invalid_population_plan([age]), "0~5세 인구")

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("서울에서 0~5세 인구가 가장 많은 행정동은?")

    assert state.status == RunStatus.FAILED
    assert state.errors[0].code == "PLAN_INVALID"
    assert [attempt.validation.ok for attempt in state.plan_history] == [False, False]


def test_mechanical_plan_mistakes_are_repaired_without_regenerating(pool, catalog):
    # gpt-4o-mini가 자주 낸 실수 두 가지: 같은 필터를 두 번 걸고, 위치가 없는 인구 표를 공간 집계했다.
    data = load_plan_dict()
    data["filters"].append(data["filters"][0])
    data["operations"][1] = {
        "type": "aggregate_by_area",
        "input": "population_by_age",
        "boundary": "emd_boundary",
        "aggregation": "count",
        "output": "child_population",
        "label": "0~5세 인구",
    }
    llm = daycare_llm(plans=[AnalysisPlan.model_validate(data)])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert len(llm.prompts_for(AnalysisPlan)) == 1  # 다시 만들지 않았다
    [attempt] = state.plan_history
    assert [issue.code for issue in attempt.validation.warnings] == ["PLAN_REPAIRED", "PLAN_REPAIRED"]
    assert attempt.plan.operations[1].type == "aggregate"
    assert attempt.original_plan.operations[1].type == "aggregate_by_area"  # LLM이 낸 원래 계획도 남는다
    assert state.final_response.ranking[0].unit_name == "카동"


def test_filter_the_question_did_not_ask_for_is_sent_back_for_regeneration(pool, catalog):
    # 실제 gpt-4o-mini 계획: 국공립을 묻지 않은 질문에 '시설 유형 = 국공립'을 붙여 국공립만 셌다.
    data = load_plan_dict()
    data["filters"].append(
        {"dataset": "daycare_facility", "where": {"field": "facility_type", "operator": "eq", "value": "국공립"}}
    )
    llm = daycare_llm(plans=[AnalysisPlan.model_validate(data), load_plan()])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert [attempt.validation.ok for attempt in state.plan_history] == [False, True]
    assert "[FILTER_NOT_IN_QUESTION]" in llm.prompts_for(AnalysisPlan)[1]
    assert [f.dataset for f in state.analysis_plan.filters] == ["population_by_age"]


def test_plan_schema_failure_counts_as_invalid_plan(pool, catalog):
    llm = daycare_llm(plans=[LLMOutputError("AnalysisPlan 스키마 불일치"), load_plan()])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.SUCCEEDED
    assert [attempt.plan is None for attempt in state.plan_history] == [True, False]
    assert "[PLAN_SCHEMA_INVALID]" in llm.prompts_for(AnalysisPlan)[1]


def test_plan_that_changes_the_interpretation_is_rejected(pool, catalog):
    llm = daycare_llm(plans=[load_plan(analysis_unit="sigungu")])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.errors[0].code == "PLAN_INVALID"
    assert "PLAN_INTERPRETATION_MISMATCH" in [issue.code for issue in state.errors[0].issues]


@pytest.mark.parametrize(
    ("interpretation", "code"),
    [
        (
            InterpretedQuery.model_validate(
                {
                    "intent": None,
                    "unsupported_reason": "지하철역 거리 분석은 아직 지원하지 않습니다",
                    "region": None,
                    "analysis_unit": None,
                    "restated_question": "지하철역 500m 밖 지역",
                    "key_concepts": ["지하철역"],
                    "assumptions": [],
                }
            ),
            "UNSUPPORTED_QUESTION",
        ),
        (interpreted(region="부산광역시", region_phrase="부산"), "REGION_NOT_SUPPORTED"),
        (interpreted(analysis_unit="sigungu", analysis_unit_phrase="구"), "UNIT_NOT_SUPPORTED"),
        (LLMError("OpenAI 호출 실패: 401"), "LLM_FAILED"),
    ],
)
def test_interpretation_failures_stop_before_planning(pool, catalog, conn, interpretation, code):
    llm = daycare_llm(interpreted=[interpretation])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("부산 지하철역에서 500m 이상 떨어진 구를 찾아줘")

    assert state.status == RunStatus.FAILED
    assert (state.errors[0].step, state.errors[0].code) == (WorkflowStep.INTERPRET_QUERY, code)
    assert llm.prompts_for(AnalysisPlan) == []
    assert get_run(conn, state.run_id)["status"] == "failed"


def test_unsupported_interpretation_is_retried_before_stopping(pool, catalog):
    unsupported = InterpretedQuery.model_validate(
        {
            "intent": None,
            "unsupported_reason": "분석 유형이 지원되지 않음",
            "region": None,
            "analysis_unit": None,
            "restated_question": "서울에서 어린이집이 부족한 곳",
            "key_concepts": [],
            "assumptions": [],
        }
    )
    llm = daycare_llm(interpreted=[unsupported, interpreted()])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    prompts = llm.prompts_for(InterpretedQuery)
    assert len(prompts) == 2
    assert "[재판정 요청]" in prompts[1]
    assert "분석 유형이 지원되지 않음" in prompts[1]


def test_incomplete_interpretation_is_retried(pool, catalog):
    incomplete = interpreted().model_copy(update={"region": None, "analysis_unit": None})
    llm = daycare_llm(interpreted=[incomplete, interpreted()])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    prompts = llm.prompts_for(InterpretedQuery)
    assert len(prompts) == 2
    assert "분석 유형·지역·분석 단위 중 일부를 해석하지 못함" in prompts[1]


def test_interpretation_prompt_lists_analysis_units_per_region(pool, catalog):
    llm = daycare_llm()

    AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    system = next(system for output_type, system, _ in llm.calls if output_type is InterpretedQuery)
    assert "- 서울특별시 (sido): emd(행정동)" in system
    assert "region.level은 그 지역 자체의 행정 단위다 (예: 서울특별시 → sido)" in system


def test_region_with_wrong_level_fails_showing_both_levels(pool, catalog):
    # 실제 gpt-4o-mini 해석: 분석 단위(emd)를 지역 자체의 단위로 씀
    wrong_level = interpreted().model_copy(update={"region": Region(name="서울특별시", level="emd")})
    llm = daycare_llm(interpreted=[wrong_level])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert (state.errors[0].step, state.errors[0].code) == (WorkflowStep.INTERPRET_QUERY, "REGION_NOT_SUPPORTED")
    assert state.errors[0].message == "'서울특별시'(emd) 데이터가 없습니다. 분석 가능한 지역: 서울특별시(sido)"


def test_unit_without_boundary_data_fails_with_available_units(pool, catalog):
    llm = daycare_llm(interpreted=[interpreted(analysis_unit="sigungu", analysis_unit_phrase="구별")])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("서울특별시 구별 어린이집 공급 부족을 알려줘")

    assert state.errors[0].message == (
        "'서울특별시'에는 시군구(sigungu) 단위 경계 데이터가 없습니다. 분석 가능한 단위: 행정동(emd)"
    )


def test_unspecified_unit_uses_finest_available_boundary(pool, catalog):
    llm = daycare_llm(interpreted=[interpreted(analysis_unit="sigungu", analysis_unit_phrase=None)])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("서울에서 어린이집이 부족한 곳을 알려줘")

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert state.interpreted_query.analysis_unit == "emd"
    assert state.analysis_plan.analysis_unit == "emd"


def test_dataset_selection_adds_boundary_matching_analysis_unit(pool, catalog):
    llm = daycare_llm(
        selection=[
            DatasetSelection(
                datasets=["population_by_age", "daycare_facility"],
                rationale="수요와 공급 데이터만 선택",
            )
        ]
    )

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert state.selected_datasets == ["population_by_age", "daycare_facility", "emd_boundary"]


class FailingEmbedder(FakeEmbedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("OpenAI 임베딩 호출 실패: timeout")


@pytest.mark.parametrize(
    ("embedder", "code"),
    [(FakeEmbedder(model="other-model"), "CATALOG_INDEX_STALE"), (FailingEmbedder(), "EMBEDDING_FAILED")],
)
def test_dataset_search_failures_stop_before_selection(pool, catalog, embedder, code):
    llm = daycare_llm()

    state = AnalysisWorkflow(llm, pool, embedder).run(QUESTION)

    assert (state.errors[0].step, state.errors[0].code) == (WorkflowStep.SELECT_DATASETS, code)
    assert state.dataset_search is None and llm.prompts_for(DatasetSelection) == []


def test_invalid_dataset_selection_is_recorded_before_stopping(pool, catalog, conn):
    selection = DatasetSelection(datasets=["emd_boundary", "subway_station"], rationale="지하철역 거리를 계산")
    llm = daycare_llm(selection=[selection])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)

    assert (state.errors[0].step, state.errors[0].code) == (WorkflowStep.SELECT_DATASETS, "INVALID_DATASET_SELECTION")
    assert "분석할 수 있는 관련 데이터: " in state.errors[0].message
    assert llm.prompts_for(AnalysisPlan) == []
    stored = get_run(conn, state.run_id)["provenance"]["dataset_selection"]
    assert stored == {
        "required_data": [],
        "datasets": ["emd_boundary", "subway_station"],
        "rationale": "지하철역 거리를 계산",
    }


@pytest.mark.parametrize(
    ("source", "datasets"),
    [
        (None, ["emd_boundary", "population_by_age"]),  # 후보에 없다고 답함
        ("pharmacy_facility", ["emd_boundary", "population_by_age", "pharmacy_facility"]),  # 없는 ID를 지어냄
    ],
)
def test_missing_data_stops_with_related_data_instead_of_a_substitute(pool, catalog, conn, source, datasets):
    needs = [
        RequiredData(concept="약국", dataset_id=source),
        RequiredData(concept="인구", dataset_id="population_by_age"),
    ]
    llm = daycare_llm(
        interpreted=[
            interpreted(
                restated_question="서울특별시 행정동별 인구 1,000명당 약국 수가 적은 곳",
                key_concepts=["약국 위치", "인구", "행정동 경계"],
                assumptions=[],
            )
        ],
        selection=[
            DatasetSelection(required_data=needs, datasets=datasets, rationale="약국 위치 데이터가 후보에 없다")
        ],
    )

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("서울에서 인구 대비 약국이 부족한 행정동은?")

    [error] = state.errors
    assert (error.step, error.code) == (WorkflowStep.SELECT_DATASETS, "DATA_NOT_AVAILABLE")
    assert error.message.startswith("'약국' 데이터가 없어 분석할 수 없습니다. 분석할 수 있는 관련 데이터: ")
    assert "서울특별시 어린이집 위치(서울특별시 범위)" in error.message
    assert "행정동별 연령별 주민등록 인구(대한민국 범위)" in error.message
    assert "경계" not in error.message  # 분석 단위 경계는 안내하지 않는다
    assert state.selected_datasets == [] and llm.prompts_for(AnalysisPlan) == []
    stored = get_run(conn, state.run_id)["provenance"]["dataset_selection"]["required_data"]
    assert stored == [{"concept": "약국", "dataset_id": source}, {"concept": "인구", "dataset_id": "population_by_age"}]


def test_result_validation_failure_blocks_map_and_explanation(pool, catalog, conn):
    conn.execute(
        "UPDATE catalog.dataset SET metadata = jsonb_set(metadata, '{spatial,feature_count}', '50') "
        "WHERE dataset_id = 'emd_boundary'"
    )
    conn.commit()

    state = AnalysisWorkflow(daycare_llm(), pool, FakeEmbedder()).run(QUESTION)

    assert (state.errors[0].step, state.errors[0].code) == (WorkflowStep.VALIDATE_RESULT, "RESULT_INVALID")
    assert [issue.code for issue in state.errors[0].issues] == ["UNIT_COUNT_MISMATCH"]
    assert state.map_layer is None and state.final_response is None


def test_condition_without_matching_data_says_so_with_actual_values(pool, catalog):
    # 합성 어린이집은 모두 민간이다. 없는 유형을 물으면 실패가 아니라 '없다'고 답하고, 조건을 고칠 수 있게 실제 값을 보여 준다.
    data = load_plan_dict()
    data["filters"].append(
        {"dataset": "daycare_facility", "where": {"field": "facility_type", "operator": "eq", "value": "협동"}}
    )
    llm = daycare_llm(plans=[AnalysisPlan.model_validate(data)])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(
        "서울에서 어린이 인구 대비 협동 어린이집이 부족한 행정동을 알려줘."
    )

    assert state.status == RunStatus.FAILED
    [error] = state.errors
    assert (error.step, error.code) == (WorkflowStep.LOAD_DATA, "NO_MATCHING_DATA")
    assert error.message == (
        "'facility_type = 협동' 조건에 맞는 '서울특별시 어린이집 위치' 데이터가 없습니다. 'facility_type'의 실제 값: 민간"
    )


# 시군구 이름이 겹치는 두 시도(서울·부산 '중구')의 전국 경계: (adm_cd, 행정동, 시도, 시도명, 시군구, 시군구명, 범위)
NATIONAL_UNITS = [
    ("1114052000", "을지로동", "11", "서울특별시", "11140", "중구", (126.9, 37.5, 127.0, 37.6)),
    ("2611053000", "중앙동", "26", "부산광역시", "26110", "중구", (129.0, 35.1, 129.1, 35.2)),
    ("2611054000", "광복동", "26", "부산광역시", "26110", "중구", (129.1, 35.1, 129.2, 35.2)),
    ("2614051000", "동대신1동", "26", "부산광역시", "26140", "서구", (129.0, 35.0, 129.1, 35.1)),
]
NATIONAL_POPULATION = {"1114052000": 100, "2611053000": 300, "2611054000": 200, "2614051000": 400}


def _ingest_units(conn, tmp_path, units) -> None:
    """(adm_cd, 행정동, 시도, 시도명, 시군구, 시군구명, 범위) 단위를 admdongkor 형식 전국 경계로 적재한다."""
    features = [
        {
            "type": "Feature",
            "properties": {
                "adm_nm": f"{sido_nm} {sgg_nm} {adm_nm}",
                "adm_cd2": adm_cd,
                "sido": sido_cd,
                "sidonm": sido_nm,
                "sgg": sgg_cd,
                "sggnm": sgg_nm,
            },
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]]},
        }
        for adm_cd, adm_nm, sido_cd, sido_nm, sgg_cd, sgg_nm, (x0, y0, x1, y1) in units
    ]
    path = tmp_path / "HangJeongDong_ver20260701.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    ingest_boundary(conn, path)


@pytest.fixture
def national_catalog(conn, tmp_path):
    """전국 경계(admdongkor 형식)와 인구를 적재하고 검색 임베딩을 맞춘다."""
    _ingest_units(conn, tmp_path, NATIONAL_UNITS)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO datasets.population_by_age VALUES (%s, 30, %s, '2026-09-30')",
            list(NATIONAL_POPULATION.items()),
        )
    temporal = POPULATION_BY_AGE.temporal.model_copy(update={"reference_date": date(2026, 9, 30)})
    upsert_dataset(conn, POPULATION_BY_AGE.model_copy(update={"temporal": temporal}))
    conn.commit()
    sync_catalog_embeddings(conn, FakeEmbedder())


def _population_plan(region: str) -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "intent": "regional_summary",
            "region": {"name": region, "level": "sigungu"},
            "analysis_unit": "emd",
            "datasets": ["population_by_age", "national_emd_boundary"],
            "filters": [],
            "operations": [
                {
                    "type": "aggregate",
                    "input": "population_by_age",
                    "group_by": ["adm_cd"],
                    "aggregation": {"field": "population", "function": "sum"},
                    "output": "population_total",
                    "label": "인구수",
                },
                {
                    "type": "rank",
                    "input": "population_total",
                    "order": "desc",
                    "output": "population_rank",
                    "label": "인구수 순위",
                },
            ],
            "output": {
                "map_type": "choropleth",
                "metric": "population_total",
                "ranking": {"enabled": True, "limit": 10},
            },
        }
    )


def _regional(region: str, phrase: str) -> InterpretedQuery:
    return interpreted(
        intent="regional_summary",
        region_phrase=phrase,
        restated_question=f"{region} 행정동별 인구 순위",
        key_concepts=["인구수", "행정동 경계"],
        assumptions=[],
    ).model_copy(update={"region": Region(name=region, level="sigungu")})


def test_sigungu_sharing_a_name_is_resolved_with_its_province(pool, national_catalog):
    # 실제 qwen3.5:9b 해석: 시도와 시군구를 붙여 씀
    llm = daycare_llm(
        interpreted=[_regional("부산광역시중구", "부산 중구")],
        selection=[DatasetSelection(datasets=["population_by_age", "national_emd_boundary"], rationale="인구와 경계")],
        plans=[_population_plan("부산광역시 중구")],
    )

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("부산 중구 행정동별 인구 순위", AnalysisScope.NATIONAL)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert (state.resolved_region.name, state.resolved_region.code) == ("부산광역시 중구", "26110")
    assert [(item.rank, item.unit_name) for item in state.final_response.ranking] == [(1, "중앙동"), (2, "광복동")]
    # 다른 시도·시군구 인구 코드는 지역 밖이라 빠지는 것이 정상이므로 결합 실패로 알리지 않는다.
    assert not any("분석 단위 경계에 없어 제외" in note for note in state.final_response.limitations)
    # 자동 발견 Dataset을 쓰지 않았으므로 그 한계는 붙이지 않는다.
    assert not set(AUTO_DATASET_LIMITATIONS) & set(state.final_response.limitations)
    system = next(system for output_type, system, _ in llm.calls if output_type is InterpretedQuery)
    assert "- 서울특별시 중구 (sigungu): sigungu(시군구), emd(행정동)" in system
    assert "- 부산광역시 중구 (sigungu): sigungu(시군구), emd(행정동)" in system


def test_sigungu_name_shared_by_provinces_stops_with_candidates(pool, national_catalog):
    llm = daycare_llm(interpreted=[_regional("중구", "중구")])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("중구 행정동별 인구 순위", AnalysisScope.NATIONAL)

    assert (state.errors[0].step, state.errors[0].code) == (WorkflowStep.INTERPRET_QUERY, "REGION_AMBIGUOUS")
    assert "서울특별시 중구, 부산광역시 중구" in state.errors[0].message
    assert llm.prompts_for(AnalysisPlan) == []


# 아래 범위 시험은 해석 단계만 본다. 선택 단계에서 없는 Dataset을 골라 계획 전에 멈추게 한다.
_STOP_AFTER_INTERPRETATION = [DatasetSelection(datasets=["subway_station"], rationale="해석 단계만 확인")]


def test_seoul_scope_resolves_a_shared_name_inside_seoul(pool, national_catalog):
    # 서울 범위의 해석에는 서울 지역만 보여 주므로 '중구'가 서울 중구 하나로 정해진다.
    llm = daycare_llm(interpreted=[_regional("중구", "중구")], selection=_STOP_AFTER_INTERPRETATION)

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("중구 행정동별 인구 순위")

    assert state.scope == AnalysisScope.SEOUL
    assert state.resolved_region.name == "서울특별시 중구"
    system = next(system for output_type, system, _ in llm.calls if output_type is InterpretedQuery)
    assert "- 서울특별시 중구 (sigungu)" in system
    assert "- 부산광역시 중구 (sigungu)" not in system  # 지역 목록에 서울 밖 지역이 없다


def test_region_outside_seoul_scope_asks_to_switch_scope(pool, national_catalog):
    # 실제 gpt-4o-mini는 지역을 줄여 썼다('부산 중구'). 안내에는 등록된 공식 이름을 쓴다.
    llm = daycare_llm(interpreted=[_regional("부산 중구", "부산 중구")])

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("부산 중구 행정동별 인구 순위")

    [error] = state.errors
    assert (error.step, error.code) == (WorkflowStep.INTERPRET_QUERY, "REGION_OUT_OF_SCOPE")
    assert error.message == (
        "'부산광역시 중구'은(는) 현재 분석 범위(서울) 밖입니다. 설정에서 분석 범위를 전국으로 바꿔 주세요"
    )


@pytest.mark.parametrize(
    ("scope", "region"), [(AnalysisScope.SEOUL, "서울특별시"), (AnalysisScope.NATIONAL, "대한민국")]
)
def test_question_without_region_uses_the_scope_region(pool, national_catalog, scope, region):
    # 실제 gpt-4o-mini: 지역이 없는 질문을 '대한민국'(emd)처럼 채웠다. 질문에 근거가 없으면 분석 범위의 기준 지역을 쓴다.
    filled = interpreted(intent="regional_summary", region_phrase=None).model_copy(
        update={"region": Region(name="대한민국", level="emd")}
    )
    llm = daycare_llm(interpreted=[filled], selection=_STOP_AFTER_INTERPRETATION)

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("어린이 인구 대비 어린이집이 부족한 행정동을 알려줘", scope)

    assert state.resolved_region.name == region
    assert f"질문에 지역이 없어 분석 범위의 기준 지역인 {region} 전체를 분석" in state.interpreted_query.assumptions


def test_dataset_that_does_not_cover_the_region_stops_before_planning(pool, national_catalog, conn):
    # 서울 범위 어린이집으로 부산 중구를 분석하는 계획은 다시 만들어도 검증을 통과할 수 없다.
    upsert_dataset(conn, DAYCARE_FACILITY)
    conn.commit()
    sync_catalog_embeddings(conn, FakeEmbedder())
    region = Region(name="부산광역시 중구", level="sigungu")
    llm = daycare_llm(
        interpreted=[interpreted(region_phrase="부산 중구").model_copy(update={"region": region})],
        selection=[
            DatasetSelection(
                datasets=["national_emd_boundary", "population_by_age", "daycare_facility"],
                rationale="경계, 수요(0~5세 인구), 공급(어린이집)",
            )
        ],
    )

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(
        "부산 중구에서 어린이 인구 대비 어린이집이 부족한 행정동", AnalysisScope.NATIONAL
    )

    [error] = state.errors
    assert (error.step, error.code) == (WorkflowStep.SELECT_DATASETS, "DATA_NOT_AVAILABLE")
    assert (
        error.message
        == "서울특별시 어린이집 위치(서울특별시 범위)에는 '부산광역시 중구' 지역이 없어 분석할 수 없습니다"
    )
    assert llm.prompts_for(AnalysisPlan) == []


@pytest.mark.parametrize(("phrase", "expected"), [("자치구", "sigungu"), (None, "emd"), ("구별", "emd")])
def test_unit_is_replaced_only_when_the_question_did_not_specify_it(pool, national_catalog, phrase, expected):
    # '구별' 같은 정해진 낱말이 없어도('자치구 중') LLM이 옮겨 적은 단위 표현이 질문에 있으면 그 단위를 그대로 쓴다.
    # 질문에 없는 표현('구별')은 단위를 말한 근거가 아니다.
    llm = daycare_llm(
        interpreted=[interpreted(intent="regional_summary", analysis_unit="sigungu", analysis_unit_phrase=phrase)],
        selection=[DatasetSelection(datasets=["subway_station"], rationale="해석 단계만 확인")],
    )

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("서울 자치구 중 인구가 가장 많은 곳은?")

    assert state.interpreted_query.analysis_unit == expected


def test_auto_discovered_dataset_limitations_appear_when_one_is_used(pool, catalog, conn):
    conn.execute(
        "UPDATE catalog.dataset SET metadata = jsonb_set(metadata, '{category}', '\"auto_discovered\"') "
        "WHERE dataset_id = 'daycare_facility'"
    )
    conn.commit()
    sync_catalog_embeddings(conn, FakeEmbedder())

    state = AnalysisWorkflow(daycare_llm(), pool, FakeEmbedder()).run(QUESTION)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert set(AUTO_DATASET_LIMITATIONS) <= set(state.final_response.limitations)


@pytest.fixture
def sigungu_catalog(conn, tmp_path):
    """합성 단위를 전국 경계로 적재하고 서울 범위 어린이집·인구를 넣는다 (시군구를 분석 지역으로 잡는 질문용)."""
    _ingest_units(conn, tmp_path, UNITS)
    load_synthetic(conn, boundary=False)
    # 서울 범위 Dataset으로 하위 지역을 분석하는지 보려고, 전국 범위 행정동 경계는 후보에서 뺀다.
    conn.execute("DELETE FROM catalog.dataset WHERE dataset_id = 'national_emd_boundary'")
    for dataset, reference_date in ((POPULATION_BY_AGE, date(2026, 9, 30)), (DAYCARE_FACILITY, date(2026, 10, 7))):
        temporal = dataset.temporal.model_copy(update={"reference_date": reference_date})
        upsert_dataset(conn, dataset.model_copy(update={"temporal": temporal}))
    conn.commit()
    sync_catalog_embeddings(conn, FakeEmbedder())


def test_sub_region_uses_seoul_datasets_that_cover_it(pool, sigungu_catalog):
    # 가가구(11110)는 서울(11) 범위의 행정동 경계·어린이집 안에 있다.
    region = Region(name="서울특별시 가가구", level="sigungu")
    llm = daycare_llm(
        interpreted=[interpreted().model_copy(update={"region": region})],
        plans=[load_plan(region=region.model_dump(mode="json"))],
    )

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run("서울 가가구에서 어린이 인구 대비 어린이집이 부족한 행정동")

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert state.resolved_region.code == "11110"
    ranking = [(item.rank, item.unit_name) for item in state.final_response.ranking]
    assert ranking == [(1, "아동"), (2, "사동"), (3, "자동"), (4, "가동")]
    # 서울 범위 어린이집으로 가가구를 분석하면 다른 구의 점은 빠지는 것이 정상이라 경고하지 않는다.
    assert not any("경계 밖의 점" in note for note in state.final_response.limitations)


def test_region_without_matching_facilities_says_no_data(pool, sigungu_catalog, conn):
    # 직장 어린이집은 나나구 라동에만 있다. 가가구를 물으면 모든 행정동이 0이므로 비교할 수 없다는 오류 대신 '없다'고 답한다.
    conn.execute("UPDATE datasets.daycare_facility SET facility_type = '직장' WHERE facility_id = 'e1'")
    conn.commit()
    region = Region(name="서울특별시 가가구", level="sigungu")
    data = load_plan_dict()
    data["region"] = region.model_dump(mode="json")
    data["filters"].append(
        {"dataset": "daycare_facility", "where": {"field": "facility_type", "operator": "eq", "value": "직장"}}
    )
    llm = daycare_llm(
        interpreted=[interpreted().model_copy(update={"region": region})],
        plans=[AnalysisPlan.model_validate(data)],
    )

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(
        "서울 가가구에서 어린이 인구 대비 직장 어린이집이 부족한 행정동"
    )

    [error] = state.errors
    assert (error.step, error.code) == (WorkflowStep.VALIDATE_RESULT, "NO_MATCHING_DATA")
    # 나동은 어린이가 0명이라 값이 없고(분모 0), 나머지 4개 행정동이 모두 0이다.
    assert error.message == (
        "'서울특별시 가가구'의 행정동 4개 모두 '0~5세 어린이 1,000명당 어린이집 수' 값이 0입니다. "
        "이 지역에는 조건에 맞는 데이터가 없습니다"
    )
    assert error.issues == []  # 같은 문구를 issues에 다시 싣지 않는다
    assert state.map_layer is None and state.final_response is None


def _daycare_count_llm(map_focus_phrase: str | None):
    plan = AnalysisPlan.model_validate(
        {
            "intent": "regional_summary",
            "region": {"name": "서울특별시", "level": "sido"},
            "analysis_unit": "emd",
            "datasets": ["daycare_facility", "emd_boundary"],
            "operations": [
                {
                    "type": "aggregate_by_area",
                    "input": "daycare_facility",
                    "boundary": "emd_boundary",
                    "aggregation": "count",
                    "output": "daycare_count",
                    "label": "어린이집 수",
                },
                {
                    "type": "rank",
                    "input": "daycare_count",
                    "order": "desc",
                    "output": "daycare_rank",
                    "label": "어린이집 수 순위",
                },
            ],
            "output": {"map_type": "choropleth", "metric": "daycare_count", "ranking": {"enabled": True, "limit": 10}},
        }
    )
    return daycare_llm(
        interpreted=[
            interpreted(
                intent="regional_summary",
                map_focus_phrase=map_focus_phrase,
                restated_question="서울특별시 행정동별 어린이집 수와 위치",
                key_concepts=["어린이집 위치", "행정동 경계"],
                assumptions=[],
            )
        ],
        selection=[DatasetSelection(datasets=["daycare_facility", "emd_boundary"], rationale="어린이집과 경계")],
        plans=[plan],
    )


@pytest.mark.parametrize(
    ("question", "phrase", "view"),
    [
        ("서울 행정동 어린이집 위치를 보여줘", "위치", "point"),
        ("서울에서 어린이집이 밀집한 행정동은?", "밀집한", "heatmap"),
        ("서울 행정동별 어린이집 수", None, "proportional_symbol"),
        # 해석 LLM이 순위 표현을 옮겨 적어도 위치·밀집을 뜻하지 않으면 기본 보기를 바꾸지 않는다.
        ("서울에서 어린이집이 가장 많은 행정동은?", "가장 많은 행정동", "proportional_symbol"),
    ],
)
def test_point_count_map_sends_locations_and_follows_the_question_focus(pool, catalog, question, phrase, view):
    state = AnalysisWorkflow(_daycare_count_llm(phrase), pool, FakeEmbedder()).run(question)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    layer = state.final_response.map_layer
    assert layer.style.default_view == view
    assert layer.style.views == ["proportional_symbol", "choropleth", "point", "heatmap", "hexbin"]
    # 지도 지표로 센 어린이집(서울 안 14곳)의 위치와 육각 격자를 함께 보낸다.
    assert layer.points.count == len(layer.points.geojson["features"]) == 14
    assert layer.hexbins.count == 14
    assert layer.bivariate is None
    assert state.interpreted_query.map_focus_phrase == phrase


def test_too_many_points_are_shown_on_the_hexagon_grid(pool, catalog, monkeypatch):
    # 점이 한도를 넘으면 위치를 보내지 않고, 위치를 물은 질문은 육각 격자를 기본으로 보인다.
    monkeypatch.setattr(
        analysis_workflow,
        "point_locations",
        lambda conn, view, boundary: point_locations(conn, view, boundary, limit=1),
    )

    state = AnalysisWorkflow(_daycare_count_llm("위치"), pool, FakeEmbedder()).run("서울 행정동 어린이집 위치를 보여줘")

    assert state.status == RunStatus.SUCCEEDED, state.errors
    layer = state.final_response.map_layer
    assert layer.points is None and layer.hexbins.count == 14
    assert layer.style.default_view == "hexbin"
    assert layer.style.views == ["proportional_symbol", "choropleth", "hexbin"]
