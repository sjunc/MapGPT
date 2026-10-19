import type { ExpressionSpecification } from "@maplibre/maplibre-gl-style-spec";
import {
  type GeoJSONSource,
  LngLatBounds,
  Map as MapLibreMap,
  type MapLayerMouseEvent,
  NavigationControl,
  Popup,
  setWorkerUrl,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre v6: Vite가 번들한 worker URL을 명시적으로 지정한다.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useRef } from "react";
import { type ColorScheme, MAP_CHROME, NULL_REASONS, classColors, formatValue } from "../theme";
import type { FinalResponse, UnitProperties } from "../types";
import { MapResetControl } from "./MapResetControl";

setWorkerUrl(maplibreWorkerUrl);

const SOURCE = "units";
const LAYERS = ["units-fill", "units-separator", "units-hover"];

// 표에서 지역을 선택할 때마다 sequence가 증가하므로 같은 행을 다시 눌러도 이동할 수 있다.
export interface FocusRequest {
  unitKey: string;
  sequence: number;
}

function fillColor(result: FinalResponse, scheme: ColorScheme): ExpressionSpecification {
  const { metric, classification } = result.map_layer;
  const colors = classColors(scheme, classification.class_count);
  const value: ExpressionSpecification = ["get", metric];
  const stops = classification.breaks.flatMap((limit, i) => [limit, colors[i + 1]]);
  const classified = (stops.length > 0 ? ["step", value, colors[0], ...stops] : colors[0]) as ExpressionSpecification;
  return ["case", ["==", value, null], MAP_CHROME[scheme].noData, classified];
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
  const requestedBottom = mobile && panelRect ? rect.bottom - panelRect.top + 24 : 110;

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
    mobile && panelRect ? Math.ceil(rect.bottom - panelRect.top + 24) : 110,
    rect.height - top - 80,
  ));

  map.fitBounds(bounds, {
    padding: { top, right, bottom, left },
    maxZoom: 13.5,
    duration: 450,
  });
}

function applyResult(map: MapLibreMap, result: FinalResponse, scheme: ColorScheme, panelOpen: boolean) {
  (map.getSource(SOURCE) as GeoJSONSource).setData(result.map_layer.geojson);
  map.setPaintProperty("units-fill", "fill-color", fillColor(result, scheme));
  fitResultBounds(map, result, panelOpen);
}

interface Props {
  result: FinalResponse;
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
  result, scheme, hoveredKey, onHover, panelOpen, panelExpanded, selectedKey, onSelect, focusRequest,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const ready = useRef(false);
  const latest = useRef({ result, onHover, hoveredKey, panelOpen, selectedKey, onSelect, focusRequest });
  latest.current = { result, onHover, hoveredKey, panelOpen, selectedKey, onSelect, focusRequest };
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

      ready.current = true;
      applyResult(instance, latest.current.result, scheme, latest.current.panelOpen);

      if (latest.current.hoveredKey !== null) {
        instance.setFeatureState(
          { source: SOURCE, id: latest.current.hoveredKey },
          { hover: true },
        );
      }
      if (latest.current.selectedKey !== null) {
        instance.setFeatureState(
          { source: SOURCE, id: latest.current.selectedKey },
          { selected: true },
        );
      }
      if (latest.current.focusRequest) {
        focusFeature(instance, latest.current.result, latest.current.focusRequest.unitKey, latest.current.panelOpen);
      }
    });

    // 마우스 hover: 일시적인 팝업과 표/지도 강조 동기화
    instance.on("mousemove", LAYERS[0], (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      if (!feature) return;

      instance.getCanvas().style.cursor = "pointer";
      latest.current.onHover(feature.properties.unit_key as string);

      if (clickPopup.isOpen()) return;
      hoverPopup
        .setLngLat(event.lngLat)
        .setHTML(tooltipHtml(latest.current.result, feature.properties as UnitProperties))
        .addTo(instance);
    });

    instance.on("mouseleave", LAYERS[0], () => {
      instance.getCanvas().style.cursor = "";
      latest.current.onHover(null);
      hoverPopup.remove();
    });

    // 클릭 및 모바일 탭: 닫기 버튼이 있는 고정 팝업
    instance.on("click", (event) => {
      if (!ready.current) return;

      const feature = instance.queryRenderedFeatures(event.point, { layers: [LAYERS[0]] })[0];
      if (!feature) {
        clickPopup.remove();
        latest.current.onSelect(null);
        return;
      }

      latest.current.onSelect(feature.properties.unit_key as string);
      hoverPopup.remove();
      clickPopup
        .setLngLat(event.lngLat)
        .setHTML(tooltipHtml(latest.current.result, feature.properties as UnitProperties))
        .addTo(instance);
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
    applyResult(map.current, result, scheme, panelOpen);
  }, [result, scheme]);

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

    instance.setFeatureState({ source: SOURCE, id: selectedKey }, { selected: true });
    return () => {
      if (instance.getSource(SOURCE)) {
        instance.setFeatureState({ source: SOURCE, id: selectedKey }, { selected: false });
      }
    };
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

    instance.setFeatureState({ source: SOURCE, id: hoveredKey }, { hover: true });
    return () => {
      if (instance.getSource(SOURCE)) {
        instance.setFeatureState({ source: SOURCE, id: hoveredKey }, { hover: false });
      }
    };
  }, [hoveredKey]);

  return (
    <div
      ref={container}
      className="map"
      role="img"
      aria-label={`${result.map_layer.metric_label} Choropleth 지도`}
    />
  );
}
