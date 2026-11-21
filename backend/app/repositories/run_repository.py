"""Provenance 저장소: 모든 분석 실행을 성공·실패와 관계없이 기록한다."""

from datetime import datetime
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


def count_runs_since(conn: psycopg.Connection, since: datetime) -> int:
    """since 이후에 시작한 분석 실행 수 (성공·실패 모두. 실패한 실행도 LLM을 호출한다)."""
    return conn.execute("SELECT count(*) FROM analysis.run WHERE started_at >= %s", (since,)).fetchone()[0]


def run_stats(conn: psycopg.Connection, since: datetime) -> dict[str, Any]:
    """since 이후에 시작한 실행의 건수·성공률·소요 시간 분위수·오류 코드·모델별 건수."""
    total, succeeded, failed, p50, p90 = conn.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE status = 'succeeded'),
               count(*) FILTER (WHERE status = 'failed'),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at)),
               percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at))
        FROM analysis.run WHERE started_at >= %s
        """,
        (since,),
    ).fetchone()
    errors = conn.execute(
        """
        SELECT e->>'code', count(DISTINCT r.run_id)
        FROM analysis.run r, jsonb_array_elements(coalesce(r.errors, '[]'::jsonb)) e
        WHERE r.started_at >= %s
        GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5
        """,
        (since,),
    ).fetchall()
    models = conn.execute(
        """
        SELECT coalesce(llm_model, ''), count(*) FROM analysis.run
        WHERE started_at >= %s GROUP BY 1 ORDER BY 2 DESC, 1
        """,
        (since,),
    ).fetchall()
    return {
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "success_rate": succeeded / total if total else None,
        "duration_p50_seconds": p50,
        "duration_p90_seconds": p90,
        "top_error_codes": [{"code": code, "runs": runs} for code, runs in errors],
        "by_llm_model": [{"llm_model": model, "runs": runs} for model, runs in models],
    }
