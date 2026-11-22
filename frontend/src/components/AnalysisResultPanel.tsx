import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Legend } from "./Legend";
import { RankingTable } from "./RankingTable";
import { ResultDetails } from "./ResultDetails";
import { ExecutionTrace } from "./ExecutionTrace";
import { ResultShare } from "./ResultShare";
import type { RunProvenance } from "../api";
import { SCOPE_LABELS } from "../scope";
import { type ColorScheme, formatValue, layerStyle, NULL_REASONS } from "../theme";
import type { AnalysisScope, FinalResponse, MapView } from "../types";
import "./analysis-result.css";

type PanelView = "overview" | "evidence" | "trace";

interface Props {
  result: FinalResponse;
  mapView: MapView;
  onMapViewChange: (view: MapView) => void;
  scheme: ColorScheme;
  hoveredKey: string | null;
  onHover: (key: string | null) => void;
  selectedKey: string | null;
  onSelect: (key: string) => void;
  onClearSelection: () => void;
  provenance: RunProvenance | null;
  traceLoading: boolean;
  traceError: string | null;
  demo: boolean;
  /** 이 결과를 분석한 범위 (통계 카드) */
  scope: AnalysisScope;
  onRefreshTrace: () => void;
  onCopyLink: () => void;
  linkCopied: boolean;
}

/** 백엔드 계산 결과를 그대로 표시한다. 지표·순위·구간을 프런트에서 재계산하지 않는다. */
export function AnalysisResultPanel({
  result, mapView, onMapViewChange, scheme, hoveredKey, onHover, selectedKey, onSelect, onClearSelection,
  provenance, traceLoading, traceError, demo, scope, onRefreshTrace, onCopyLink, linkCopied,
}: Props) {
  const [view, setView] = useState<PanelView>("overview");

  const [summaryOpen, setSummaryOpen] = useState(false);
  const [summaryClamped, setSummaryClamped] = useState(false);
  const summaryRef = useRef<HTMLParagraphElement>(null);

  // 지도에서 새 지역을 고르면 상세 정보를 볼 수 있는 화면으로 돌아온다.
  useEffect(() => {
    if (selectedKey !== null) setView("overview");
  }, [selectedKey]);

  // 새 결과를 열면 AI 요약을 다시 두 줄로 접는다.
  useEffect(() => {
    setSummaryOpen(false);
  }, [result.run_id]);

  // AI 요약이 두 줄을 넘을 때만 "더 보기"를 보인다. 패널 폭이 바뀌면 다시 잰다.
  useLayoutEffect(() => {
    const element = summaryRef.current;
    if (!element || summaryOpen) return;
    const measure = () => setSummaryClamped(element.scrollHeight > element.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [result.run_id, result.summary, summaryOpen, view]);

  const selected = selectedKey
    ? result.map_layer.geojson.features.find((feature) => feature.properties.unit_key === selectedKey)?.properties
    : null;
  const selectedMetric = selected?.[result.metric.name];
  const selectedRank = result.rank_metric ? selected?.[result.rank_metric] : null;
  const detailMetrics = Object.entries(result.metric_labels).filter(
    ([key]) => key !== result.metric.name && key !== result.rank_metric,
  );
  // 답 카드: 응답 순위의 1위. 순위가 없는 결과는 요약 문장을 대신 보인다.
  const top = result.ranking[0];
  const rankLabel = (result.rank_metric && result.metric_labels[result.rank_metric]) || "순위";

  return (
    <div className="analysis-result">
      <div className="analysis-view-switch" role="group" aria-label="분석 결과 화면 선택">
        <button
          type="button"
          className={view === "overview" ? "active" : undefined}
          aria-pressed={view === "overview"}
          onClick={() => setView("overview")}
        >
          결과·순위
        </button>
        <button
          type="button"
          className={view === "evidence" ? "active" : undefined}
          aria-pressed={view === "evidence"}
          onClick={() => setView("evidence")}
        >
          분석 근거
        </button>
        <button
          type="button"
          className={view === "trace" ? "active" : undefined}
          aria-pressed={view === "trace"}
          onClick={() => setView("trace")}
        >
          실행 기록
        </button>
      </div>

      {view === "overview" ? (
        <>
          {/* 답 먼저: 질문의 답(순위 1위 지역과 값)을 맨 위에 크게 보인다. 수치는 코드가 계산한 응답 값이다. */}
          <section className="card analysis-answer" aria-label="분석 결과 요약">
            <p className="analysis-question">{result.question}</p>
            {top ? (
              <>
                <p className="analysis-answer-caption">{rankLabel} 1위</p>
                <div className="analysis-answer-main">
                  <button
                    type="button"
                    className="p5-region-button analysis-answer-place"
                    title={`${top.unit_name} 지도에서 찾기`}
                    onMouseEnter={() => onHover(top.unit_key)}
                    onMouseLeave={() => onHover(null)}
                    onClick={() => onSelect(top.unit_key)}
                  >
                    {top.parent_name && <span>{top.parent_name}</span>}
                    {top.unit_name}
                  </button>
                  <strong className="analysis-answer-value">{formatValue(top.value)}</strong>
                </div>
              </>
            ) : (
              <p className="analysis-summary-text">{result.summary}</p>
            )}
            <p className="analysis-answer-metric">{result.metric.label}</p>
            <p className="analysis-answer-note">{result.metric.interpretation}</p>
          </section>

          <div className="analysis-mobile-legend">
            <Legend layer={result.map_layer} scheme={scheme} view={mapView} onViewChange={onMapViewChange} />
          </div>

          {selected && (
            <section className="card p5-selected-card" aria-label="선택한 지역의 분석 상세">
              <div className="p5-selected-header">
                <div>
                  <p className="analysis-eyebrow">SELECTED AREA</p>
                  <h3>{selected.parent_name ? `${selected.parent_name} ` : ""}{selected.unit_name}</h3>
                </div>
                <button type="button" onClick={onClearSelection} className="p5-clear-selection">선택 해제</button>
              </div>
              <div className="p5-selected-metric">
                <span>{result.metric.label}</span>
                <strong>
                  {typeof selectedMetric === "number"
                    ? formatValue(selectedMetric)
                    : NULL_REASONS[selected.null_reason ?? ""] ?? "값 없음"}
                </strong>
              </div>
              {typeof selectedRank === "number" && (
                <p className="p5-selected-rank">분석 순위: {formatValue(selectedRank, 0)}위</p>
              )}
              {detailMetrics.length > 0 && (
                <dl className="p5-detail-list">
                  {detailMetrics.map(([key, label]) => (
                    <div key={key}>
                      <dt>{label}</dt>
                      <dd>{typeof selected[key] === "number" ? formatValue(selected[key] as number) : "값 없음"}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </section>
          )}

          {result.ranking.length > 0 ? (
            <RankingTable
              result={result}
              scheme={scheme}
              hoveredKey={hoveredKey}
              onHover={onHover}
              selectedKey={selectedKey}
              onSelect={onSelect}
            />
          ) : (
            <section className="card">
              <h3>지역 순위</h3>
              <p className="muted">이번 분석 결과에는 순위 정보가 없습니다.</p>
            </section>
          )}

          {/* 모델이 쓴 요약은 순위 아래에 두 줄까지 보이고 펼쳐 읽는다. 순위가 없으면 위의 답 카드에 있다. */}
          {top && (
            <section className="card analysis-ai-summary" aria-label="AI 요약">
              <p className="analysis-ai-label">AI 요약</p>
              <p ref={summaryRef} className={summaryOpen ? "analysis-ai-text" : "analysis-ai-text clamped"}>
                {result.summary}
              </p>
              {(summaryOpen || summaryClamped) && (
                <button
                  type="button"
                  className="link-button"
                  aria-expanded={summaryOpen}
                  onClick={() => setSummaryOpen((open) => !open)}
                >
                  {summaryOpen ? "접기" : "더 보기"}
                </button>
              )}
            </section>
          )}

          {/* 통계 카드(분석 범위·지역 수·값 없음·시각화·데이터)를 한 줄로 줄인 것 */}
          <p className="analysis-meta" aria-label="분석 개요">
            분석 범위 {SCOPE_LABELS[scope]} · 지역 {result.map_layer.feature_count.toLocaleString("ko-KR")}개 · 값 없음{" "}
            {result.map_layer.null_count.toLocaleString("ko-KR")}개 · 시각화 {layerStyle(result.map_layer).views.length}종 ·{" "}
            {demo ? "가상" : "서버"} 데이터
          </p>

          {!demo && <ResultShare runId={result.run_id} onCopyLink={onCopyLink} linkCopied={linkCopied} />}

          <button type="button" className="analysis-evidence-link" onClick={() => setView("evidence")}>
            데이터 출처와 분석 방법 확인 <span aria-hidden="true">→</span>
          </button>
        </>
      ) : view === "evidence" ? (
        <>
          <section className="card analysis-evidence-intro">
            <h3>분석 근거</h3>
            <p className="muted">
              사용 데이터의 출처·기준 시점, 분석 방법, 가정과 한계를 확인할 수 있습니다.
            </p>
          </section>
          <ResultDetails result={result} />
        </>
      ) : (
        <>
          {!demo && (
            <button type="button" className="analysis-evidence-link" onClick={onCopyLink}>
              {linkCopied ? "결과 주소 복사 완료" : "이 분석 결과의 링크 복사"} <span aria-hidden="true">↗</span>
            </button>
          )}
          <ExecutionTrace
            provenance={provenance}
            runId={result.run_id}
            loading={traceLoading}
            error={traceError}
            demo={demo}
            onRefresh={onRefreshTrace}
          />
        </>
      )}
    </div>
  );
}
