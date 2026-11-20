import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from "react";
import { getStoredAnalysis, requestAnalysis } from "../api";
import { loadDemoAnalysis } from "../demo";
import { readScope, saveScope, SCOPES, SCOPE_HINTS, SCOPE_LABELS } from "../scope";
import { formatValue, classColors, layerStyle } from "../theme";
import type { AnalysisScope, FinalResponse, MapView } from "../types";
import {
  BIVARIATE_COLORS,
  KakaoShowcaseMap,
  type ShowcaseFeature,
  type ShowcaseView,
} from "./KakaoShowcaseMap";
import "./kakao-showcase.css";

const VIEWS: Array<{ id: ShowcaseView; api: MapView; label: string; icon: string; explanation: string }> = [
  { id: "choropleth", api: "choropleth", label: "단계 구분도", icon: "▦", explanation: "서버에서 계산한 구간으로 지역별 지표를 표현" },
  { id: "bubble", api: "proportional_symbol", label: "비례 원", icon: "◉", explanation: "분석 단위의 지표값을 원의 면적으로 표시" },
  { id: "point", api: "point", label: "시설 위치", icon: "●", explanation: "서버가 제공한 시설 좌표로 마커 표시" },
  { id: "bivariate", api: "bivariate", label: "이변량", icon: "▥", explanation: "서버가 제공한 두 지표의 구간을 함께 표시" },
];

function mapViewToShowcase(view: MapView): ShowcaseView {
  return VIEWS.find((item) => item.api === view)?.id ?? "choropleth";
}

function numberOf(feature: ShowcaseFeature | null, key: string): number | null {
  if (!feature) return null;
  const value = feature.properties[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function MapLegend({ view, result, demo }: { view: ShowcaseView; result: FinalResponse; demo: boolean }) {
  const layer = result.map_layer;
  const colors = classColors("light", layer.classification.class_count, layerStyle(layer).emphasis);
  return (
    <div className="kshow-legend">
      <div className="kshow-legend-head">
        <span>MAP LEGEND</span>
        <strong>{VIEWS.find((item) => item.id === view)?.label}</strong>
      </div>
      {view === "choropleth" && (
        <>
          <div className="kshow-swatches" style={{ gridTemplateColumns: `repeat(${colors.length}, 1fr)` }}>
            {colors.map((color, index) => (
              <div key={`${color}-${index}`} title={`${index + 1}구간`} style={{ background: color }} />
            ))}
          </div>
          <div className="kshow-legend-ends">
            <span>{layer.style?.legend_low ?? "작은 값"}</span>
            <span>{layer.style?.legend_high ?? "큰 값"}</span>
          </div>
          <small>{layer.metric_label} · 값 없음은 회색으로 표시</small>
        </>
      )}
      {view === "bubble" && (
        <>
          <div className="kshow-bubble-scale"><i /><i /><i /><span>원 넓이 ∝ {layer.metric_label}</span></div>
          <small>{demo ? "가상 시설 개수 · 표본 85개 표시" : "서버 지표 기준 · 최대 150개 단위 표시"}</small>
        </>
      )}
      {view === "point" && (
        <>
          <div className="kshow-point-scale"><span>📍</span>{demo ? "시연용 가상 위치" : "백엔드 시설 좌표"}</div>
          <small>{demo ? "실제 시설 좌표가 아님" : `시설 ${layer.points?.count.toLocaleString("ko-KR") ?? 0}개 · 최대 700개 지도 표시`}</small>
        </>
      )}
      {view === "bivariate" && (
        <>
          <div className="kshow-bi-layout">
            <div className="kshow-bi-grid">
              {[2, 1, 0].flatMap((y) => [0, 1, 2].map((x) => (
                <span key={`${y}-${x}`} style={{ background: BIVARIATE_COLORS[y][x] }} />
              )))}
            </div>
            <div><strong>가로</strong> {demo ? "어린이 인구" : layer.bivariate?.x.label}<br />
              <strong>세로</strong> {demo ? "시설 개수" : layer.bivariate?.y.label}</div>
          </div>
          <small>{demo ? "정적 데모 지표를 3단계로 구분" : "백엔드의 이변량 분류 경계값 사용"}</small>
        </>
      )}
      <div className="kshow-legend-footer">
        <span>지역 {layer.geojson.features.length.toLocaleString("ko-KR")}개</span>
        <span>{layer.crs}</span>
      </div>
    </div>
  );
}

export function KakaoShowcase() {
  const [demoResult, setDemoResult] = useState<FinalResponse | null>(null);
  const [result, setResult] = useState<FinalResponse | null>(null);
  const [mode, setMode] = useState<"demo" | "live">("demo");
  const [demoError, setDemoError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [serverMessage, setServerMessage] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState<AnalysisScope>(readScope);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState<"unknown" | "checking" | "online" | "degraded" | "offline">("unknown");
  const [runningId, setRunningId] = useState<string | null>(null);
  const [view, setView] = useState<ShowcaseView>("choropleth");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<{ key: string; sequence: number } | null>(null);
  const [resetSequence, setResetSequence] = useState(0);

  useEffect(() => {
    let active = true;
    loadDemoAnalysis()
      .then((payload) => {
        if (!active || !payload.result) return;
        setDemoResult(payload.result);
        setResult((current) => current ?? payload.result);
      })
      .catch((cause: unknown) => {
        if (active) setDemoError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => { active = false; };
  }, []);

  const choices = useMemo(() => {
    if (mode === "demo" || !result) return VIEWS;
    const supported = new Set(layerStyle(result.map_layer).views);
    // 카카오맵으로 구현한 보기만 노출한다. 서버가 허용하지 않은 보기/가짜 데이터를 보여주지 않는다.
    return VIEWS.filter((item) =>
      (item.id === "choropleth" && supported.has("choropleth")) ||
      (item.id === "bubble" && supported.has("proportional_symbol")) ||
      (item.id === "point" && supported.has("point") && Boolean(result.map_layer.points)) ||
      (item.id === "bivariate" && supported.has("bivariate") && Boolean(result.map_layer.bivariate))
    );
  }, [mode, result]);

  const actualView = choices.some((item) => item.id === view) ? view : choices[0]?.id ?? "choropleth";
  const chosenView = VIEWS.find((item) => item.id === actualView)!;
  const featureIndex = useMemo(() => {
    const index = new Map<string, ShowcaseFeature>();
    for (const feature of result?.map_layer.geojson.features ?? []) {
      index.set(feature.properties.unit_key, feature as ShowcaseFeature);
    }
    return index;
  }, [result]);
  const activeFeature = featureIndex.get(selectedKey ?? "") ?? null;
  const ranking = result?.ranking.slice(0, 10) ?? [];

  function setNewResult(next: FinalResponse) {
    setResult(next);
    setMode("live");
    setView(mapViewToShowcase(layerStyle(next.map_layer).default_view));
    setHoveredKey(null);
    setSelectedKey(null);
    setFocusRequest(null);
    setResetSequence((previous) => previous + 1);
  }

  function showDemo() {
    if (!demoResult || loading) return;
    setResult(demoResult);
    setMode("demo");
    setView("choropleth");
    setApiError(null);
    setServerMessage(null);
    setRunningId(null);
    setSelectedKey(null);
    setHoveredKey(null);
    setFocusRequest(null);
    setResetSequence((previous) => previous + 1);
  }

  function focusRegion(key: string) {
    setSelectedKey(key);
    setFocusRequest((previous) => ({ key, sequence: (previous?.sequence ?? 0) + 1 }));
  }

  async function submitAnalysis(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const text = question.trim();
    if (!text || loading || text.length > 500) return;
    setLoading(true);
    setApiError(null);
    setServerMessage(null);
    setRunningId(null);
    try {
      const payload = await requestAnalysis(text, scope);
      if (payload.status === "succeeded") {
        if (!payload.result) throw new Error("분석 완료 응답에 결과 데이터가 없습니다.");
        setNewResult(payload.result);
        setServerMessage(`분석 완료 · 실행 ID: ${payload.run_id}`);
      } else if (payload.status === "failed") {
        const details = payload.errors.map((entry) => entry.message).filter(Boolean).join(" / ");
        setApiError(details || "백엔드 분석이 실패했습니다. 실행 기록을 확인해 주세요.");
        setServerMessage(`실패한 실행 ID: ${payload.run_id}`);
      } else {
        setRunningId(payload.run_id);
        setServerMessage("서버가 분석 진행 중이라고 응답했습니다. 아래 버튼으로 완료 여부를 확인할 수 있습니다.");
      }
    } catch (cause: unknown) {
      setApiError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  async function refreshRunning() {
    if (!runningId || loading) return;
    setLoading(true);
    setApiError(null);
    try {
      const payload = await getStoredAnalysis(runningId);
      if (payload.status === "succeeded" && payload.result) {
        setNewResult(payload.result);
        setRunningId(null);
        setServerMessage(`분석 완료 · 실행 ID: ${payload.run_id}`);
      } else if (payload.status === "failed") {
        setRunningId(null);
        setApiError(payload.provenance?.errors?.map((item) => item.message).join(" / ") || "서버 분석이 실패했습니다.");
      } else {
        setServerMessage("아직 분석 중입니다. 잠시 후 다시 확인해 주세요.");
      }
    } catch (cause: unknown) {
      setApiError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  // 백엔드는 요청 시에만 켜지므로 자동 핑을 하지 않고 사용자가 눌렀을 때만 확인한다.
  async function checkHealth() {
    if (health === "checking") return;
    setHealth("checking");
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body: { status?: string } = await response.json();
      setHealth(body.status === "ok" ? "online" : "degraded");
    } catch {
      setHealth("offline");
    }
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submitAnalysis();
    }
  }

  return (
    <div className="kshow-root">
      <main className="kshow-stage" aria-label="카카오지도 기반 분석 지도">
        {result ? (
          <KakaoShowcaseMap
            result={result}
            demo={mode === "demo"}
            view={actualView}
            selectedKey={selectedKey}
            hoveredKey={hoveredKey}
            onHover={setHoveredKey}
            onSelect={setSelectedKey}
            focusRequest={focusRequest}
            resetSequence={resetSequence}
          />
        ) : (
          <div className="kshow-map-status" role={demoError ? "alert" : "status"}>
            {demoError ?? "초기 지도 데이터를 불러오는 중…"}
          </div>
        )}

        <div className="kshow-topbar">
          <div className="kshow-brand">
            <img src="/MapGPT.png" alt="" />
            <div><strong>MAPGPT</strong><small>KAKAO MAP + ANALYSIS API</small></div>
          </div>
          <span className="kshow-demo-tag">{mode === "demo" ? "● DEMO · MOCK DATA" : "● BACKEND · ANALYSIS RESULT"}</span>
        </div>

        <nav className="kshow-view-switch" aria-label="지도 시각화 유형">
          {choices.map((item) => (
            <button
              type="button" key={item.id}
              className={actualView === item.id ? "kshow-view active" : "kshow-view"}
              onClick={() => { setView(item.id); setHoveredKey(null); }}
              aria-pressed={actualView === item.id}
              title={item.explanation}
            >
              <span aria-hidden="true">{item.icon}</span>{item.label}
            </button>
          ))}
        </nav>
        <div className="kshow-map-actions">
          <button type="button" onClick={() => setResetSequence((n) => n + 1)}>↺ 전체 보기</button>
          <button type="button" onClick={() => { setSelectedKey(null); setHoveredKey(null); }}>선택 해제</button>
        </div>
        {result && <MapLegend view={actualView} result={result} demo={mode === "demo"} />}
        <div className="kshow-corner-note">
          {mode === "demo" ? "2015년 법정동 경계 · 가상 통계 · 실제 분석 결과 아님" : "백엔드 분석 결과 · 출처 및 한계는 오른쪽 패널 참고"}
        </div>
      </main>

      <aside className="kshow-aside" aria-label="분석 결과 및 정보">
        <div className="kshow-aside-header">
          <span className="kshow-kicker">MAPGPT / SPATIAL ANALYSIS</span>
          <h1>{mode === "demo" ? "공간 분석 미리보기" : "공간 분석 결과"}</h1>
          <p>{mode === "demo" ? "시연 데이터로 지도를 표시하고 있어. 하단에 질문을 입력하면 실제 백엔드에 분석을 요청해." : result?.summary ?? "서버 분석 결과를 지도에 표시했어."}</p>
          <div className="kshow-stats">
            <div><small>분석 범위</small><strong>{SCOPE_LABELS[scope]}</strong></div>
            <div><small>시각화</small><strong>{choices.length}종</strong></div>
            <div><small>데이터</small><strong>{mode === "demo" ? "가상" : "서버"}</strong></div>
          </div>
        </div>

        <div className="kshow-aside-content">
          <section className="kshow-panel kshow-prompt">
            <span className="kshow-kicker">CURRENT QUESTION</span>
            <h2>“{result?.question ?? "분석 질문을 입력해 주세요"}”</h2>
            <p>{mode === "demo" ? "현재 지도는 정적 데모야. 실제 결과는 아래 질문 입력창으로 요청해." : result?.metric.interpretation}</p>
          </section>

          <section className="kshow-panel">
            <span className="kshow-kicker">CURRENT VIEW</span>
            <h2>{chosenView.label}</h2>
            <p>{chosenView.explanation}</p>
            {mode === "live" && choices.length === 0 && <p>현재 백엔드의 기본 지도 유형은 아직 카카오맵에서 지원하지 않아.</p>}
          </section>

          <section className="kshow-panel">
            <div className="kshow-section-title"><span className="kshow-kicker">SELECTED AREA</span><span>지도 또는 순위 클릭</span></div>
            {activeFeature && result ? (
              <>
                <h2 className="kshow-area-name">{activeFeature.properties.unit_name}</h2>
                <div className="kshow-highlight-number">
                  <strong>{formatValue(numberOf(activeFeature, result.map_layer.metric))}</strong>
                  <small>{result.map_layer.metric_label}</small>
                </div>
                <div className="kshow-details">
                  {Object.entries(result.metric_labels)
                    .filter(([key]) => key !== result.map_layer.metric)
                    .slice(0, 6)
                    .map(([key, label]) => (
                      <div key={key}><span>{label}</span><b>{formatValue(numberOf(activeFeature, key))}</b></div>
                    ))}
                  <div><span>지역 코드</span><b>{activeFeature.properties.unit_key}</b></div>
                </div>
              </>
            ) : (
              <p className="kshow-empty">지도 또는 아래 지역 순위를 선택하면 상세 수치가 표시돼.</p>
            )}
          </section>

          {ranking.length > 0 && (
            <section className="kshow-panel">
              <div className="kshow-section-title"><span className="kshow-kicker">RANKING</span><span>{mode === "demo" ? "가상 순위" : "서버 산출 순위"}</span></div>
              <h2>지역별 분석 순위</h2>
              <div className="kshow-ranking">
                {ranking.map((item) => (
                  <button
                    type="button" key={item.unit_key}
                    onClick={() => focusRegion(item.unit_key)}
                    onMouseEnter={() => setHoveredKey(item.unit_key)}
                    onMouseLeave={() => setHoveredKey(null)}
                    className={item.unit_key === selectedKey || item.unit_key === hoveredKey ? "kshow-rank active" : "kshow-rank"}
                  >
                    <span className="kshow-rank-number">{String(item.rank).padStart(2, "0")}</span>
                    <span className="kshow-rank-title">{item.unit_name}</span>
                    <strong>{formatValue(item.value)}</strong>
                    <span className="kshow-rank-arrow">↗</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {mode === "live" && result && (
            <section className="kshow-panel kshow-evidence">
              <span className="kshow-kicker">DATA & LIMITATIONS</span>
              <h2>분석 방법 및 출처</h2>
              {result.analysis_method.map((line, index) => <p key={`method-${index}`}>• {line}</p>)}
              {result.datasets.map((data) => (
                <p key={data.dataset_id}><strong>{data.title}</strong> · {data.provider} · 기준 {data.reference_date ?? "미상"}</p>
              ))}
              {result.assumptions.map((line, index) => <p key={`assumption-${index}`}>가정: {line}</p>)}
              {result.limitations.map((line, index) => <p key={`limit-${index}`}>한계: {line}</p>)}
            </section>
          )}

          <section className="kshow-footer">
            <strong>{mode === "demo" ? "DATA NOTICE" : "LIVE RESPONSE"}</strong>
            <p>{mode === "demo" ? "현재 수치는 시연용 가상 데이터야. 백엔드가 꺼져 있어도 데모는 정상 동작해." : "서버의 분석 결과를 직접 표시했어. 질문마다 독립적으로 분석을 요청하며 이전 대화 맥락은 자동 전달하지 않아."}</p>
            {demoResult && <button type="button" className="kshow-demo-reset" onClick={showDemo} disabled={loading}>가상 데모로 돌아가기</button>}
          </section>
        </div>
      </aside>

      <form className="kshow-chat" onSubmit={(event: FormEvent<HTMLFormElement>) => void submitAnalysis(event)}>
        <div className="kshow-chat-top">
          <div className="kshow-chat-scopes" role="group" aria-label="분석 범위">
            {SCOPES.map((item) => (
              <button
                type="button" key={item} aria-pressed={scope === item}
                className={scope === item ? "active" : ""}
                onClick={() => { setScope(item); saveScope(item); }}
                title={SCOPE_HINTS[item]} disabled={loading}
              >{SCOPE_LABELS[item]}</button>
            ))}
          </div>
          <div className="kshow-chat-status">
            <span>{health === "online" ? "서버 연결됨" : health === "degraded" ? "서버 확인 필요" : health === "offline" ? "서버 응답 없음" : health === "checking" ? "서버 확인 중…" : loading ? "분석 요청 처리 중…" : "서버는 요청 시 가동"}</span>
            <button type="button" onClick={() => void checkHealth()} disabled={health === "checking"}>연결 확인</button>
          </div>
        </div>
        <div className="kshow-chat-row">
          <textarea
            aria-label="공간 분석 질문" placeholder="예: 서울에서 어린이 인구 대비 어린이집이 부족한 지역은?"
            value={question} onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setQuestion(event.target.value)}
            onKeyDown={onComposerKeyDown} maxLength={500} rows={2} disabled={loading}
          />
          <button type="submit" className="kshow-chat-send" disabled={loading || !question.trim()}>
            {loading ? "분석 중" : "분석 요청 ↗"}
          </button>
        </div>
        {(apiError || serverMessage || runningId) && (
          <div className={apiError ? "kshow-chat-feedback error" : "kshow-chat-feedback"} role={apiError ? "alert" : "status"}>
            <span>{apiError ?? serverMessage}</span>
            {runningId && <button type="button" onClick={() => void refreshRunning()} disabled={loading}>실행 상태 확인</button>}
          </div>
        )}
      </form>
    </div>
  );
}
