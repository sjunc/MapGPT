import type { AnalysisScope } from "./types";

/** 분석 범위 설정. 이 브라우저에만 기억하고, 질문마다 서버로 함께 보낸다. */
export const SCOPES: AnalysisScope[] = ["seoul", "national"];

export const SCOPE_LABELS: Record<AnalysisScope, string> = { seoul: "서울", national: "전국" };

export const SCOPE_HINTS: Record<AnalysisScope, string> = {
  seoul: "서울특별시와 그 안의 구·행정동을 분석합니다. 지역을 말하지 않으면 서울 전체를 분석합니다.",
  national: "전국 지역을 분석합니다. 서울에만 있는 데이터(어린이집 등)는 서울 밖 지역에 쓸 수 없습니다.",
};

const STORAGE_KEY = "mapgpt.scope.v1";
const DEFAULT_SCOPE: AnalysisScope = "seoul";

export function readScope(): AnalysisScope {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return SCOPES.find((scope) => scope === stored) ?? DEFAULT_SCOPE;
  } catch {
    return DEFAULT_SCOPE;
  }
}

export function saveScope(scope: AnalysisScope): void {
  try {
    localStorage.setItem(STORAGE_KEY, scope);
  } catch {
    // 저장소를 쓸 수 없으면 이번 화면에서만 유지한다.
  }
}
