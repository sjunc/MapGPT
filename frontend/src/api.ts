import type { AnalysisResponse, AnalysisScope, FinalResponse, ValidationIssue, WorkflowError } from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").trim().replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

export async function requestAnalysis(question: string, scope: AnalysisScope): Promise<AnalysisResponse> {
  const response = await fetch(apiUrl("/api/analyses"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, scope }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? response.statusText);
    throw new Error(`분석 요청 실패 (${response.status}): ${detail}`);
  }
  return response.json();
}

// GET /api/analyses/{run_id}는 POST의 AnalysisResponse와 다르다.
// 원본 API 계약: {run_id, status, question, provenance, result}.
export interface RunProvenance {
  scope?: AnalysisScope;
  llm_provider?: string;
  llm_model?: string;
  started_at?: string | null;
  finished_at?: string | null;
  current_step?: string | null;
  interpreted_query?: {
    restated_question?: string;
    key_concepts?: string[];
    assumptions?: string[];
  } | null;
  dataset_search?: {
    query?: string;
    embedding_model?: string;
    hits?: { dataset_id: string; similarity: number }[];
  } | null;
  dataset_selection?: {
    required_data?: { concept: string; dataset_id: string | null }[];
    datasets?: string[];
    rationale?: string;
  } | null;
  analysis_plan?: {
    intent?: string;
    analysis_unit?: string;
    operations?: { label?: string; type?: string; output?: string }[];
  } | null;
  plan_history?: { validation?: { issues?: ValidationIssue[] } }[];
  plan_validation?: { issues?: ValidationIssue[] } | null;
  result_validation?: { issues?: ValidationIssue[] } | null;
  execution_results?: { operations?: { label?: string; tool?: string; duration_ms?: number }[] } | null;
  errors?: WorkflowError[];
}

export interface StoredAnalysis {
  run_id: string;
  status: AnalysisResponse["status"];
  question: string;
  provenance: RunProvenance | null;
  result: FinalResponse | null;
}

export const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function getStoredAnalysis(runId: string, signal?: AbortSignal): Promise<StoredAnalysis> {
  if (!RUN_ID_PATTERN.test(runId)) throw new Error("잘못된 실행 ID입니다. UUID 형식을 확인해주세요.");

  const response = await fetch(apiUrl(`/api/analyses/${encodeURIComponent(runId)}`), { signal });
  if (!response.ok) {
    if (response.status === 404) throw new Error("해당 실행 기록이 서버에 없습니다(404).");
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : response.statusText;
    throw new Error(`실행 기록 조회 실패 (${response.status}): ${detail}`);
  }
  const payload: StoredAnalysis = await response.json();
  if (payload.run_id !== runId || !["succeeded", "failed", "running"].includes(payload.status)) {
    throw new Error("실행 기록의 응답 형식이 올바르지 않습니다.");
  }
  return payload;
}

// ── 백그라운드 분석 작업 (POST /api/analysis-jobs + SSE 진행 이벤트) ──────────────

export interface JobStep {
  step: string;
  duration_ms: number;
  elapsed_ms: number;
  failed: boolean;
}

export interface JobProgress {
  runId: string;
  phase: "queued" | "running" | "cancelling";
  steps: JobStep[];
}

interface JobOptions {
  onStarted?: (runId: string) => void;
  onProgress?: (progress: JobProgress) => void;
  signal?: AbortSignal;
}

const POLL_INTERVAL_MS = 2000;

async function errorMessage(response: Response, prefix: string): Promise<string> {
  const body = await response.json().catch(() => null);
  const detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? response.statusText);
  return `${prefix} (${response.status}): ${detail}`;
}

/** 분석을 백그라운드 작업으로 시작하고, 끝날 때까지 단계 진행을 알린 뒤 저장된 결과를 돌려준다. signal을 abort하면 취소를 요청한다. */
export async function runAnalysisJob(question: string, scope: AnalysisScope, options: JobOptions = {}): Promise<AnalysisResponse> {
  const response = await fetch(apiUrl("/api/analysis-jobs"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, scope }),
  });
  if (!response.ok) throw new Error(await errorMessage(response, "분석 요청 실패"));
  const { run_id: runId } = (await response.json()) as { run_id: string };
  options.onStarted?.(runId);
  return followAnalysisJob(runId, options);
}

/** 이미 시작한 작업(새로고침으로 다시 연 running 실행 포함)을 끝까지 따라간다. */
export async function followAnalysisJob(runId: string, options: JobOptions = {}): Promise<AnalysisResponse> {
  const progress: JobProgress = { runId, phase: "queued", steps: [] };
  const emit = () => options.onProgress?.({ ...progress, steps: [...progress.steps] });
  const cancel = () => {
    progress.phase = "cancelling";
    emit();
    void fetch(apiUrl(`/api/analysis-jobs/${encodeURIComponent(runId)}/cancel`), { method: "POST" }).catch(() => undefined);
  };
  if (options.signal?.aborted) cancel();
  options.signal?.addEventListener("abort", cancel, { once: true });
  emit();
  try {
    await waitForJob(runId, progress, emit);
  } finally {
    options.signal?.removeEventListener("abort", cancel);
  }
  const stored = await getStoredAnalysis(runId);
  return { run_id: runId, status: stored.status, result: stored.result, errors: stored.provenance?.errors ?? [] };
}

function waitForJob(runId: string, progress: JobProgress, emit: () => void): Promise<void> {
  return new Promise((resolve) => {
    const source = new EventSource(apiUrl(`/api/analysis-jobs/${encodeURIComponent(runId)}/events`));
    // 다시 연결하면 서버가 처음부터 다시 보내므로 이미 받은 이벤트 번호는 건너뛴다.
    let lastId = -1;
    const fresh = (event: MessageEvent) => {
      const id = Number(event.lastEventId);
      if (event.lastEventId === "" || Number.isNaN(id)) return true;
      if (id <= lastId) return false;
      lastId = id;
      return true;
    };
    source.addEventListener("started", (event) => {
      if (!fresh(event as MessageEvent)) return;
      if (progress.phase === "queued") progress.phase = "running";
      emit();
    });
    source.addEventListener("step", (event) => {
      const message = event as MessageEvent;
      if (!fresh(message)) return;
      if (progress.phase === "queued") progress.phase = "running";
      progress.steps.push(JSON.parse(message.data) as JobStep);
      emit();
    });
    source.addEventListener("done", () => {
      source.close();
      resolve();
    });
    source.onerror = () => {
      // 연결을 다시 맺지 못하면(프록시가 SSE를 막는 등) 실행 기록을 주기적으로 조회한다.
      if (source.readyState !== EventSource.CLOSED) return;
      const poll = async () => {
        const record = await getStoredAnalysis(runId).catch(() => null);
        if (record && record.status !== "running") resolve();
        else window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
      };
      void poll();
    };
  });
}
