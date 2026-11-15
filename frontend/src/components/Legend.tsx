import { type ColorScheme, MAP_CHROME, classColors, formatValue, layerStyle, symbolMaxRadius } from "../theme";
import type { MapLayer, MapView } from "../types";

const VIEW_LABELS: Record<MapView, { label: string; title: string }> = {
  proportional_symbol: { label: "원 크기", title: "비례 원: 값에 비례한 넓이의 원" },
  choropleth: { label: "색 구간", title: "단계 구분도: 값 구간별 색으로 칠하기" },
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
      {view === "proportional_symbol" ? (
        <SymbolScale layer={layer} scheme={scheme} />
      ) : (
        <ClassScale layer={layer} scheme={scheme} />
      )}
      {layer.null_count > 0 && (
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
