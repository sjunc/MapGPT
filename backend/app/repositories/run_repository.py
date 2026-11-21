"""Provenance 저장소: 모든 분석 실행을 성공·실패와 관계없이 기록한다.

실행을 시작할 때 running으로 한 번 저장하고, 끝나면 같은 행을 최종 상태로 덮어쓴다.
그래서 실행 중에도 GET /api/analyses/{run_id}가 running을 돌려준다.
"""

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.domain.state import AgentState


def save_run(conn: psycopg.Connection, state: AgentState) -> None:
    plan = state.analysis_plan
    candidates = {d.dataset_id: d for d in state.dataset_candidates}
    used = plan.datasets if plan else state.selected_datasets
    datasets = [
        {"dataset_id": d, "reference_date": candidates[d].temporal.model_dump(mode="json")["reference_date"]}
        for d in used
        if d in candidates
    ]
    result = state.execution_results
    response = state.final_response
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO analysis.run (
                run_id, question, status, analysis_plan, datasets, operations, crs, result_id,
                llm_provider, llm_model, errors, state, started_at, finished_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status, analysis_plan = EXCLUDED.analysis_plan, datasets = EXCLUDED.datasets,
                operations = EXCLUDED.operations, crs = EXCLUDED.crs, result_id = EXCLUDED.result_id,
                errors = EXCLUDED.errors, state = EXCLUDED.state, finished_at = EXCLUDED.finished_at
            """,
            (
                state.run_id,
                state.question,
                state.status,
                Jsonb(plan.model_dump(mode="json")) if plan else None,
                Jsonb(datasets),
                Jsonb([log.model_dump(mode="json") for log in result.operations]) if result else None,
                result.analysis_crs if result else None,
                response.result_id if response else None,
                state.llm_provider,
                state.llm_model,
                Jsonb([e.model_dump(mode="json") for e in state.errors]),
                # GeoJSON은 analysis.result에만 저장한다.
                Jsonb(state.model_dump(mode="json", exclude={"map_layer", "final_response"})),
                state.started_at,
                state.finished_at,
            ),
        )
        if response is not None:
            conn.execute(
                "INSERT INTO analysis.result (result_id, run_id, response, created_at) VALUES (%s, %s, %s, now())",
                (response.result_id, state.run_id, Jsonb(response.model_dump(mode="json"))),
            )


def get_run(conn: psycopg.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT r.run_id::text, r.status, r.question, r.state, res.response
        FROM analysis.run r LEFT JOIN analysis.result res ON res.run_id = r.run_id
        WHERE r.run_id = %s
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    run_id, status, question, state, response = row
    return {"run_id": run_id, "status": status, "question": question, "provenance": state, "result": response}
