import { useEffect, useState } from "react";
import { Legend } from "./Legend";
import { RankingTable } from "./RankingTable";
import { ResultDetails } from "./ResultDetails";
import { ExecutionTrace } from "./ExecutionTrace";
import type { RunProvenance } from "../api";
import { type ColorScheme, formatValue, NULL_REASONS } from "../theme";
import type { FinalResponse } from "../types";
import "./analysis-result.css";

type PanelView = "overview" | "evidence" | "trace";

interface Props {
  result: FinalResponse;
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
  onRefreshTrace: () => void;
  onCopyLink: () => void;
  linkCopied: boolean;
}

/** 백엔드 계산 결과를 그대로 표시한다. 지표·순위·구간을 프런트에서 재계산하지 않는다. */
export function AnalysisResultPanel({
  result, scheme, hoveredKey, onHover, selectedKey, onSelect, onClearSelection,
  provenance, traceLoading, traceError, demo, onRefreshTrace, onCopyLink, linkCopied,
}: Props) {
  const [view, setView] = useState<PanelView>("overview");

  // 지도에서 새 지역을 고르면 상세 정보를 볼 수 있는 화면으로 돌아온다.
  useEffect(() => {
    if (selectedKey !== null) setView("overview");
  }, [selectedKey]);

  const selected = selectedKey
    ? result.map_layer.geojson.features.find((feature) => feature.properties.unit_key === selectedKey)?.properties
    : null;
  const selectedMetric = selected?.[result.metric.name];
  const selectedRank = result.rank_metric ? selected?.[result.rank_metric] : null;
  const detailMetrics = Object.entries(result.metric_labels).filter(
    ([key]) => key !== result.metric.name && key !== result.rank_metric,
  );

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
          <section className="card analysis-summary-card" aria-label="분석 요약">
            <p className="analysis-eyebrow">ANALYSIS SUMMARY</p>
            <p className="analysis-question">{result.question}</p>
            <h3>분석 결과</h3>
            <p className="analysis-summary-text">{result.summary}</p>
            <div className="analysis-primary-metric">
              <span className="analysis-metric-title">분석 지표</span>
              <strong>{result.metric.label}</strong>
              <span>{result.metric.interpretation}</span>
            </div>
          </section>

          <div className="analysis-stats" aria-label="분석 데이터 현황">
            <div className="analysis-stat">
              <span>분석 공간 단위</span>
              <strong>{result.map_layer.feature_count.toLocaleString("ko-KR")}</strong>
            </div>
            <div className="analysis-stat">
              <span>값 없음</span>
              <strong>{result.map_layer.null_count.toLocaleString("ko-KR")}</strong>
            </div>
          </div>

          <div className="analysis-mobile-legend">
            <Legend layer={result.map_layer} scheme={scheme} />
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
