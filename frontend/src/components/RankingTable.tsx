import { useMemo, useState } from "react";
import { type ColorScheme, MAP_CHROME, classColors, classIndex, formatValue, layerStyle } from "../theme";
import type { FinalResponse, UnitProperties } from "../types";

interface Props {
  result: FinalResponse;
  scheme: ColorScheme;
  hoveredKey: string | null;
  onHover: (key: string | null) => void;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}

interface Row {
  rank: number | null;
  properties: UnitProperties;
}

/**
 * 순위 막대 목록. 막대 길이는 지표 값(지금 보이는 행의 최댓값 기준)이고, 색은 지도 단계 구분도와 같은 구간 색이다.
 * 다른 지표 값은 지역을 누르면 선택 카드와 지도 툴팁에 보인다.
 */
export function RankingTable({ result, scheme, hoveredKey, onHover, selectedKey, onSelect }: Props) {
  const [showAll, setShowAll] = useState(false);
  const metric = result.metric.name;
  const rankMetric = result.rank_metric;
  const { classification } = result.map_layer;
  const style = layerStyle(result.map_layer);

  // 응답에 이미 포함된 순위를 정렬에만 이용한다. 순위를 새로 계산하지 않는다.
  const allRows = useMemo<Row[]>(() => {
    const rows = result.map_layer.geojson.features.map((feature) => ({
      rank: rankMetric ? (feature.properties[rankMetric] as number | null) : null,
      properties: feature.properties,
    }));
    return rows.sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity) || a.properties.unit_key.localeCompare(b.properties.unit_key));
  }, [result, rankMetric]);
  const rows = showAll ? allRows : allRows.slice(0, result.ranking.length);
  // 응답에 공간 단위 이름이 없어 '지역'으로 쓰고, 순위 이름(예: "부족 순위")은 순위 Operation 라벨을 쓴다.
  const rankLabel = (rankMetric && result.metric_labels[rankMetric]) || "순위";

  const valueOf = (properties: UnitProperties) => {
    const value = properties[metric];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };
  const max = Math.max(0, ...rows.map((row) => valueOf(row.properties) ?? 0));
  const colors = classColors(scheme, classification.class_count, style.emphasis);

  return (
    <section className="card">
      <div className="card-header">
        <h2>{showAll ? `전체 ${allRows.length}개 지역` : `${rankLabel} 상위 ${result.ranking.length}개 지역`}</h2>
        <button type="button" className="link-button" onClick={() => setShowAll((value) => !value)}>
          {showAll ? "상위만 보기" : "전체 순위 보기"}
        </button>
      </div>
      {/* 막대가 짧을수록 범례의 낮은 쪽이다 (예: ← 공급 부족 · 여유 →) */}
      <div className="rank-ends" aria-hidden="true">
        <span>
          <span>← {style.legend_low}</span>
          <span>{style.legend_high} →</span>
        </span>
      </div>
      <ol className={showAll ? "rank-bars rank-bars-all" : "rank-bars"} aria-label={`${rankLabel} 목록`}>
        {rows.map(({ rank, properties }) => {
          const value = valueOf(properties);
          const selected = selectedKey === properties.unit_key;
          const color = value === null
            ? MAP_CHROME[scheme].noData
            : colors[Math.min(classIndex(value, classification.breaks), colors.length - 1)];
          return (
            <li key={properties.unit_key}>
              <button
                type="button"
                className={selected ? "rank-row selected" : properties.unit_key === hoveredKey ? "rank-row hovered" : "rank-row"}
                aria-pressed={selected}
                title={`${properties.unit_name} 지도에서 찾기`}
                onMouseEnter={() => onHover(properties.unit_key)}
                onMouseLeave={() => onHover(null)}
                onClick={() => onSelect(properties.unit_key)}
              >
                <span className="rank-no">{rank ?? "–"}</span>
                <span className="rank-place">
                  {properties.parent_name && <span className="muted">{properties.parent_name} </span>}
                  {properties.unit_name}
                </span>
                <span className="rank-bar" aria-hidden="true">
                  <span style={{ width: `${value !== null && max > 0 ? (Math.max(0, value) / max) * 100 : 0}%`, background: color }} />
                </span>
                {/* 값 없는 사유는 지역을 누르면 선택 카드에 보인다 */}
                <span className="rank-value">{value === null ? "값 없음" : formatValue(value)}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
