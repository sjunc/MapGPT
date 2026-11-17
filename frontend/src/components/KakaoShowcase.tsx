import { useEffect, useMemo, useState } from "react";
import { loadDemoAnalysis } from "../demo";
import type { FinalResponse } from "../types";
import { formatValue } from "../theme";
import {
  BIVARIATE_COLORS,
  KakaoShowcaseMap,
  type ShowcaseFeature,
  type ShowcaseView,
} from "./KakaoShowcaseMap";
import "./kakao-showcase.css";

const VIEWS: Array<{ id: ShowcaseView; label: string; icon: string; explanation: string }> = [
  { id: "choropleth", label: "단계 구분도", icon: "▦", explanation: "지역별 공급 지표를 5단계 색상으로 비교" },
  { id: "bubble", label: "비례 원", icon: "◉", explanation: "가상 시설 개수를 원의 크기로 비교" },
  { id: "point", label: "시설 위치", icon: "●", explanation: "지역 내부의 샘플 위치를 마커로 표시" },
  { id: "bivariate", label: "이변량", icon: "▥", explanation: "어린이 인구와 시설 수를 함께 시각화" },
];

function metric(feature: ShowcaseFeature | null, key: string): number | null {
  if (!feature) return null;
  const value = feature.properties[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function MapLegend({ view, result }: { view: ShowcaseView; result: FinalResponse }) {
  const layer = result.map_layer;
  return (
    <div className="kshow-legend">
      <div className="kshow-legend-head">
        <span>MAP LEGEND</span>
        <strong>{VIEWS.find((item) => item.id === view)?.label}</strong>
      </div>
      {view === "choropleth" && (
        <>
          <div className="kshow-swatches">
            {["#124b88", "#2677b8", "#56a0d2", "#94c7e5", "#cee5f2"].map((color, i) => (
              <div key={color} title={`${i + 1}단계`} style={{ background: color }} />
            ))}
          </div>
          <div className="kshow-legend-ends"><span>공급 부족 (작은 값)</span><span>큰 값</span></div>
          <small>가상 수치 · 분위 구간은 정적 데모 JSON 기준</small>
        </>
      )}
      {view === "bubble" && (
        <>
          <div className="kshow-bubble-scale"><i /><i /><i /><span>원 넓이 ∝ 시설 개수</span></div>
          <small>상위 85개 법정동 표본만 표시 · 가상 시설 개수</small>
        </>
      )}
      {view === "point" && (
        <>
          <div className="kshow-point-scale"><span>📍</span> 샘플 위치 마커</div>
          <small>실제 어린이집 좌표가 아니라 법정동 내부에서 만든 가상 위치</small>
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
            <div><strong>가로</strong> 어린이 인구<br /><strong>세로</strong> 시설 개수</div>
          </div>
          <small>두 가상 지표를 각각 3단계로 구분한 예시</small>
        </>
      )}
      <div className="kshow-legend-footer">
        <span>지역 {layer.geojson.features.length.toLocaleString("ko-KR")}개</span>
        <span>EPSG:4326</span>
      </div>
    </div>
  );
}

export function KakaoShowcase() {
  const [result, setResult] = useState<FinalResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [view, setView] = useState<ShowcaseView>("choropleth");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<{ key: string; sequence: number } | null>(null);
  const [resetSequence, setResetSequence] = useState(0);

  useEffect(() => {
    let active = true;
    loadDemoAnalysis()
      .then((data) => {
        if (active && data.result) setResult(data.result);
      })
      .catch((cause: unknown) => {
        if (active) setLoadError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => { active = false; };
  }, []);

  const featureIndex = useMemo(() => {
    const index = new Map<string, ShowcaseFeature>();
    for (const feature of result?.map_layer.geojson.features ?? []) {
      index.set(feature.properties.unit_key, feature as ShowcaseFeature);
    }
    return index;
  }, [result]);

  const activeFeature = featureIndex.get(selectedKey ?? "") ?? null;
  const chosenView = VIEWS.find((item) => item.id === view)!;
  const ranking = result?.ranking.slice(0, 8) ?? [];

  function focusRegion(key: string) {
    setSelectedKey(key);
    setFocusRequest((previous) => ({ key, sequence: (previous?.sequence ?? 0) + 1 }));
  }

  return (
    <div className="kshow-root">
      <main className="kshow-stage" aria-label="카카오지도 기반 분석 시각화 데모">
        {result ? (
          <KakaoShowcaseMap
            result={result}
            view={view}
            selectedKey={selectedKey}
            hoveredKey={hoveredKey}
            onHover={setHoveredKey}
            onSelect={setSelectedKey}
            focusRequest={focusRequest}
            resetSequence={resetSequence}
          />
        ) : (
          <div className="kshow-map-status" role={loadError ? "alert" : "status"}>
            {loadError ?? "시연용 분석 데이터를 불러오는 중…"}
          </div>
        )}

        <div className="kshow-topbar">
          <div className="kshow-brand">
            <img src="/MapGPT.png" alt="" />
            <div><strong>MAPGPT</strong><small>KAKAO MAP SHOWCASE</small></div>
          </div>
          <span className="kshow-demo-tag">● LIVE UI · MOCK DATA</span>
        </div>

        <nav className="kshow-view-switch" aria-label="지도 시각화 유형">
          {VIEWS.map((item) => (
            <button
              type="button"
              key={item.id}
              className={view === item.id ? "kshow-view active" : "kshow-view"}
              onClick={() => { setView(item.id); setHoveredKey(null); }}
              aria-pressed={view === item.id}
              title={item.explanation}
            >
              <span aria-hidden="true">{item.icon}</span>{item.label}
            </button>
          ))}
        </nav>

        <div className="kshow-map-actions">
          <button type="button" onClick={() => setResetSequence((n) => n + 1)}>↺ 서울 전체 보기</button>
          <button type="button" onClick={() => { setSelectedKey(null); setHoveredKey(null); }}>선택 해제</button>
        </div>

        {result && <MapLegend view={view} result={result} />}
        <div className="kshow-corner-note">2015년 법정동 경계 · 가상 통계 · 실제 분석 결과 아님</div>
      </main>

      <aside className="kshow-aside" aria-label="시연 분석 정보">
        <div className="kshow-aside-header">
          <span className="kshow-kicker">VISUALIZATION LAB / 01</span>
          <h1>공간 분석 미리보기</h1>
          <p>카카오지도 위에서 분석 결과를 표현하는 시연 화면이야. 백엔드 연결 없이 동작해.</p>
          <div className="kshow-stats">
            <div><small>분석 범위</small><strong>서울</strong></div>
            <div><small>시각화</small><strong>4종</strong></div>
            <div><small>데이터</small><strong>가상</strong></div>
          </div>
        </div>

        <div className="kshow-aside-content">
          <section className="kshow-panel kshow-prompt">
            <span className="kshow-kicker">DEMO QUESTION</span>
            <h2>“서울에서 어린이 인구 대비 어린이집이 부족한 지역은?”</h2>
            <p>기존 정적 분석 결과의 질문이야. 버튼을 누르면 재분석하는 대신 지도 표현만 바뀌어.</p>
          </section>

          <section className="kshow-panel">
            <span className="kshow-kicker">CURRENT VIEW</span>
            <h2>{chosenView.label}</h2>
            <p>{chosenView.explanation}</p>
          </section>

          <section className="kshow-panel">
            <div className="kshow-section-title"><span className="kshow-kicker">SELECTED AREA</span><span>지도 또는 순위 클릭</span></div>
            {activeFeature && result ? (
              <>
                <h2 className="kshow-area-name">{activeFeature.properties.unit_name}</h2>
                <div className="kshow-highlight-number">
                  <strong>{formatValue(metric(activeFeature, result.map_layer.metric))}</strong>
                  <small>{result.map_layer.metric_label}</small>
                </div>
                <div className="kshow-details">
                  <div><span>어린이 인구 (가상)</span><b>{formatValue(metric(activeFeature, "children_count"))}명</b></div>
                  <div><span>어린이집 (가상)</span><b>{formatValue(metric(activeFeature, "daycare_count"))}개</b></div>
                  <div><span>지역 코드</span><b>{activeFeature.properties.unit_key}</b></div>
                </div>
              </>
            ) : (
              <p className="kshow-empty">지도의 색칠된 지역이나 아래 순위를 선택하면 상세 수치가 표시돼.</p>
            )}
          </section>

          <section className="kshow-panel">
            <div className="kshow-section-title"><span className="kshow-kicker">DEMO RANKING</span><span>가상 공급 지표 오름차순</span></div>
            <h2>공급 부족 예시 순위</h2>
            <div className="kshow-ranking">
              {ranking.map((item) => (
                <button
                  type="button"
                  key={item.unit_key}
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

          <section className="kshow-footer">
            <strong>DATA NOTICE</strong>
            <p>지도 경계: 2015년 법정동 GeoJSON. 수치와 시설 위치는 시연 목적의 가상값으로 실제 통계 또는 시설 좌표와 무관해.</p>
            <a href="/?demo=1">기존 MAPGPT 데모로 돌아가기 ↗</a>
          </section>
        </div>
      </aside>
    </div>
  );
}
