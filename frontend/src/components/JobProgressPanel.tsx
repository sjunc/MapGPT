import type { JobProgress } from "../api";
import { STEP_NAMES } from "./ErrorPanel";

// 그래프의 기본 순서. 계획 검증에 실패하면 계획 생성으로 돌아가므로 같은 단계가 두 번 보일 수 있다.
const STEP_ORDER = [
  "interpret_query",
  "select_datasets",
  "acquire_data",
  "build_analysis_plan",
  "validate_plan",
  "load_data",
  "execute_operations",
  "validate_result",
  "create_map_layer",
  "generate_explanation",
];
// 대부분 바로 끝나 목록에서 다음 단계로 보여 줄 필요가 없는 단계
const QUIET_STEPS = new Set(["acquire_data"]);

function nextStep(progress: JobProgress): string | null {
  const last = progress.steps.at(-1);
  if (!last) return progress.phase === "queued" ? null : STEP_ORDER[0];
  if (last.failed) return null;
  let index = STEP_ORDER.indexOf(last.step) + 1;
  while (index < STEP_ORDER.length && QUIET_STEPS.has(STEP_ORDER[index])) index += 1;
  return STEP_ORDER[index] ?? null;
}

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}초`;
}

/** 백그라운드 분석 작업의 단계 진행과 취소 버튼. 끝난 단계는 걸린 시간과 함께, 진행 중인 단계는 스피너로 보인다. */
export function JobProgressPanel({
  progress,
  elapsed,
  onCancel,
  className = "card progress p3-feedback",
}: {
  progress: JobProgress | null;
  elapsed?: number;
  onCancel?: () => void;
  className?: string;
}) {
  const steps = progress?.steps.filter((step) => !QUIET_STEPS.has(step.step) || step.duration_ms >= 500) ?? [];
  const seen = new Map<string, number>();
  const running = progress ? nextStep(progress) : null;
  const phase = progress?.phase ?? "queued";

  return (
    <section className={`${className} job-progress`} role="status" aria-live="polite">
      <div className="job-progress-head">
        <div className="spinner" aria-hidden="true" />
        <strong>
          {phase === "cancelling" ? "취소하는 중" : phase === "queued" ? "분석 대기 중" : "분석 중"}
          {elapsed !== undefined && ` · ${elapsed}초`}
        </strong>
        {onCancel && (
          <button type="button" className="job-cancel" onClick={onCancel} disabled={phase === "cancelling"}>
            {phase === "cancelling" ? "취소 요청됨" : "취소"}
          </button>
        )}
      </div>
      {phase === "queued" && <p className="muted">다른 분석이 끝나기를 기다리고 있습니다.</p>}
      {phase === "cancelling" && <p className="muted">진행 중인 단계가 끝나면 멈춥니다.</p>}
      <ol className="job-steps">
        {steps.map((step, index) => {
          const count = (seen.get(step.step) ?? 0) + 1;
          seen.set(step.step, count);
          return (
            <li key={`${step.step}-${index}`} className={step.failed ? "failed" : "done"}>
              <span aria-hidden="true">{step.failed ? "✕" : "✓"}</span>
              {STEP_NAMES[step.step] ?? step.step}
              {count > 1 && " (다시)"}
              <span className="job-step-time">{seconds(step.duration_ms)}</span>
            </li>
          );
        })}
        {running && phase !== "cancelling" && (
          <li className="active">
            <span aria-hidden="true">…</span>
            {STEP_NAMES[running] ?? running}
          </li>
        )}
      </ol>
    </section>
  );
}
