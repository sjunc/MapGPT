"""단계 진행 알림·취소·시간 제한과 백그라운드 분석 작업 API."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app, get_embedder, get_llm
from app.config import Settings
from app.domain.enums import RunStatus, WorkflowStep
from app.repositories.run_repository import get_run
from app.workflows.analysis_workflow import AnalysisWorkflow
from tests.fakes import FakeEmbedder, daycare_llm
from tests.support import QUESTION

pytestmark = pytest.mark.integration


def test_progress_is_reported_per_step_and_timed(pool, catalog):
    events = []

    state = AnalysisWorkflow(daycare_llm(), pool, FakeEmbedder()).run(QUESTION, on_progress=events.append)

    assert state.status == RunStatus.SUCCEEDED, state.errors
    assert events[0].step == WorkflowStep.INTERPRET_QUERY and events[-1].step == WorkflowStep.GENERATE_EXPLANATION
    assert [event.step for event in events] == [timing.step for timing in state.step_timings]
    assert all(timing.duration_ms >= 0 for timing in state.step_timings)
    assert events[-1].elapsed_ms >= events[0].elapsed_ms


def test_cancel_stops_before_the_next_step_and_records_the_run(pool, catalog, conn):
    llm = daycare_llm()
    cancel = threading.Event()

    def cancel_after_interpretation(event):
        if event.step == WorkflowStep.INTERPRET_QUERY:
            cancel.set()

    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(
        QUESTION, on_progress=cancel_after_interpretation, cancel=cancel
    )

    assert state.status == RunStatus.FAILED
    assert [(error.step, error.code) for error in state.errors] == [(WorkflowStep.INTERPRET_QUERY, "CANCELLED")]
    assert len(llm.calls) == 1  # 해석만 하고 데이터 선택 LLM은 부르지 않았다
    stored = get_run(conn, state.run_id)
    assert stored["status"] == "failed" and stored["result"] is None


def test_time_budget_stops_the_run(pool, catalog):
    state = AnalysisWorkflow(daycare_llm(), pool, FakeEmbedder()).run(QUESTION, time_budget_seconds=1e-9)

    assert [error.code for error in state.errors] == ["TIME_BUDGET_EXCEEDED"]
    assert state.final_response is None


def test_run_is_recorded_as_running_while_it_runs(pool, catalog, conn):
    seen = []

    def record(event):
        if not seen:
            with pool.connection() as other:
                seen.append(get_run(other, run_id)["status"])

    run_id = "00000000-0000-4000-8000-000000000001"
    AnalysisWorkflow(daycare_llm(), pool, FakeEmbedder()).run(QUESTION, run_id=run_id, on_progress=record)

    assert seen == ["running"]
    assert get_run(conn, run_id)["status"] == "succeeded"


@pytest.fixture
def client(database_url, catalog):
    settings = Settings(database_url=database_url, llm_provider="ollama", openai_embedding_model="test-embedding")
    app = create_app(settings)
    app.dependency_overrides[get_llm] = lambda: daycare_llm()
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    with TestClient(app) as test_client:
        yield test_client


def _events(client, run_id):
    with client.stream("GET", f"/api/analysis-jobs/{run_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    parsed = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def test_analysis_job_streams_steps_and_stores_the_result(client):
    accepted = client.post("/api/analysis-jobs", json={"question": QUESTION})

    assert accepted.status_code == 202
    run_id = accepted.json()["run_id"]
    events = _events(client, run_id)
    names = [name for name, _ in events]
    assert names[0] == "queued" and names[1] == "started" and names[-1] == "done"
    steps = [data["step"] for name, data in events if name == "step"]
    assert steps[0] == "interpret_query" and steps[-1] == "generate_explanation"
    assert events[-1][1] == {"run_id": run_id, "status": "succeeded", "error_code": None}
    stored = client.get(accepted.json()["result_url"]).json()
    assert stored["status"] == "succeeded" and stored["result"]["ranking"]
    assert [t["step"] for t in stored["provenance"]["step_timings"]] == steps
    # 끝난 작업은 취소할 수 없다
    assert client.post(f"/api/analysis-jobs/{run_id}/cancel").status_code == 409


def test_unknown_job_is_404(client):
    missing = "00000000-0000-4000-8000-00000000abcd"
    assert client.get(f"/api/analysis-jobs/{missing}/events").status_code == 404
    assert client.post(f"/api/analysis-jobs/{missing}/cancel").status_code == 404
