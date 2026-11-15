import {
  type ColorScheme,
  HEAT_COLORS,
  MAP_CHROME,
  classColors,
  formatValue,
  layerStyle,
  symbolMaxRadius,
} from "../theme";
import type { MapLayer, MapView } from "../types";

const VIEW_LABELS: Record<MapView, { label: string; title: string }> = {
  proportional_symbol: { label: "원 크기", title: "비례 원: 값에 비례한 넓이의 원" },
  choropleth: { label: "색 구간", title: "단계 구분도: 값 구간별 색으로 칠하기" },
  point: { label: "점", title: "점 지도: 하나하나의 위치 (가까운 곳은 묶어 개수로 보인다)" },
  heatmap: { label: "밀도", title: "밀도 지도: 점이 몰린 정도를 색 번짐으로" },
};

interface Props {
  layer: MapLayer;
  scheme: ColorScheme;
  view: MapView;
  onViewChange: (view: MapView) => void;
}

/** 지도 범례와 보기 전환. 바꿔 볼 수 있는 보기가 둘 이상일 때만 전환 버튼과 그 보기를 고른 이유를 보인다. */
export function Legend({ layer, scheme, view, onViewChange }: Props) {
  const style = layerStyle(layer);
  return (
    <div className="legend" aria-label="지도 범례">
      {style.views.length > 1 && (
        <div className="view-switch" role="group" aria-label="지도 보기">
          {style.views.map((option) => (
            <button
              key={option}
              type="button"
              className={option === view ? "on" : ""}
              aria-pressed={option === view}
              title={VIEW_LABELS[option].title}
              onClick={() => onViewChange(option)}
            >
              {VIEW_LABELS[option].label}
            </button>
          ))}
        </div>
      )}
      {view === "proportional_symbol" && <SymbolScale layer={layer} scheme={scheme} />}
      {view === "choropleth" && <ClassScale layer={layer} scheme={scheme} />}
      {view === "point" && <PointScale layer={layer} scheme={scheme} />}
      {view === "heatmap" && <HeatScale layer={layer} scheme={scheme} />}
      {layer.null_count > 0 && (view === "choropleth" || view === "proportional_symbol") && (
        <div className="legend-class">
          <span className="swatch" style={{ background: MAP_CHROME[scheme].noData }} />
          <span className="legend-range">값 없음 {layer.null_count}개</span>
        </div>
      )}
      {style.views.length > 1 && style.note && <div className="legend-note">{style.note}</div>}
    </div>
  );
}

/** 분위 구간 범례. 구간은 [경계값 이상, 다음 경계값 미만)이다 (MapLibre step 식과 같음). */
function ClassScale({ layer, scheme }: { layer: MapLayer; scheme: ColorScheme }) {
  const { classification } = layer;
  const style = layerStyle(layer);
  const colors = classColors(scheme, classification.class_count, style.emphasis);
  const breaks = classification.breaks;
  const label = (i: number) => {
    if (breaks.length === 0) return `${formatValue(classification.min)}~${formatValue(classification.max)}`;
    if (i === 0) return `${formatValue(breaks[0])} 미만`;
    if (i === breaks.length) return `${formatValue(breaks[i - 1])} 이상`;
    return `${formatValue(breaks[i - 1])}~${formatValue(breaks[i])}`;
  };
  return (
    <>
      <div className="legend-title">
        {layer.metric_label} <span className="muted">(분위 {colors.length}구간)</span>
      </div>
      <div className="legend-scale">
        {colors.map((color, i) => (
          <div key={color + i} className="legend-class">
            <span className="swatch" style={{ background: color }} />
            <span className="legend-range">{label(i)}</span>
          </div>
        ))}
      </div>
      <div className="legend-ends">
        <span>← {style.legend_low}</span>
        <span>{style.legend_high} →</span>
      </div>
    </>
  );
}

// 범례에 보일 원 크기 예시 값: 최댓값과 그 1/4, 1/16 (원 넓이가 값에 비례하므로 반지름은 1/2, 1/4).
function sampleValues(max: number): number[] {
  const round = (value: number) => (max >= 10 ? Math.round(value) : Number(value.toPrecision(2)));
  return [...new Set([max, round(max / 4), round(max / 16)])].filter((value) => value > 0);
}

/** 비례 원 범례: 같은 바닥선에 겹쳐 그린 원과, 원 꼭대기에서 오른쪽으로 뺀 값 표시. */
function SymbolScale({ layer, scheme }: { layer: MapLayer; scheme: ColorScheme }) {
  const max = layer.classification.max ?? 0;
  const radius = symbolMaxRadius(layer.feature_count);
  const base = 4 + radius * 2;
  return (
    <>
      <div className="legend-title">
        {layer.metric_label} <span className="muted">(원 넓이)</span>
      </div>
      {max > 0 && (
        <svg className="legend-symbols" width={radius * 2 + 64} height={base + 2} aria-hidden="true">
          {sampleValues(max).map((value) => {
            const r = radius * Math.sqrt(value / max);
            const top = base - 2 * r;
            return (
              <g key={value}>
                <circle cx={radius} cy={base - r} r={r} fill="none" stroke={MAP_CHROME[scheme].symbol} />
                <line x1={radius} y1={top} x2={radius * 2 + 8} y2={top} />
                <text x={radius * 2 + 11} y={top + 4}>{formatValue(value)}</text>
              </g>
            );
          })}
        </svg>
      )}
    </>
  );
}

function PointsTitle({ layer, detail }: { layer: MapLayer; detail?: string }) {
  const count = `분석 지역 안 ${(layer.points?.count ?? 0).toLocaleString("ko-KR")}곳`;
  return (
    <div className="legend-title">
      {layer.points?.title} <span className="muted">({detail ? `${detail}, ${count}` : count})</span>
    </div>
  );
}

/** 점 지도 범례: 한 곳을 뜻하는 점과, 가까운 곳을 묶은 원(숫자는 곳 수). */
function PointScale({ layer, scheme }: { layer: MapLayer; scheme: ColorScheme }) {
  const chrome = MAP_CHROME[scheme];
  return (
    <>
      <PointsTitle layer={layer} />
      <div className="legend-class">
        <svg className="legend-mark" width="22" height="22" aria-hidden="true">
          <circle cx="11" cy="11" r="4" fill={chrome.symbol} stroke={chrome.separator} />
        </svg>
        <span className="legend-range">한 곳</span>
      </div>
      <div className="legend-class">
        <svg className="legend-mark" width="22" height="22" aria-hidden="true">
          <circle cx="11" cy="11" r="10" fill={chrome.symbol} fillOpacity={0.85} />
          <text x="11" y="14.5" textAnchor="middle" fill={chrome.symbolText}>12</text>
        </svg>
        <span className="legend-range">가까운 곳 묶음 (숫자는 곳 수, 누르면 확대)</span>
      </div>
    </>
  );
}

/** 밀도 지도 범례: 투명(점 없음)에서 가장 밀집한 색까지의 띠. 밀도는 화면 배율에 따라 상대적이다. */
function HeatScale({ layer, scheme }: { layer: MapLayer; scheme: ColorScheme }) {
  return (
    <>
      <PointsTitle layer={layer} detail="밀도" />
      <div
        className="legend-heat"
        style={{ background: `linear-gradient(to right, transparent, ${HEAT_COLORS[scheme].join(", ")})` }}
      />
      <div className="legend-ends">
        <span>← 드문 곳</span>
        <span>밀집한 곳 →</span>
      </div>
      <div className="legend-note">색은 지금 화면 배율에서의 상대 밀도라 확대·축소하면 달라집니다.</div>
    </>
  );
}
