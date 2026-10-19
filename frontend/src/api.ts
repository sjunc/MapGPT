import type { AnalysisResponse, FinalResponse, ValidationIssue, WorkflowError } from "./types";

export async function requestAnalysis(question: string): Promise<AnalysisResponse> {
  const response = await fetch("/api/analyses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
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
  dataset_selection?: { datasets?: string[]; rationale?: string } | null;
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

  const response = await fetch(`/api/analyses/${encodeURIComponent(runId)}`, { signal });
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
