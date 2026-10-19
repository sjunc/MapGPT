import type { AnalysisResponse } from "./types";

export async function loadDemoAnalysis(): Promise<AnalysisResponse> {
  const response = await fetch("/demo/analysis.json");
  if (!response.ok) throw new Error("데모 데이터가 없습니다. node scripts/create-demo.mjs를 먼저 실행하세요.");
  const payload: AnalysisResponse = await response.json();
  if (payload.status !== "succeeded" || !payload.result) {
    throw new Error("올바른 데모 응답이 아닙니다.");
  }
  return payload;
}
