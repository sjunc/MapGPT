import type { WorkflowError } from "../types";
import { apiUrl } from "../api";

export const STEP_NAMES: Record<string, string> = {
  interpret_query: "질문 해석",
  select_datasets: "데이터 선택",
  acquire_data: "데이터 수집",
  build_analysis_plan: "분석 계획 생성",
  validate_plan: "계획 검증",
  load_data: "데이터 조회",
  execute_operations: "GIS 실행",
  validate_result: "결과 검증",
  create_map_layer: "지도 생성",
  generate_explanation: "결과 설명",
};

// 데이터가 없다는 답 (필요한 데이터가 등록되지 않았거나, 조건·지역에 맞는 데이터가 없음). 분석 실패와 구분해 보여 준다.
const NO_DATA_CODES = new Set(["DATA_NOT_AVAILABLE", "NO_MATCHING_DATA"]);

export function ErrorPanel({ errors, runId }: { errors: WorkflowError[]; runId?: string }) {
  const noData = errors.some((error) => NO_DATA_CODES.has(error.code));
  return (
    <section className="card error" role="alert">
      <h2>{noData ? "데이터가 없습니다" : "분석을 완료하지 못했습니다"}</h2>
      {errors.map((error) => (
        <div key={error.code + error.message}>
          <p>
            <strong>{STEP_NAMES[error.step ?? ""] ?? error.step ?? "요청"}</strong> 단계 · <code>{error.code}</code>
          </p>
          <p>{error.message}</p>
          {error.issues.length > 0 && (
            <ul>
              {error.issues.map((issue) => (
                <li key={issue.code + issue.message}>
                  <code>{issue.code}</code> {issue.path ? <span className="muted">{issue.path}</span> : null} {issue.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      {runId && <p className="muted provenance">실행 기록: <a href={apiUrl(`/api/analyses/${encodeURIComponent(runId)}`)} target="_blank" rel="noreferrer">{runId}</a></p>}
    </section>
  );
}
