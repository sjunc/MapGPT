import type { RunProvenance } from "../api";
import type { ValidationIssue } from "../types";

interface Props {
  provenance: RunProvenance | null;
  runId: string;
  loading: boolean;
  error: string | null;
  demo: boolean;
  onRefresh: () => void;
}

function displayTime(value: string | null | undefined): string {
  if (!value) return "기록 없음";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString("ko-KR");
}

function IssueList({ issues }: { issues: ValidationIssue[] | undefined }) {
  if (!issues?.length) return <p className="muted">검증 이슈 없음</p>;
  return (
    <ul>
      {issues.map((issue, index) => (
        <li key={`${issue.code}-${index}`}>
          <strong>{issue.severity === "warning" ? "주의" : "오류"}</strong> · {issue.message}
          {issue.path && <span className="muted"> ({issue.path})</span>}
        </li>
      ))}
    </ul>
  );
}

export function ExecutionTrace({ provenance, runId, loading, error, demo, onRefresh }: Props) {
  if (demo) {
    return <section className="card"><p>데모 모드에는 서버 실행 기록이 없습니다.</p></section>;
  }

  return (
    <section className="card p7-trace" aria-label="실행 기록 상세">
      <div className="p7-trace-header">
        <div>
          <h3>Agent 실행 기록</h3>
          <p className="muted p7-run-id">{runId}</p>
        </div>
        <button type="button" className="link-button" onClick={onRefresh} disabled={loading}>
          {loading ? "조회 중…" : "새로고침"}
        </button>
      </div>
      {error && <p className="p7-trace-error" role="alert">{error}</p>}
      {!provenance ? (
        <p className="muted">{loading ? "실행 기록을 불러오고 있습니다." : "실행 기록을 조회하면 분석 과정이 표시됩니다."}</p>
      ) : (
        <>
          <dl className="p7-trace-meta">
            <div><dt>사용 모델</dt><dd>{[provenance.llm_provider, provenance.llm_model].filter(Boolean).join(" / ") || "정보 없음"}</dd></div>
            <div><dt>시작</dt><dd>{displayTime(provenance.started_at)}</dd></div>
            <div><dt>종료</dt><dd>{displayTime(provenance.finished_at)}</dd></div>
          </dl>

          {provenance.interpreted_query && (
            <details open>
              <summary>1. 질문 해석</summary>
              <p>{provenance.interpreted_query.restated_question ?? ""}</p>
              {!!provenance.interpreted_query.key_concepts?.length && (
                <p className="muted">핵심 개념: {provenance.interpreted_query.key_concepts.join(", ")}</p>
              )}
            </details>
          )}

          {provenance.dataset_search && (
            <details>
              <summary>2. 데이터 후보 검색</summary>
              <p className="muted">검색어: {provenance.dataset_search.query ?? "정보 없음"}</p>
              <p className="muted">임베딩 모델: {provenance.dataset_search.embedding_model ?? "정보 없음"}</p>
              <ul>
                {(provenance.dataset_search.hits ?? []).map((hit) => (
                  <li key={hit.dataset_id}>
                    {hit.dataset_id} · 유사도 {Number.isFinite(hit.similarity) ? hit.similarity.toFixed(3) : "-"}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {provenance.dataset_selection && (
            <details>
              <summary>3. 데이터 선택</summary>
              <p>선택: {(provenance.dataset_selection.datasets ?? []).join(", ") || "정보 없음"}</p>
              <p>{provenance.dataset_selection.rationale ?? ""}</p>
            </details>
          )}

          {provenance.analysis_plan && (
            <details>
              <summary>4. 분석 계획</summary>
              <p className="muted">유형: {provenance.analysis_plan.intent ?? "정보 없음"} · 단위: {provenance.analysis_plan.analysis_unit ?? "정보 없음"}</p>
              <ol>
                {(provenance.analysis_plan.operations ?? []).map((operation, i) => (
                  <li key={`${operation.output ?? "operation"}-${i}`}>
                    {operation.label ?? operation.type ?? operation.output ?? `연산 ${i + 1}`}
                  </li>
                ))}
              </ol>
              <p className="muted">계획 시도: {provenance.plan_history?.length ?? 0}회</p>
              <IssueList issues={provenance.plan_validation?.issues} />
            </details>
          )}

          {!!provenance.execution_results?.operations?.length && (
            <details>
              <summary>5. GIS 연산 실행</summary>
              <ol>
                {provenance.execution_results.operations.map((operation, i) => (
                  <li key={`${operation.tool ?? "tool"}-${i}`}>
                    {operation.label ?? operation.tool ?? `연산 ${i + 1}`}
                    {typeof operation.duration_ms === "number" && Number.isFinite(operation.duration_ms)
                      ? ` · ${operation.duration_ms.toFixed(1)}ms` : ""}
                  </li>
                ))}
              </ol>
            </details>
          )}

          {provenance.result_validation && (
            <details>
              <summary>6. 결과 검증</summary>
              <IssueList issues={provenance.result_validation.issues} />
            </details>
          )}

          {!!provenance.errors?.length && (
            <details open>
              <summary>오류 ({provenance.errors.length})</summary>
              <ul>{provenance.errors.map((item, i) => <li key={`${item.code}-${i}`}>{item.message}</li>)}</ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
