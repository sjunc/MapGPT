import type { ExpressionSpecification } from "@maplibre/maplibre-gl-style-spec";
import type { FeatureCollection, Point } from "geojson";
import {
  type GeoJSONSource,
  LngLatBounds,
  Map as MapLibreMap,
  type MapGeoJSONFeature,
  NavigationControl,
  Popup,
  setWorkerUrl,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre v6: Vite가 번들한 worker URL을 명시적으로 지정한다.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useRef } from "react";
import {
  type ColorScheme,
  HEAT_COLORS,
  MAP_CHROME,
  NULL_REASONS,
  classColors,
  formatValue,
  layerStyle,
  symbolMaxRadius,
} from "../theme";
import type { FacilityProperties, FinalResponse, MapView, UnitProperties } from "../types";
import { MapResetControl } from "./MapResetControl";

setWorkerUrl(maplibreWorkerUrl);

const SOURCE = "units";
const POINT_SOURCE = "unit-points";
const FACILITY_SOURCE = "facilities"; // 점 지도: 가까운 점을 묶는다
const HEAT_SOURCE = "facilities-heat"; // 밀도 지도: 같은 점을 묶지 않고 쓴다
const LAYERS = ["units-fill", "units-separator", "units-hover"];
const SYMBOL_LAYERS = ["units-outline", "unit-symbols"];
const FACILITY_LAYERS = ["facility-clusters", "facility-cluster-count", "facility-points"];
const HEAT_LAYER = "facility-heat";
const HIT_LAYER = "units-hit"; // 점·밀도 보기에서 지역 hover·클릭을 받는 투명한 면
// 보기마다 보이는 레이어. hover·선택 테두리(units-hover)는 모든 보기에서 보인다.
const VIEW_LAYERS: Record<MapView, string[]> = {
  choropleth: [LAYERS[0], LAYERS[1]],
  proportional_symbol: SYMBOL_LAYERS,
  point: [SYMBOL_LAYERS[0], HIT_LAYER, ...FACILITY_LAYERS],
  heatmap: [SYMBOL_LAYERS[0], HIT_LAYER, HEAT_LAYER],
};
const VIEW_SWITCHED_LAYERS = [...new Set(Object.values(VIEW_LAYERS).flat())];
// hover·클릭을 받는 레이어. 보이는 레이어 중 위에 그린 것(시설 점)이 먼저 잡힌다.
const INTERACTIVE_LAYERS = [FACILITY_LAYERS[0], FACILITY_LAYERS[2], SYMBOL_LAYERS[1], LAYERS[0], HIT_LAYER];
const VIEW_NAMES: Record<MapView, string> = {
  choropleth: "단계 구분도",
  proportional_symbol: "비례 원",
  point: "점",
  heatmap: "밀도",
};
const NO_POINTS: FeatureCollection<Point, FacilityProperties> = { type: "FeatureCollection", features: [] };

// 표에서 지역을 선택할 때마다 sequence가 증가하므로 같은 행을 다시 눌러도 이동할 수 있다.
export interface FocusRequest {
  unitKey: string;
  sequence: number;
}

function fillColor(result: FinalResponse, scheme: ColorScheme): ExpressionSpecification {
  const { metric, classification } = result.map_layer;
  const colors = classColors(scheme, classification.class_count, layerStyle(result.map_layer).emphasis);
  const value: ExpressionSpecification = ["get", metric];
  const stops = classification.breaks.flatMap((limit, i) => [limit, colors[i + 1]]);
  const classified = (stops.length > 0 ? ["step", value, colors[0], ...stops] : colors[0]) as ExpressionSpecification;
  return ["case", ["==", value, null], MAP_CHROME[scheme].noData, classified];
}

/** 단위마다 대표점(label_point)에 놓을 비례 원. 예전 실행 기록에는 대표점이 없어 비어 있다. */
function unitPoints(result: FinalResponse): FeatureCollection<Point, UnitProperties> {
  return {
    type: "FeatureCollection",
    features: result.map_layer.geojson.features.flatMap((feature) => {
      const point = feature.properties.label_point;
      return point
        ? [{ type: "Feature" as const, id: feature.id, geometry: { type: "Point" as const, coordinates: point }, properties: feature.properties }]
        : [];
    }),
  };
}

/** 원의 넓이가 값에 비례하도록 반지름은 값의 제곱근으로 키운다. */
function symbolRadius(result: FinalResponse): ExpressionSpecification | number {
  const { metric, classification, feature_count } = result.map_layer;
  const max = classification.max ?? 0;
  if (max <= 0) return 0;
  return ["*", symbolMaxRadius(feature_count), ["sqrt", ["/", ["max", ["to-number", ["get", metric]], 0], max]]];
}

/** 지도 지표로 센 시설 위치. 점 개수 지표가 아니거나 예전 실행 기록이면 비어 있다. */
function facilityPoints(result: FinalResponse): FeatureCollection<Point, FacilityProperties> {
  return result.map_layer.points?.geojson ?? NO_POINTS;
}

/** 밀도가 0인 곳은 투명하고(같은 색의 투명으로 시작해 회색 테두리가 생기지 않게), 밀집할수록 램프의 끝 색이 된다. */
function heatmapColor(scheme: ColorScheme): ExpressionSpecification {
  const colors = HEAT_COLORS[scheme];
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(colors[0].slice(i, i + 2), 16));
  const stops = colors.flatMap((color, i) => [0.08 + (0.92 * i) / (colors.length - 1), color]);
  return [
    "interpolate", ["linear"], ["heatmap-density"], 0, `rgba(${r}, ${g}, ${b}, 0)`, ...stops,
  ] as ExpressionSpecification;
}

function applyView(map: MapLibreMap, view: MapView) {
  const visible = new Set(VIEW_LAYERS[view]);
  for (const layer of VIEW_SWITCHED_LAYERS) {
    map.setLayoutProperty(layer, "visibility", visible.has(layer) ? "visible" : "none");
  }
}

// 두 보기의 같은 지역을 함께 강조한다 (면과 원은 같은 unit_key를 쓴다).
function setUnitState(map: MapLibreMap, id: string, state: Record<string, boolean>) {
  for (const source of [SOURCE, POINT_SOURCE]) {
    if (map.getSource(source)) map.setFeatureState({ source, id }, state);
  }
}

// LLM이 만든 문자열은 팝업 HTML에 삽입하기 전에 반드시 이스케이프한다.
function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

function tooltipHtml(result: FinalResponse, properties: UnitProperties): string {
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
function facilityHtml(result: FinalResponse, properties: FacilityProperties): string {
  const unit = result.map_layer.geojson.features.find((item) => item.properties.unit_key === properties.unit_key);
  const place = unit ? `${unit.properties.parent_name ?? ""} ${unit.properties.unit_name}` : "";
  return `<div class="tooltip"><strong>${escapeHtml(properties.name ?? "이름 없음")}</strong>
    <div class="tooltip-value">${escapeHtml(place)}</div></div>`;
}

function clusterHtml(count: number): string {
  return `<div class="tooltip"><strong>${count.toLocaleString("ko-KR")}곳</strong>
    <div class="tooltip-value">가까운 곳을 묶었습니다. 누르면 확대합니다.</div></div>`;
}

function featureHtml(result: FinalResponse, feature: MapGeoJSONFeature): string {
  if (feature.layer.id === FACILITY_LAYERS[0]) return clusterHtml(feature.properties.point_count as number);
  if (feature.layer.id === FACILITY_LAYERS[2]) return facilityHtml(result, feature.properties as FacilityProperties);
  return tooltipHtml(result, feature.properties as UnitProperties);
}

// 데스크톱에서 오른쪽 아래 범례가 결과 지역을 가리지 않도록, 지도를 맞출 때 범례 윗변 아래를 비운다.
// 모바일에서는 범례가 지도 위에 없다(크기 0).
function legendClearance(rect: DOMRect): number {
  const legend = document.querySelector<HTMLElement>(".map-legend")?.getBoundingClientRect();
  return legend && legend.height > 0 ? Math.ceil(rect.bottom - legend.top + 16) : 0;
}

function fitResultBounds(map: MapLibreMap, result: FinalResponse, panelOpen: boolean) {
  const bounds = new LngLatBounds();

  for (const feature of result.map_layer.geojson.features) {
    for (const polygon of feature.geometry.coordinates) {
      for (const [x, y] of polygon[0]) {
        bounds.extend([x, y]);
      }
    }
  }

  if (bounds.isEmpty()) return;

  const rect = map.getContainer().getBoundingClientRect();
  if (rect.width < 120 || rect.height < 120) return;

  const mobile = window.matchMedia("(max-width: 860px)").matches;
  const panel = panelOpen ? document.querySelector<HTMLElement>(".result-panel") : null;
  const panelRect = panel?.getBoundingClientRect();

  const requestedLeft = !mobile && panelRect ? panelRect.right - rect.left + 24 : 24;
  const requestedBottom = mobile && panelRect ? rect.bottom - panelRect.top + 24 : Math.max(110, legendClearance(rect));

  const top = Math.min(100, Math.floor(rect.height * 0.2));
  const right = 24;
  const left = Math.max(0, Math.min(Math.ceil(requestedLeft), rect.width - right - 80));
  const bottom = Math.max(0, Math.min(Math.ceil(requestedBottom), rect.height - top - 80));

  map.fitBounds(bounds, {
    padding: { top, right, bottom, left },
    duration: 0,
    maxZoom: 12,
  });
}

function focusFeature(map: MapLibreMap, result: FinalResponse, key: string, panelOpen: boolean) {
  const feature = result.map_layer.geojson.features.find((item) => item.properties.unit_key === key);
  if (!feature) return;

  const bounds = new LngLatBounds();
  for (const polygon of feature.geometry.coordinates) {
    for (const [x, y] of polygon[0]) bounds.extend([x, y]);
  }
  if (bounds.isEmpty()) return;

  const rect = map.getContainer().getBoundingClientRect();
  const panelRect = panelOpen
    ? document.querySelector<HTMLElement>(".result-panel")?.getBoundingClientRect()
    : undefined;
  const mobile = window.matchMedia("(max-width: 860px)").matches;
  const top = Math.min(100, Math.floor(rect.height * 0.2));
  const right = 24;
  const left = Math.max(0, Math.min(
    !mobile && panelRect ? Math.ceil(panelRect.right - rect.left + 24) : 24,
    rect.width - right - 80,
  ));
  const bottom = Math.max(0, Math.min(
    mobile && panelRect ? Math.ceil(rect.bottom - panelRect.top + 24) : Math.max(110, legendClearance(rect)),
    rect.height - top - 80,
  ));

  map.fitBounds(bounds, {
    padding: { top, right, bottom, left },
    maxZoom: 13.5,
    duration: 450,
  });
}

function applyResult(map: MapLibreMap, result: FinalResponse, scheme: ColorScheme, panelOpen: boolean, view: MapView) {
  const value: ExpressionSpecification = ["get", result.map_layer.metric];
  (map.getSource(SOURCE) as GeoJSONSource).setData(result.map_layer.geojson);
  (map.getSource(POINT_SOURCE) as GeoJSONSource).setData(unitPoints(result));
  (map.getSource(FACILITY_SOURCE) as GeoJSONSource).setData(facilityPoints(result));
  (map.getSource(HEAT_SOURCE) as GeoJSONSource).setData(facilityPoints(result));
  map.setPaintProperty("units-fill", "fill-color", fillColor(result, scheme));
  map.setPaintProperty(SYMBOL_LAYERS[1], "circle-radius", symbolRadius(result));
  map.setFilter(SYMBOL_LAYERS[1], ["!=", value, null]); // 값이 없는 지역은 원을 그리지 않는다
  map.setLayoutProperty(SYMBOL_LAYERS[1], "circle-sort-key", ["-", 0, ["to-number", value]]); // 작은 원이 위에
  applyView(map, view);
  fitResultBounds(map, result, panelOpen);
}

interface Props {
  result: FinalResponse;
  /** 지금 보여줄 지도 보기 (기본값은 백엔드가 지표 성격으로 정한다) */
  view: MapView;
  scheme: ColorScheme;
  hoveredKey: string | null;
  onHover: (key: string | null) => void;
  panelOpen: boolean;
  panelExpanded: boolean;
  selectedKey: string | null;
  onSelect: (key: string | null) => void;
  focusRequest: FocusRequest | null;
}

export function ChoroplethMap({
  result, view, scheme, hoveredKey, onHover, panelOpen, panelExpanded, selectedKey, onSelect, focusRequest,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const ready = useRef(false);
  const latest = useRef({ result, view, onHover, hoveredKey, panelOpen, selectedKey, onSelect, focusRequest });
  latest.current = { result, view, onHover, hoveredKey, panelOpen, selectedKey, onSelect, focusRequest };
  const clickPopupRef = useRef<Popup | null>(null);

  // 배경지도 스타일이 바뀌면 MapLibre 지도를 새로 생성한다.
  useEffect(() => {
    if (!container.current) return;

    const chrome = MAP_CHROME[scheme];
    const instance = new MapLibreMap({
      container: container.current,
      style: chrome.basemapStyle,
      center: [126.98, 37.56],
      zoom: 10,
      attributionControl: { customAttribution: "경계: 통계청 SGIS" },
    });

    const hoverPopup = new Popup({ closeButton: false, closeOnClick: false, maxWidth: "280px" });
    const clickPopup = new Popup({ closeButton: true, closeOnClick: false, maxWidth: "280px" });
    clickPopupRef.current = clickPopup;

    instance.addControl(new NavigationControl({ showCompass: false }), "top-right");
    instance.addControl(
      new MapResetControl(
        () => fitResultBounds(instance, latest.current.result, latest.current.panelOpen),
        "분석 범위 전체 보기",
      ),
      "top-right",
    );

    instance.on("load", () => {
      // 행정구역은 배경 면 위, 지명 라벨 아래에 배치한다.
      const layers = instance.getStyle().layers;
      const lastShape = layers.findLastIndex((layer) => layer.type !== "symbol");
      const beforeId = layers.slice(lastShape + 1).find((layer) => layer.type === "symbol")?.id;

      instance.addSource(SOURCE, {
        type: "geojson",
        data: latest.current.result.map_layer.geojson,
        promoteId: "unit_key",
      });

      instance.addLayer(
        { id: LAYERS[0], type: "fill", source: SOURCE, paint: { "fill-opacity": 0.85 } },
        beforeId,
      );
      instance.addLayer(
        {
          id: LAYERS[1],
          type: "line",
          source: SOURCE,
          paint: { "line-width": 0.8, "line-color": chrome.separator },
        },
        beforeId,
      );
      instance.addLayer(
        {
          id: LAYERS[2],
          type: "line",
          source: SOURCE,
          paint: {
            "line-color": chrome.hover,
            "line-width": [
              "case",
              ["boolean", ["feature-state", "selected"], false], 4,
              ["boolean", ["feature-state", "hover"], false], 2.5,
              0,
            ],
          },
        },
        beforeId,
      );

      // 비례 원 보기: 옅은 지역 경계선과, 지명 라벨 위에 그리는 원
      instance.addSource(POINT_SOURCE, {
        type: "geojson",
        data: unitPoints(latest.current.result),
        promoteId: "unit_key",
      });
      instance.addLayer(
        {
          id: SYMBOL_LAYERS[0],
          type: "line",
          source: SOURCE,
          layout: { visibility: "none" },
          paint: { "line-width": 0.8, "line-color": chrome.outline, "line-opacity": 0.8 },
        },
        beforeId,
      );
      instance.addLayer({
        id: SYMBOL_LAYERS[1],
        type: "circle",
        source: POINT_SOURCE,
        layout: { visibility: "none" },
        paint: {
          "circle-color": chrome.symbol,
          "circle-opacity": 0.78,
          "circle-stroke-color": [
            "case",
            ["any", ["boolean", ["feature-state", "selected"], false], ["boolean", ["feature-state", "hover"], false]],
            chrome.hover,
            chrome.separator,
          ],
          "circle-stroke-width": [
            "case",
            ["boolean", ["feature-state", "selected"], false], 3,
            ["boolean", ["feature-state", "hover"], false], 2,
            1,
          ],
        },
      });

      // 점·밀도 보기: 지역 hover를 받는 투명한 면, 지명 라벨 아래의 밀도, 맨 위의 시설 점과 묶음
      instance.addLayer(
        { id: HIT_LAYER, type: "fill", source: SOURCE, layout: { visibility: "none" }, paint: { "fill-opacity": 0 } },
        beforeId,
      );
      instance.addSource(HEAT_SOURCE, { type: "geojson", data: facilityPoints(latest.current.result) });
      instance.addLayer(
        {
          id: HEAT_LAYER,
          type: "heatmap",
          source: HEAT_SOURCE,
          layout: { visibility: "none" },
          paint: {
            "heatmap-color": heatmapColor(scheme),
            // 서울 전체 배율에서 시설이 고르게 퍼져 있어도 몰린 곳만 진해지도록 강도를 낮게 시작한다.
            "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 9, 6, 13, 22],
            "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 9, 0.12, 13, 0.9],
            "heatmap-opacity": 0.85,
          },
        },
        LAYERS[2], // hover·선택 테두리가 밀도 위에 보이도록
      );
      instance.addSource(FACILITY_SOURCE, {
        type: "geojson",
        data: facilityPoints(latest.current.result),
        cluster: true,
        clusterRadius: 36,
        clusterMaxZoom: 14,
      });
      instance.addLayer({
        id: FACILITY_LAYERS[0],
        type: "circle",
        source: FACILITY_SOURCE,
        filter: ["has", "point_count"],
        layout: { visibility: "none" },
        paint: {
          "circle-color": chrome.symbol,
          "circle-opacity": 0.85,
          "circle-radius": ["step", ["get", "point_count"], 10, 10, 13, 50, 17, 200, 22],
          "circle-stroke-color": chrome.separator,
          "circle-stroke-width": 1.5,
        },
      });
      instance.addLayer({
        id: FACILITY_LAYERS[1],
        type: "symbol",
        source: FACILITY_SOURCE,
        filter: ["has", "point_count"],
        layout: {
          visibility: "none",
          "text-field": ["get", "point_count_abbreviated"],
          "text-font": ["Noto Sans Bold"],
          "text-size": 11,
          "text-allow-overlap": true,
        },
        paint: { "text-color": chrome.symbolText },
      });
      instance.addLayer({
        id: FACILITY_LAYERS[2],
        type: "circle",
        source: FACILITY_SOURCE,
        filter: ["!", ["has", "point_count"]],
        layout: { visibility: "none" },
        paint: {
          "circle-color": chrome.symbol,
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3.5, 15, 6],
          "circle-stroke-color": chrome.separator,
          "circle-stroke-width": 1,
        },
      });

      ready.current = true;
      applyResult(instance, latest.current.result, scheme, latest.current.panelOpen, latest.current.view);

      if (latest.current.hoveredKey !== null) {
        setUnitState(instance, latest.current.hoveredKey, { hover: true });
      }
      if (latest.current.selectedKey !== null) {
        setUnitState(instance, latest.current.selectedKey, { selected: true });
      }
      if (latest.current.focusRequest) {
        focusFeature(instance, latest.current.result, latest.current.focusRequest.unitKey, latest.current.panelOpen);
      }
    });

    const clearHover = () => {
      instance.getCanvas().style.cursor = "";
      latest.current.onHover(null);
      hoverPopup.remove();
    };

    // 마우스 hover: 일시적인 팝업과 표/지도 강조 동기화. 보이는 보기의 면·원·시설 점에서만 일어난다.
    instance.on("mousemove", (event) => {
      if (!ready.current) return;
      const feature = instance.queryRenderedFeatures(event.point, { layers: INTERACTIVE_LAYERS })[0];
      if (!feature) {
        clearHover();
        return;
      }

      instance.getCanvas().style.cursor = "pointer";
      // 묶음은 여러 지역에 걸칠 수 있어 지역을 강조하지 않는다.
      latest.current.onHover((feature.properties.unit_key as string | undefined) ?? null);

      if (clickPopup.isOpen()) return;
      hoverPopup.setLngLat(event.lngLat).setHTML(featureHtml(latest.current.result, feature)).addTo(instance);
    });
    instance.on("mouseout", clearHover);

    // 클릭 및 모바일 탭: 닫기 버튼이 있는 고정 팝업. 묶음을 누르면 묶음이 풀리는 배율로 확대한다.
    instance.on("click", (event) => {
      if (!ready.current) return;

      const feature = instance.queryRenderedFeatures(event.point, { layers: INTERACTIVE_LAYERS })[0];
      if (!feature) {
        clickPopup.remove();
        latest.current.onSelect(null);
        return;
      }

      hoverPopup.remove();
      if (feature.layer.id === FACILITY_LAYERS[0]) {
        const source = instance.getSource(FACILITY_SOURCE) as GeoJSONSource;
        const center = (feature.geometry as Point).coordinates as [number, number];
        void source.getClusterExpansionZoom(feature.properties.cluster_id as number).then((zoom) => {
          instance.easeTo({ center, zoom, duration: 450 });
        });
        return;
      }

      latest.current.onSelect(feature.properties.unit_key as string);
      clickPopup.setLngLat(event.lngLat).setHTML(featureHtml(latest.current.result, feature)).addTo(instance);
    });

    const handleResize = () => {
      instance.resize();
      if (ready.current) {
        fitResultBounds(instance, latest.current.result, latest.current.panelOpen);
      }
    };
    window.addEventListener("resize", handleResize);

    map.current = instance;
    return () => {
      window.removeEventListener("resize", handleResize);
      hoverPopup.remove();
      clickPopup.remove();
      clickPopupRef.current = null;
      ready.current = false;
      map.current = null;
      instance.remove();
    };
  }, [scheme]);

  // 새 분석 결과를 기존 지도 레이어에 반영한다.
  useEffect(() => {
    if (!map.current || !ready.current) return;
    applyResult(map.current, result, scheme, panelOpen, view);
  }, [result, scheme]);

  // 지도 보기(단계 구분도·비례 원·점·밀도)를 바꾼다.
  useEffect(() => {
    if (!map.current || !ready.current) return;
    applyView(map.current, view);
  }, [view]);

  // 패널의 열림/닫힘에 따라 지도 중심과 배율을 다시 맞춘다.
  useEffect(() => {
    if (!map.current || !ready.current) return;
    fitResultBounds(map.current, result, panelOpen);
  }, [panelOpen, panelExpanded, result]);

  // 지역을 선택하면 마우스 hover와 무관하게 굵은 경계선을 유지한다.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current || selectedKey === null) {
      if (selectedKey === null) clickPopupRef.current?.remove();
      return;
    }

    setUnitState(instance, selectedKey, { selected: true });
    return () => setUnitState(instance, selectedKey, { selected: false });
  }, [selectedKey]);

  // 순위표 클릭 시 대상 행정구역으로 이동한다. 지도 클릭은 지도를 이동시키지 않는다.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current || focusRequest === null) return;
    clickPopupRef.current?.remove();
    focusFeature(instance, result, focusRequest.unitKey, panelOpen);
  }, [focusRequest, result, panelOpen]);

  // 표 ↔ 지도 hover 강조 상태를 동기화한다.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current || hoveredKey === null) return;

    setUnitState(instance, hoveredKey, { hover: true });
    return () => setUnitState(instance, hoveredKey, { hover: false });
  }, [hoveredKey]);

  return (
    <div
      ref={container}
      className="map"
      role="img"
      aria-label={`${view === "point" || view === "heatmap" ? (result.map_layer.points?.title ?? "") : result.map_layer.metric_label} ${VIEW_NAMES[view]} 지도`}
    />
  );
}
