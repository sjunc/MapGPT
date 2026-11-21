"""백그라운드 분석 작업: 요청은 run_id만 받고 바로 끝나며, 진행 상황은 SSE로, 결과는 실행 기록으로 받는다.

- 작업은 이 프로세스의 스레드 풀에서 돈다. 동시에 도는 분석 수를 analysis_max_concurrency로 묶고, 나머지는 기다린다.
- 진행 이벤트는 메모리에만 둔다. 서버가 다시 시작되면 진행 중이던 작업은 사라지고 실행 기록은 running으로 남는다.
  여러 프로세스로 띄우거나 재시작에도 살아남아야 하면 PostgreSQL 큐(SELECT … FOR UPDATE SKIP LOCKED)로 옮긴다
  (backend-handoff 8.1).
- 끝난 작업은 FINISHED_TTL_SECONDS 동안 남겨 늦게 연결한 화면도 마지막 이벤트를 받게 한다.
"""

import json
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import AnalysisScope
from app.domain.state import AgentState
from app.workflows.analysis_workflow import AnalysisWorkflow, ProgressEvent

FINISHED_TTL_SECONDS = 600
KEEPALIVE_SECONDS = 15  # 프록시가 조용한 연결을 끊지 않게 보내는 주석 줄 간격


@dataclass
class AnalysisJob:
    run_id: str
    question: str
    cancel: threading.Event = field(default_factory=threading.Event)
    events: list[dict[str, Any]] = field(default_factory=list)
    finished_at: float | None = None
    _condition: threading.Condition = field(default_factory=threading.Condition)

    def publish(self, event: str, data: dict[str, Any], *, final: bool = False) -> None:
        with self._condition:
            self.events.append({"id": len(self.events), "event": event, "data": data})
            if final:
                self.finished_at = time.monotonic()
            self._condition.notify_all()

    @property
    def done(self) -> bool:
        return self.finished_at is not None

    def stream(self, keepalive: float = KEEPALIVE_SECONDS) -> Iterator[str]:
        """지금까지의 이벤트를 먼저 보내고, 끝날 때까지 새 이벤트를 SSE 형식으로 보낸다."""
        sent = 0
        while True:
            with self._condition:
                if sent >= len(self.events) and not self.done:
                    self._condition.wait(timeout=keepalive)
                pending = self.events[sent:]
                done = self.done
            for item in pending:
                yield format_sse(item["event"], item["data"], item["id"])
            sent += len(pending)
            if done and sent >= len(self.events):
                return
            if not pending:
                yield ": keep-alive\n\n"


def format_sse(event: str, data: dict[str, Any], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class AnalysisJobs:
    def __init__(self, max_workers: int, time_budget_seconds: float | None):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="analysis")
        self._time_budget = time_budget_seconds
        self._jobs: dict[str, AnalysisJob] = {}
        self._lock = threading.Lock()

    def submit(self, workflow: AnalysisWorkflow, question: str, scope: AnalysisScope) -> AnalysisJob:
        self._prune()
        job = AnalysisJob(run_id=str(uuid.uuid4()), question=question)
        with self._lock:
            self._jobs[job.run_id] = job
        job.publish("queued", {"run_id": job.run_id})
        self._executor.submit(self._run, job, workflow, scope)
        return job

    def get(self, run_id: str) -> AnalysisJob | None:
        with self._lock:
            return self._jobs.get(run_id)

    def shutdown(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job: AnalysisJob, workflow: AnalysisWorkflow, scope: AnalysisScope) -> None:
        if job.cancel.is_set():  # 기다리는 동안 취소됐으면 시작하지 않는다
            job.publish("done", {"run_id": job.run_id, "status": "failed", "error_code": "CANCELLED"}, final=True)
            return
        job.publish("started", {"run_id": job.run_id})

        def progress(event: ProgressEvent) -> None:
            job.publish(
                "step",
                {
                    "step": event.step,
                    "duration_ms": event.duration_ms,
                    "elapsed_ms": event.elapsed_ms,
                    "failed": event.failed,
                },
            )

        state: AgentState | None = None
        try:
            state = workflow.run(
                job.question,
                scope,
                run_id=job.run_id,
                on_progress=progress,
                cancel=job.cancel,
                time_budget_seconds=self._time_budget,
            )
        finally:
            if state is None:  # 실행 기록 저장 자체가 실패한 경우
                done = {"run_id": job.run_id, "status": "failed", "error_code": "INTERNAL_ERROR"}
            else:
                code = state.errors[-1].code if state.errors else None
                done = {"run_id": job.run_id, "status": state.status, "error_code": code}
            job.publish("done", done, final=True)

    def _prune(self) -> None:
        now = time.monotonic()
        with self._lock:
            for run_id in [
                run_id
                for run_id, job in self._jobs.items()
                if job.finished_at is not None and now - job.finished_at > FINISHED_TTL_SECONDS
            ]:
                del self._jobs[run_id]
