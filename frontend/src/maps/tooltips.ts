/**
 * 지도 툴팁 내용. MapLibre 지도(ChoroplethMap)와 카카오 지도(KakaoMap)가 같은 내용을 보이도록 함께 쓴다.
 * 이스케이프한 HTML 문자열을 돌려준다.
 */
import type { FacilityProperties, FinalResponse, UnitProperties } from "../types";
import { formatValue, NULL_REASONS } from "../theme";

// LLM이 만든 문자열은 팝업 HTML에 삽입하기 전에 반드시 이스케이프한다.
function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export function tooltipHtml(result: FinalResponse, properties: UnitProperties): string {
  const { metric, metric_label } = result.map_layer;
  const rows = Object.entries(result.metric_labels)
    .filter(([name]) => name !== metric)
    .map(([name, label]) => `<tr><th>${escapeHtml(label)}</th><td>${formatValue(properties[name] as number | null)}</td></tr>`)
    .join("");
  const value = properties[metric] as number | null;
  const valueText = value === null ? (NULL_REASONS[properties.null_reason ?? ""] ?? "값 없음") : formatValue(value);
  const place = escapeHtml(`${properties.parent_name ?? ""} ${properties.unit_name}`);
  return `<div class="tooltip"><strong>${place}</strong>
    <div class="tooltip-value">${escapeHtml(metric_label)}: <b>${valueText}</b></div><table>${rows}</table></div>`;
}

// 시설 이름은 외부 데이터라 이스케이프한다.
export function facilityHtml(result: FinalResponse, properties: FacilityProperties): string {
  const unit = result.map_layer.geojson.features.find((item) => item.properties.unit_key === properties.unit_key);
  const place = unit ? `${unit.properties.parent_name ?? ""} ${unit.properties.unit_name}` : "";
  return `<div class="tooltip"><strong>${escapeHtml(properties.name ?? "이름 없음")}</strong>
    <div class="tooltip-value">${escapeHtml(place)}</div></div>`;
}

export function hexHtml(result: FinalResponse, count: number): string {
  const hexbins = result.map_layer.hexbins;
  const size = hexbins ? ` (한 변 ${hexbins.cell_size_m.toLocaleString("ko-KR")}m)` : "";
  return `<div class="tooltip"><strong>${count.toLocaleString("ko-KR")}곳</strong>
    <div class="tooltip-value">이 육각형 칸${size} 안의 수</div></div>`;
}

export function clusterHtml(count: number): string {
  return `<div class="tooltip"><strong>${count.toLocaleString("ko-KR")}곳</strong>
    <div class="tooltip-value">가까운 곳을 묶었습니다. 누르면 확대합니다.</div></div>`;
}
