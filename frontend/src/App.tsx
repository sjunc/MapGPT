import { type FormEvent, useEffect, useRef, useState } from "react";
import { apiUrl, followAnalysisJob, getStoredAnalysis, runAnalysisJob } from "./api";
import type { JobProgress, StoredAnalysis } from "./api";
import { JobProgressPanel } from "./components/JobProgressPanel";
import { RecentRuns } from "./components/RecentRuns";
import { ExecutionTrace } from "./components/ExecutionTrace";
import { addRecentRun, readRecentRuns, saveRecentRuns } from "./runHistory";
import type { RecentRun } from "./runHistory";
import { BaseMap } from "./components/BaseMap";
import { ChoroplethMap } from "./components/ChoroplethMap";
import { KakaoMap } from "./components/KakaoMap";
import { AnalysisResultPanel } from "./components/AnalysisResultPanel";
import { ErrorPanel } from "./components/ErrorPanel";
import { Legend } from "./components/Legend";
import { SuggestionChips } from "./components/SuggestionChips";
import { loadDemoAnalysis } from "./demo";
import { SCOPE_HINTS, SCOPE_LABELS, SCOPES, readScope, saveScope } from "./scope";
import { type ColorScheme, layerStyle, useColorScheme } from "./theme";
import type { AnalysisResponse, AnalysisScope, MapView, WorkflowError } from "./types";
import type { FocusRequest } from "./components/ChoroplethMap";
import "./p3.css";
import "./p4.css";
import "./p5.css";
import "./p6.css";
import "./p7.css";
import "./p8.css";

const EXAMPLE = "서울에서 어린이 인구 대비 어린이집이 부족한 행정동을 알려줘.";
// map=kakao면 지도만 카카오 지도로 바꾸고 나머지 화면은 같다.
// 카카오 지도에는 다크 배경지도가 없어 화면을 밝은 테마로 고정한다 (styles.css의 data-theme).
const KAKAO_MAP = new URLSearchParams(window.location.search).get("map") === "kakao";
if (KAKAO_MAP) document.documentElement.dataset.theme = "light";

type FailedRun = { runId: string; errors: WorkflowError[] };

/** 주소가 화면을 정한다. run이 있으면 그 실행, 없고 demo=1이면 가상 데모, 둘 다 없으면 빈 지도다. */
function readRoute(): { runId: string | null; demo: boolean } {
  const query = new URLSearchParams(window.location.search);
  const runId = query.get("run") || null;
  return { runId, demo: runId === null && query.get("demo") === "1" };
}

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
  const systemScheme = useColorScheme();
  const scheme: ColorScheme = KAKAO_MAP ? "light" : systemScheme;
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState<AnalysisScope>(readScope);
  const [panelOpen, setPanelOpen] = useState(true);
  const [mobileExpanded, setMobileExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<AnalysisResponse | null>(null);
  // 지금 보이는 결과가 가상 데모인지 (주소 ?demo=1)
  const [demo, setDemo] = useState(false);
  // 숨긴 "가상 데모로 돌아가기" 버튼. 로고를 빠르게 세 번 누르면 보인다.
  const [demoReturnShown, setDemoReturnShown] = useState(false);
  const logoClicks = useRef<number[]>([]);
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
  const [viewChoice, setViewChoice] = useState<{ runId: string; view: MapView } | null>(null);
  // 늦게 도착한 이전 요청 응답이 새 지도를 덮어쓰지 않도록 구분한다.
  const requestSequence = useRef(0);
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const jobAbort = useRef<AbortController | null>(null);
  const elapsed = useElapsedSeconds(loading);
  const result = response?.result ?? null;
  // 결과마다 백엔드가 지표 성격으로 정한 기본 보기로 시작하고, 사용자가 바꾼 보기는 그 결과에만 쓴다.
  const view: MapView = result && viewChoice?.runId === result.run_id
    ? viewChoice.view
    : result ? layerStyle(result.map_layer).default_view : "choropleth";
  const changeView = (next: MapView) => {
    if (result) setViewChoice({ runId: result.run_id, view: next });
  };

  useEffect(() => {
    saveRecentRuns(recentRuns);
  }, [recentRuns]);

  function rememberRun(runId: string, text: string) {
    setRecentRuns((previous) => addRecentRun(previous, runId, text));
  }

  function updateRunUrl(runId: string | null, replace = false) {
    const url = new URL(window.location.href);
    url.searchParams.delete("demo"); // 실행을 열거나 결과를 지우면 가상 데모 주소에서 벗어난다
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
    setDemo(false);
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
        // 진행 중인 실행은 끝날 때까지 단계 진행을 따라간 뒤 결과를 보인다.
        const controller = new AbortController();
        jobAbort.current = controller;
        const next = await followAnalysisJob(record.run_id, {
          signal: controller.signal,
          onProgress: (value) => {
            if (requestSequence.current === sequence) setProgress(value);
          },
        });
        if (requestSequence.current !== sequence) return;
        if (next.status === "succeeded" && next.result) setResponse(next);
        else if (next.status === "failed") setFailedRun({ runId: next.run_id, errors: next.errors });
        else setRunningRunId(record.run_id);
        void fetchTrace(record.run_id, sequence);
      }
    } catch (error) {
      if (requestSequence.current !== sequence) return;
      setRequestError(error instanceof Error ? error.message : String(error));
    } finally {
      if (requestSequence.current === sequence) {
        setLoading(false);
        setProgress(null);
        jobAbort.current = null;
      }
    }
  }

  // 가상 데모(public/demo/analysis.json)를 보인다. 서버 없이도 보이고, 여기서 질문하면 실제 분석을 요청한다.
  async function showDemo() {
    const sequence = ++requestSequence.current;
    setLoading(false);
    setRequestError(null);
    setFailedRun(null);
    setRunningRunId(null);
    setStoredRun(null);
    setTraceError(null);
    setTraceLoading(false);
    setLinkCopied(false);
    setResponse(null);
    setDemo(false);
    setHoveredKey(null);
    setSelectedKey(null);
    setFocusRequest(null);
    setPanelOpen(true);
    setMobileExpanded(false);

    try {
      const data = await loadDemoAnalysis();
      if (requestSequence.current !== sequence) return;
      setResponse(data);
      setDemo(true);
    } catch (error) {
      if (requestSequence.current !== sequence) return;
      setRequestError(error instanceof Error ? error.message : String(error));
    }
  }

  // URL 주소창 직접 진입 및 브라우저 뒤로/앞으로 이동 대응.
  useEffect(() => {
    const restore = () => {
      const route = readRoute();
      if (route.runId) void loadRun(route.runId);
      else if (route.demo) void showDemo();
      else {
        ++requestSequence.current;
        setLoading(false);
        setResponse(null);
        setDemo(false);
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

  // 숨긴 "가상 데모로 돌아가기": 로고를 1초 안에 세 번 누르면 버튼이 나타나고, 다시 세 번 누르면 숨는다.
  function knockLogo() {
    const now = performance.now();
    logoClicks.current = [...logoClicks.current.filter((time) => now - time < 1000), now];
    if (logoClicks.current.length < 3) return;
    logoClicks.current = [];
    setDemoReturnShown((shown) => !shown);
  }

  function returnToDemo() {
    if (loading) return;
    setDemoReturnShown(false);
    const url = new URL(window.location.href);
    url.searchParams.delete("run");
    url.searchParams.set("demo", "1");
    window.history.pushState(null, "", url);
    void showDemo();
  }

  function refreshTrace(runId: string) {
    if (demo) return;
    void fetchTrace(runId, requestSequence.current);
  }

  async function copyRunLink(runId: string) {
    if (demo) return;
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

  async function runAnalysis(text: string, analysisScope: AnalysisScope = scope) {
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
    setProgress(null);
    // 가상 데모에서 질문하면 데모를 지우고 실제 분석 결과만 보인다 (데모는 "이전에 완료한 분석 결과"가 아니다).
    if (demo) {
      setResponse(null);
      setDemo(false);
      setHoveredKey(null);
      setSelectedKey(null);
      setFocusRequest(null);
    }
    const sequence = ++requestSequence.current;
    const controller = new AbortController();
    jobAbort.current = controller;

    try {
      const next = await runAnalysisJob(text, analysisScope, {
        signal: controller.signal,
        // 시작하자마자 주소와 최근 실행에 남겨, 새로고침해도 진행 중인 실행을 다시 따라갈 수 있게 한다.
        onStarted: (runId) => {
          if (requestSequence.current !== sequence) return;
          updateRunUrl(runId);
          rememberRun(runId, text);
        },
        onProgress: (value) => {
          if (requestSequence.current === sequence) setProgress(value);
        },
      });
      if (requestSequence.current !== sequence) return;

      if (next.status === "succeeded") {
        if (!next.result) throw new Error("서버가 성공 상태를 반환했지만 분석 결과가 없습니다.");
        setResponse(next);
        setHoveredKey(null);
        setSelectedKey(null);
        setFocusRequest(null);
        rememberRun(next.run_id, next.result.question);
        void fetchTrace(next.run_id, sequence);
      } else if (next.status === "failed") {
        // 실패해도 직전 성공 결과는 지도에 남긴다.
        setFailedRun({ runId: next.run_id, errors: next.errors });
        void fetchTrace(next.run_id, sequence);
      } else {
        setRunningRunId(next.run_id);
      }
    } catch (error) {
      if (requestSequence.current === sequence) {
        setRequestError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (requestSequence.current === sequence) {
        setLoading(false);
        setProgress(null);
        jobAbort.current = null;
      }
    }
  }

  function cancelAnalysis() {
    jobAbort.current?.abort();
  }

  // 예시 질문을 누르면 입력창을 채우고 바로 분석한다.
  function askSuggestion(text: string) {
    setQuestion(text);
    void runAnalysis(text);
  }

  // 분석 범위 밖 지역이라 실패했을 때 범위를 전국으로 바꾸고 같은 질문을 다시 분석한다.
  function retryInNationalScope() {
    setScope("national");
    saveScope("national");
    void runAnalysis(question.trim(), "national");
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
    setDemo(false);
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
  // 통계 카드의 분석 범위: 그 실행의 범위. 실행 기록이 아직 오지 않았으면 범위 토글 값이고, 가상 데모는 서울 데이터다.
  const resultScope: AnalysisScope = demo
    ? "seoul"
    : (storedRun?.run_id === result?.run_id ? storedRun?.provenance?.scope : undefined) ?? scope;

  return (
    <div className={`app map-first${!panelOpen ? " p6-panel-hidden" : mobileExpanded ? " p6-sheet-expanded" : ""}`}>
      <main className="map-canvas" aria-label="공간 분석 지도">
        {KAKAO_MAP ? (
          <KakaoMap
            result={result}
            view={view}
            hoveredKey={hoveredKey}
            onHover={setHoveredKey}
            selectedKey={selectedKey}
            onSelect={selectFromMap}
            focusRequest={focusRequest}
            panelOpen={panelOpen}
            panelExpanded={mobileExpanded}
          />
        ) : result ? (
          <ChoroplethMap
            result={result}
            view={view}
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
            <Legend layer={result.map_layer} scheme={scheme} view={view} onViewChange={changeView} />
          </div>
        )}
      </main>

      <div className="map-gradient" aria-hidden="true" />

      <header className="floating-header">
        {/* 로고를 빠르게 세 번 누르면 숨긴 "가상 데모로 돌아가기" 버튼이 데모 표시 자리에 나타난다 (팀 시연용). */}
        <div className="brand" onClick={knockLogo}>
          <div className="brand-mark" aria-hidden="true">
            <img src="/MapGPT.png" alt="" />
          </div>
          <div className="p3-brand-text">
            <strong>MAPGPT</strong>
            <span>GEO SPATIAL AI</span>
          </div>
          {demo && <span className="p3-demo-label">데모 · 가상 수치</span>}
          {!demo && demoReturnShown && !loading && (
            <button
              type="button"
              className="p3-demo-label p3-demo-return"
              onClick={(event) => {
                event.stopPropagation();
                returnToDemo();
              }}
            >
              가상 데모로 돌아가기
            </button>
          )}
        </div>
        <div className="p4-header-actions">
          <div className="p8-scope" role="radiogroup" aria-label="분석 범위">
            <span className="p8-scope-label" aria-hidden="true">분석 범위</span>
            {SCOPES.map((option) => (
              <button
                key={option}
                type="button"
                role="radio"
                aria-checked={scope === option}
                onClick={() => {
                  setScope(option);
                  saveScope(option);
                }}
                disabled={loading}
                title={SCOPE_HINTS[option]}
              >
                {SCOPE_LABELS[option]}
              </button>
            ))}
          </div>
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
            <RecentRuns
              entries={recentRuns}
              currentRunId={result?.run_id ?? failedRun?.runId ?? runningRunId}
              disabled={loading}
              onOpen={openRecentRun}
              onRemove={removeRecentRun}
              onClear={() => setRecentRuns([])}
            />
            {loading && !progress && (
              <section className="card progress p3-feedback" role="status" aria-live="polite">
                <div className="spinner" aria-hidden="true" />
                <div>
                  <strong>분석 요청 중 · {elapsed}초</strong>
                  <p className="muted">서버의 분석 결과를 기다리고 있습니다. 처리 시간은 질문과 모델에 따라 달라집니다.</p>
                </div>
              </section>
            )}
            {loading && progress && (
              <JobProgressPanel progress={progress} elapsed={elapsed} onCancel={cancelAnalysis} />
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
                <ErrorPanel
                  errors={failedRun.errors}
                  runId={failedRun.runId}
                  onUseNationalScope={scope === "seoul" && question.trim() ? retryInNationalScope : undefined}
                />
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
                <button
                  type="button"
                  className="p3-retry"
                  onClick={() => void runAnalysis(question.trim())}
                  disabled={loading || !question.trim()}
                >
                  같은 질문 다시 시도
                </button>
                <SuggestionChips scope={scope} disabled={loading} onPick={askSuggestion} />
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
                  실행 기록: <a href={apiUrl(`/api/analyses/${encodeURIComponent(runningRunId)}`)} target="_blank" rel="noreferrer">{runningRunId}</a>
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
                <SuggestionChips scope={scope} disabled={loading} onPick={askSuggestion} />
              </section>
            )}

            {result && (
              <AnalysisResultPanel
                key={result.run_id}
                result={result}
                mapView={view}
                onMapViewChange={changeView}
                scheme={scheme}
                hoveredKey={hoveredKey}
                onHover={setHoveredKey}
                selectedKey={selectedKey}
                onSelect={selectFromRanking}
                onClearSelection={clearSelection}
                provenance={storedRun?.run_id === result.run_id ? storedRun.provenance : null}
                traceLoading={traceLoading}
                traceError={traceError}
                demo={demo}
                scope={resultScope}
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
            <input
              aria-label="공간 분석 질문"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={EXAMPLE}
              maxLength={500}
            />
          <button type="submit" disabled={loading || !question.trim()}>
            {loading ? `분석 중 ${elapsed}초` : "분석"}
          </button>
        </div>
        <p className="prompt-hint">
          {demo
            ? "지도와 수치는 시연용 가상 데이터입니다. 질문을 입력하면 실제 분석을 요청합니다."
            : "질문을 입력하면 데이터 기반 공간 분석 결과를 지도로 보여줍니다."}
        </p>
      </form>
    </div>
  );
}
