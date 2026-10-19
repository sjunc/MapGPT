import { type FormEvent, useEffect, useRef, useState } from "react";
import { getStoredAnalysis, requestAnalysis } from "./api";
import type { StoredAnalysis } from "./api";
import { RecentRuns } from "./components/RecentRuns";
import { ExecutionTrace } from "./components/ExecutionTrace";
import { addRecentRun, readRecentRuns, saveRecentRuns } from "./runHistory";
import type { RecentRun } from "./runHistory";
import { BaseMap } from "./components/BaseMap";
import { ChoroplethMap } from "./components/ChoroplethMap";
import { AnalysisResultPanel } from "./components/AnalysisResultPanel";
import { ErrorPanel } from "./components/ErrorPanel";
import { Legend } from "./components/Legend";
import { loadDemoAnalysis } from "./demo";
import { useColorScheme } from "./theme";
import type { AnalysisResponse, WorkflowError } from "./types";
import type { FocusRequest } from "./components/ChoroplethMap";
import "./p3.css";
import "./p4.css";
import "./p5.css";
import "./p6.css";
import "./p7.css";

const EXAMPLE = "서울에서 어린이 인구 대비 어린이집이 부족한 행정동을 알려줘.";
const DEMO_MODE = new URLSearchParams(window.location.search).get("demo") === "1";

type FailedRun = { runId: string; errors: WorkflowError[] };

function useElapsedSeconds(running: boolean): number {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (!running) return;
    setSeconds(0);
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  return seconds;
}

export function App() {
  const scheme = useColorScheme();
  const [question, setQuestion] = useState("");
  const [panelOpen, setPanelOpen] = useState(true);
  const [mobileExpanded, setMobileExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<AnalysisResponse | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [failedRun, setFailedRun] = useState<FailedRun | null>(null);
  const [runningRunId, setRunningRunId] = useState<string | null>(null);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<FocusRequest | null>(null);
  const [storedRun, setStoredRun] = useState<StoredAnalysis | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const [recentRuns, setRecentRuns] = useState<RecentRun[]>(readRecentRuns);
  // 늦게 도착한 이전 요청 응답이 새 지도를 덮어쓰지 않도록 구분한다.
  const requestSequence = useRef(0);
  const elapsed = useElapsedSeconds(loading);
  const result = response?.result ?? null;

  // Demo Mode에서는 서버 없이 같은 분석 결과 UI를 사용한다.
  useEffect(() => {
    if (!DEMO_MODE) return;
    let active = true;

    loadDemoAnalysis()
      .then((data) => {
        if (active) setResponse(data);
      })
      .catch((error: unknown) => {
        if (active) {
          setRequestError(error instanceof Error ? error.message : String(error));
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!DEMO_MODE) saveRecentRuns(recentRuns);
  }, [recentRuns]);

  function rememberRun(runId: string, text: string) {
    setRecentRuns((previous) => addRecentRun(previous, runId, text));
  }

  function updateRunUrl(runId: string | null, replace = false) {
    if (DEMO_MODE) return;
    const url = new URL(window.location.href);
    if (runId) url.searchParams.set("run", runId);
    else url.searchParams.delete("run");
    if (replace) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
  }

  async function fetchTrace(runId: string, sequence: number) {
    setTraceLoading(true);
    setTraceError(null);
    try {
      const record = await getStoredAnalysis(runId);
      if (requestSequence.current !== sequence) return;
      setStoredRun(record);
    } catch (error) {
      if (requestSequence.current !== sequence) return;
      setTraceError(error instanceof Error ? error.message : String(error));
    } finally {
      if (requestSequence.current === sequence) setTraceLoading(false);
    }
  }

  async function loadRun(runId: string) {
    if (DEMO_MODE) return;
    const sequence = ++requestSequence.current;
    setLoading(true);
    setRequestError(null);
    setFailedRun(null);
    setRunningRunId(null);
    setStoredRun(null);
    setTraceError(null);
    setTraceLoading(false);
    setLinkCopied(false);
    setResponse(null);
    setHoveredKey(null);
    setSelectedKey(null);
    setFocusRequest(null);
    setPanelOpen(true);
    setMobileExpanded(false);

    try {
      const record = await getStoredAnalysis(runId);
      if (requestSequence.current !== sequence) return;
      setStoredRun(record);
      setQuestion(record.question);
      rememberRun(record.run_id, record.question);
      if (record.status === "succeeded") {
        if (!record.result) throw new Error("완료된 실행 기록에 분석 결과가 없습니다.");
        setResponse({ run_id: record.run_id, status: "succeeded", result: record.result, errors: [] });
      } else if (record.status === "failed") {
        setFailedRun({ runId: record.run_id, errors: record.provenance?.errors ?? [] });
      } else {
        setRunningRunId(record.run_id);
      }
    } catch (error) {
      if (requestSequence.current !== sequence) return;
      setRequestError(error instanceof Error ? error.message : String(error));
    } finally {
      if (requestSequence.current === sequence) setLoading(false);
    }
  }

  // URL 주소창 직접 진입 및 브라우저 뒤로/앞으로 이동 대응.
  useEffect(() => {
    if (DEMO_MODE) return;
    const restore = () => {
      const runId = new URLSearchParams(window.location.search).get("run");
      if (runId) void loadRun(runId);
      else {
        ++requestSequence.current;
        setLoading(false);
        setResponse(null);
        setStoredRun(null);
        setRequestError(null);
        setFailedRun(null);
        setRunningRunId(null);
        setHoveredKey(null);
        setSelectedKey(null);
        setFocusRequest(null);
        setTraceLoading(false);
        setTraceError(null);
        setLinkCopied(false);
      }
    };
    restore();
    window.addEventListener("popstate", restore);
    return () => {
      window.removeEventListener("popstate", restore);
      ++requestSequence.current;
    };
    // 초기 진입 및 브라우저 탐색에서만 호출한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openRecentRun(runId: string) {
    if (loading) return;
    updateRunUrl(runId);
    void loadRun(runId);
  }

  function removeRecentRun(runId: string) {
    setRecentRuns((previous) => previous.filter((entry) => entry.runId !== runId));
  }

  function refreshTrace(runId: string) {
    if (DEMO_MODE) return;
    void fetchTrace(runId, requestSequence.current);
  }

  async function copyRunLink(runId: string) {
    if (DEMO_MODE) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("demo");
    url.searchParams.set("run", runId);
    try {
      await navigator.clipboard.writeText(url.toString());
      setLinkCopied(true);
    } catch {
      setLinkCopied(false);
      setTraceError("링크를 복사하지 못했습니다. 주소창의 ?run=... 링크를 직접 복사해주세요.");
    }
  }

  async function runAnalysis(text: string) {
    if (!text || loading) return;

    setPanelOpen(true);
    setMobileExpanded(false);
    setLoading(true);
    setRequestError(null);
    setFailedRun(null);
    setRunningRunId(null);
    setStoredRun(null);
    setTraceError(null);
    setTraceLoading(false);
    setLinkCopied(false);
    const sequence = ++requestSequence.current;

    try {
      const next = DEMO_MODE ? await loadDemoAnalysis() : await requestAnalysis(text);
      if (requestSequence.current !== sequence) return;

      if (next.status === "succeeded") {
        if (!next.result) throw new Error("서버가 성공 상태를 반환했지만 분석 결과가 없습니다.");
        setResponse(next);
        setHoveredKey(null);
        setSelectedKey(null);
        setFocusRequest(null);
        if (!DEMO_MODE) {
          updateRunUrl(next.run_id);
          rememberRun(next.run_id, next.result.question);
          void fetchTrace(next.run_id, sequence);
        }
      } else if (next.status === "failed") {
        // 실패해도 직전 성공 결과는 지도에 남긴다.
        setFailedRun({ runId: next.run_id, errors: next.errors });
        if (!DEMO_MODE) {
          updateRunUrl(next.run_id);
          rememberRun(next.run_id, text);
          void fetchTrace(next.run_id, sequence);
        }
      } else {
        // 현재 POST/GET 방식에서는 실제 단계별 진행 정보와 자동 폴링이 없다.
        setRunningRunId(next.run_id);
      }
    } catch (error) {
      if (requestSequence.current === sequence) {
        setRequestError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (requestSequence.current === sequence) setLoading(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void runAnalysis(question.trim());
  }

  // 지도 클릭은 지역만 선택하고, 순위표 클릭은 해당 지역까지 이동한다.
  function selectFromMap(key: string | null) {
    setSelectedKey(key);
    if (key !== null) {
      setPanelOpen(true);
      setMobileExpanded(false);
    }
  }

  function selectFromRanking(key: string) {
    setSelectedKey(key);
    setPanelOpen(true);
    setMobileExpanded(false);
    setFocusRequest((previous) => ({
      unitKey: key,
      sequence: (previous?.sequence ?? 0) + 1,
    }));
  }

  function clearSelection() {
    setSelectedKey(null);
    setFocusRequest(null);
  }

  // 화면에 표시 중인 결과만 초기화한다. 서버의 실행 기록은 삭제하지 않는다.
  function clearResult() {
    if (loading) return;
    setResponse(null);
    setRequestError(null);
    setFailedRun(null);
    setRunningRunId(null);
    setStoredRun(null);
    setTraceError(null);
    setTraceLoading(false);
    setLinkCopied(false);
    ++requestSequence.current;
    updateRunUrl(null);
    setHoveredKey(null);
    clearSelection();
    setPanelOpen(true);
    setMobileExpanded(false);
  }

  // 모바일에서는 지역을 선택했을 때 상세 카드를 먼저 보여준다.
  useEffect(() => {
    if (!selectedKey || !window.matchMedia("(max-width: 860px)").matches) return;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById("analysis-sidebar-content")?.scrollTo({
        top: 0,
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [selectedKey]);

  const showingPreviousResult = Boolean(result) &&
    (loading || requestError !== null || failedRun !== null || runningRunId !== null);

  return (
    <div className={`app map-first${!panelOpen ? " p6-panel-hidden" : mobileExpanded ? " p6-sheet-expanded" : ""}`}>
      <main className="map-canvas" aria-label="공간 분석 지도">
        {result ? (
          <ChoroplethMap
            result={result}
            scheme={scheme}
            hoveredKey={hoveredKey}
            onHover={setHoveredKey}
            selectedKey={selectedKey}
            onSelect={selectFromMap}
            focusRequest={focusRequest}
            panelOpen={panelOpen}
            panelExpanded={mobileExpanded}
          />
        ) : (
          <BaseMap scheme={scheme} />
        )}

        {result && (
          <div className="map-legend">
            <Legend layer={result.map_layer} scheme={scheme} />
          </div>
        )}
      </main>

      <div className="map-gradient" aria-hidden="true" />

      <header className="floating-header">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <img src="/MapGPT.png" alt="" />
          </div>
          <div className="p3-brand-text">
            <strong>MAPGPT</strong>
            <span>GEO SPATIAL AI</span>
          </div>
          {DEMO_MODE && <span className="p3-demo-label">데모 · 가상 수치</span>}
        </div>
        <div className="p4-header-actions">
          {(result || requestError || failedRun || runningRunId) && (
            <button
              type="button"
              className="panel-toggle p4-clear-button"
              onClick={clearResult}
              disabled={loading}
              title="현재 화면의 분석 결과만 초기화합니다"
            >
              결과 초기화
            </button>
          )}
          <button
            type="button"
            className="panel-toggle"
            onClick={() => setPanelOpen((open) => !open)}
            aria-controls="analysis-sidebar"
            aria-expanded={panelOpen}
          >
            {panelOpen ? "결과 접기" : "결과 보기"}
          </button>
        </div>
      </header>

      {panelOpen && (
        <aside id="analysis-sidebar" className={`result-panel${mobileExpanded ? " p6-expanded" : ""}`} aria-label="공간 분석 결과">
          <div className="result-panel-header">
            <div>
              <span className="eyebrow">AI ANALYSIS</span>
              <h2>공간 분석 결과</h2>
            </div>
            <div className="p6-panel-actions">
              <button
                type="button"
                className="p6-expand-button"
                onClick={() => setMobileExpanded((expanded) => !expanded)}
                aria-expanded={mobileExpanded}
                aria-controls="analysis-sidebar-content"
              >
                {mobileExpanded ? "축소" : "확장"}
              </button>
              <button
                type="button"
                className="panel-close"
                onClick={() => {
                  setPanelOpen(false);
                  setMobileExpanded(false);
                }}
                aria-label="결과 패널 닫기"
              >
                ×
              </button>
            </div>
          </div>

          <div id="analysis-sidebar-content" className="result-panel-content">
            {!DEMO_MODE && (
              <RecentRuns
                entries={recentRuns}
                currentRunId={result?.run_id ?? failedRun?.runId ?? runningRunId}
                disabled={loading}
                onOpen={openRecentRun}
                onRemove={removeRecentRun}
                onClear={() => setRecentRuns([])}
              />
            )}
            {loading && (
              <section className="card progress p3-feedback" role="status" aria-live="polite">
                <div className="spinner" aria-hidden="true" />
                <div>
                  <strong>분석 요청 중 · {elapsed}초</strong>
                  <p className="muted">서버의 분석 결과를 기다리고 있습니다. 처리 시간은 질문과 모델에 따라 달라집니다.</p>
                </div>
              </section>
            )}

            {requestError && (
              <section className="card error p3-feedback" role="alert">
                <h3>분석 요청에 실패했습니다</h3>
                <p>{requestError}</p>
                <button
                  type="button"
                  className="p3-retry"
                  onClick={() => void runAnalysis(question.trim())}
                  disabled={loading || !question.trim()}
                >
                  같은 질문 다시 시도
                </button>
              </section>
            )}

            {failedRun && (
              <div className="p3-feedback-group">
                <ErrorPanel errors={failedRun.errors} runId={failedRun.runId} />
                {!DEMO_MODE && (
                  <>
                    <button type="button" className="analysis-evidence-link" onClick={() => void copyRunLink(failedRun.runId)}>
                      {linkCopied ? "실패한 실행의 링크 복사 완료" : "실패한 실행의 링크 복사"}
                    </button>
                    <ExecutionTrace
                      provenance={storedRun?.run_id === failedRun.runId ? storedRun.provenance : null}
                      runId={failedRun.runId}
                      loading={traceLoading}
                      error={traceError}
                      demo={false}
                      onRefresh={() => refreshTrace(failedRun.runId)}
                    />
                  </>
                )}
                <button
                  type="button"
                  className="p3-retry"
                  onClick={() => void runAnalysis(question.trim())}
                  disabled={loading || !question.trim()}
                >
                  같은 질문 다시 시도
                </button>
              </div>
            )}

            {runningRunId && (
              <section className="card p3-feedback" role="status">
                <h3>서버에서 분석을 진행하고 있습니다</h3>
                <p>
                  서버가 진행 중 상태를 반환했습니다. 현재 화면은 실행 상태를 자동 조회하지 않습니다.
                  결과가 완료되면 다시 확인해야 합니다.
                </p>
                <p className="muted">
                  실행 기록: <a href={`/api/analyses/${encodeURIComponent(runningRunId)}`} target="_blank" rel="noreferrer">{runningRunId}</a>
                </p>
              </section>
            )}

            {showingPreviousResult && (
              <p className="p3-previous-result" role="status">
                아래 지도와 순위는 이전에 완료한 분석 결과입니다.
              </p>
            )}

            {!result && !loading && !requestError && !failedRun && !runningRunId && (
              <section className="card welcome-card">
                <h3>지도에서 무엇을 알아볼까요?</h3>
                <p className="muted">
                  질문을 입력하면 등록된 데이터를 바탕으로 공간 분석을 수행하고 지도·순위·근거를 보여줍니다.
                </p>
              </section>
            )}

            {result && (
              <AnalysisResultPanel
                key={result.run_id}
                result={result}
                scheme={scheme}
                hoveredKey={hoveredKey}
                onHover={setHoveredKey}
                selectedKey={selectedKey}
                onSelect={selectFromRanking}
                onClearSelection={clearSelection}
                provenance={storedRun?.run_id === result.run_id ? storedRun.provenance : null}
                traceLoading={traceLoading}
                traceError={traceError}
                demo={DEMO_MODE}
                onRefreshTrace={() => refreshTrace(result.run_id)}
                onCopyLink={() => void copyRunLink(result.run_id)}
                linkCopied={linkCopied}
              />
            )}
          </div>
        </aside>
      )}

      <form className="prompt-dock" onSubmit={submit} aria-label="공간 분석 질문 입력">
        <div className="prompt-shell">
          <span className="prompt-icon" aria-hidden="true">✦</span>
            <input
              aria-label="공간 분석 질문"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={EXAMPLE}
              maxLength={500}
            />
          <button type="submit" disabled={loading || !question.trim()}>
            {loading ? `분석 중 ${elapsed}초` : DEMO_MODE ? "데모 다시 보기" : "분석"}
          </button>
        </div>
        <p className="prompt-hint">
          {DEMO_MODE
            ? "데모 모드: 지도와 분석 수치는 시연용이며 실제 분석 결과가 아닙니다."
            : "질문을 입력하면 데이터 기반 공간 분석 결과를 지도로 보여줍니다."}
        </p>
      </form>
    </div>
  );
}
