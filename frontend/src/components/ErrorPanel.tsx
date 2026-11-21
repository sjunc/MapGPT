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

// 자주 나는 실패 코드의 사용자 안내 (코드 의미: backend/docs/agent-design-overview.md 3.7절·10.2절). 없는 코드는 서버 메시지만 보인다.
const SERVER_PROBLEM = "서버 설정이나 내부 문제로 분석하지 못했습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 실행 ID를 알려 주세요.";
const ERROR_HINTS: Record<string, string> = {
  REGION_OUT_OF_SCOPE: "지금 분석 범위(서울) 밖의 지역을 물었습니다. 분석 범위를 전국으로 바꾸면 분석할 수 있습니다.",
  REGION_NOT_SUPPORTED: "이 지역은 아직 데이터가 없습니다. 아래 메시지의 분석 가능한 지역으로 다시 물어봐 주세요.",
  UNIT_NOT_SUPPORTED: "요청한 공간 단위(예: 구별·동별)의 경계 데이터가 없습니다. 아래 메시지의 분석 가능한 단위로 다시 물어봐 주세요.",
  UNSUPPORTED_QUESTION: "아직 분석할 수 없는 질문입니다. 지역별 시설 수, 인구 대비 시설 수, 시설이 몰린 곳처럼 등록된 데이터로 답할 수 있는 질문을 해 주세요.",
  INCOMPLETE_INTERPRETATION: "질문에서 분석할 지역이나 단위를 찾지 못했습니다. '서울 행정동별'처럼 지역과 단위를 함께 적어 주세요.",
  DATA_NOT_AVAILABLE: "질문에 필요한 데이터가 아직 등록되어 있지 않거나, 분석 지역에 그 데이터가 없습니다. 아래 메시지에서 빠진 데이터를 확인해 주세요.",
  NO_MATCHING_DATA: "조건에 맞는 데이터가 없습니다. 조건(시설 유형·연령 등)이나 지역을 넓혀 다시 물어봐 주세요.",
  PLAN_INVALID: "질문을 실행할 수 있는 분석 계획으로 바꾸지 못했습니다. 질문을 더 짧고 분명하게 바꿔 다시 시도해 주세요.",
  LLM_FAILED: "AI 모델을 호출하지 못했습니다. 잠시 후 같은 질문을 다시 시도해 주세요. 계속되면 서버의 LLM 설정을 확인해야 합니다.",
  CATALOG_INDEX_STALE: SERVER_PROBLEM,
  EMBEDDING_FAILED: SERVER_PROBLEM,
  INTERNAL_ERROR: SERVER_PROBLEM,
};

interface Props {
  errors: WorkflowError[];
  runId?: string;
  /** 분석 범위 밖 지역(REGION_OUT_OF_SCOPE)일 때 전국 범위로 바꿔 다시 분석한다. 없으면 버튼을 보이지 않는다 */
  onUseNationalScope?: () => void;
}

export function ErrorPanel({ errors, runId, onUseNationalScope }: Props) {
  const noData = errors.some((error) => NO_DATA_CODES.has(error.code));
  return (
    <section className="card error" role="alert">
      <h2>{noData ? "데이터가 없습니다" : "분석을 완료하지 못했습니다"}</h2>
      {errors.map((error) => (
        <div key={error.code + error.message}>
          {ERROR_HINTS[error.code] && <p className="error-hint">{ERROR_HINTS[error.code]}</p>}
          {error.code === "REGION_OUT_OF_SCOPE" && onUseNationalScope && (
            <button type="button" className="error-action" onClick={onUseNationalScope}>
              전국 범위로 바꿔 다시 분석
            </button>
          )}
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
          <p className="error-code muted">
            {STEP_NAMES[error.step ?? ""] ?? error.step ?? "요청"} 단계 · <code>{error.code}</code>
          </p>
        </div>
      ))}
      {runId && <p className="muted provenance">실행 기록: <a href={apiUrl(`/api/analyses/${encodeURIComponent(runId)}`)} target="_blank" rel="noreferrer">{runId}</a></p>}
    </section>
  );
}
