import { RUN_ID_PATTERN } from "./api";

/** 서버의 전체 실행 목록이 아니라, 이 브라우저에서 열어본 실행의 단축 기록이다. */
export interface RecentRun {
  runId: string;
  question: string;
  viewedAt: string;
}

const STORAGE_KEY = "mapgpt.recentRuns.v1";
const MAX_RUNS = 8;

export function readRecentRuns(): RecentRun[] {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as unknown;
    if (!Array.isArray(stored)) return [];
    return stored.filter((item): item is RecentRun => {
      if (typeof item !== "object" || item === null) return false;
      const row = item as Record<string, unknown>;
      return typeof row.runId === "string" && RUN_ID_PATTERN.test(row.runId) &&
        typeof row.question === "string" && typeof row.viewedAt === "string";
    }).slice(0, MAX_RUNS);
  } catch {
    return [];
  }
}

export function saveRecentRuns(entries: RecentRun[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_RUNS)));
  } catch {
    // 브라우저 저장 공간 사용이 차단된 경우에도 분석 기능은 계속 동작한다.
  }
}

export function addRecentRun(entries: RecentRun[], runId: string, question: string): RecentRun[] {
  return [
    { runId, question, viewedAt: new Date().toISOString() },
    ...entries.filter((entry) => entry.runId !== runId),
  ].slice(0, MAX_RUNS);
}
