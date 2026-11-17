import { useEffect, useRef, useState } from "react";
import type { Feature, MultiPolygon, Position } from "geojson";
import { loadKakaoMaps } from "../maps/kakaoLoader";
import { toKakaoPolygonPaths } from "../maps/kakaoGeometry";
import type { FinalResponse, UnitProperties } from "../types";

export type ShowcaseView = "choropleth" | "bubble" | "point" | "bivariate";
export type ShowcaseFeature = Feature<MultiPolygon, UnitProperties>;

interface Props {
  result: FinalResponse;
  view: ShowcaseView;
  hoveredKey: string | null;
  selectedKey: string | null;
  onHover: (key: string | null) => void;
  onSelect: (key: string | null) => void;
  focusRequest: { key: string; sequence: number } | null;
  resetSequence: number;
}

const CENTER = { lat: 37.5665, lng: 126.978 };
const BLUE = ["#124b88", "#2677b8", "#56a0d2", "#94c7e5", "#cee5f2"];
// 시연 전용 3×3 팔레트. 실제 서비스에서는 서버가 제공하는 분류 기준을 따른다.
export const BIVARIATE_COLORS = [
  ["#e8e8e8", "#adc8d7", "#6c9ebd"],
  ["#e6b68d", "#b3a6a6", "#788fae"],
  ["#db8152", "#b67f78", "#735982"],
];

function numberOf(feature: ShowcaseFeature, field: string): number {
  const value = feature.properties[field];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function classIndex(value: number, breaks: number[]): number {
  let index = 0;
  while (index < breaks.length && value >= breaks[index]) index += 1;
  return index;
}

/** 가상 수치에 적용할 분위값. 실제 분석 결과에 대한 재계산 용도로 사용하지 않는다. */
function thirds(values: number[]): [number, number] {
  const sorted = [...values].sort((a, b) => a - b);
  const at = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p))] ?? 0;
  return [at(1 / 3), at(2 / 3)];
}

export function getBivariateBreaks(features: ShowcaseFeature[]) {
  return {
    children: thirds(features.map((item) => numberOf(item, "children_count"))),
    facilities: thirds(features.map((item) => numberOf(item, "daycare_count"))),
  };
}

/** 링 안에 있는지 확인해 오목한 구역의 표식 위치도 가능한 한 안쪽으로 잡는다. */
function insideRing([x, y]: Position, ring: Position[]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** 샘플 표식용 위치. 실제 어린이집 좌표가 아니라 경계 내부의 가상 위치다. */
function representative(feature: ShowcaseFeature): [number, number] {
  const rings = feature.geometry.coordinates[0] ?? [];
  const ring = rings[0] ?? [];
  if (ring.length < 3) return [CENTER.lng, CENTER.lat];

  const xs = ring.map((p) => p[0]);
  const ys = ring.map((p) => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const x = (minX + maxX) / 2, y = (minY + maxY) / 2;
  if (insideRing([x, y], ring) && rings.slice(1).every((hole) => !insideRing([x, y], hole))) return [x, y];

  // 정해진 순서의 후보를 검사하므로 새로고침할 때 위치가 바뀌지 않는다.
  for (let i = 0; i < 40; i += 1) {
    const candidate: [number, number] = [
      minX + (maxX - minX) * ((i * 0.61803398875 + 0.27) % 1),
      minY + (maxY - minY) * ((i * 0.41421356237 + 0.37) % 1),
    ];
    if (insideRing(candidate, ring) && rings.slice(1).every((hole) => !insideRing(candidate, hole))) {
      return candidate;
    }
  }
  return [ring[0][0], ring[0][1]];
}

function facilityColor(feature: ShowcaseFeature, result: FinalResponse): string {
  const value = feature.properties[result.map_layer.metric];
  if (typeof value !== "number") return "#cbd5e1";
  const index = classIndex(value, result.map_layer.classification.breaks);
  return BLUE[Math.min(index, BLUE.length - 1)];
}

function twoValueColor(feature: ShowcaseFeature, thresholds: ReturnType<typeof getBivariateBreaks>) {
  const x = classIndex(numberOf(feature, "children_count"), thresholds.children);
  const y = classIndex(numberOf(feature, "daycare_count"), thresholds.facilities);
  return BIVARIATE_COLORS[Math.min(y, 2)][Math.min(x, 2)];
}

export function KakaoShowcaseMap({
  result, view, hoveredKey, selectedKey, onHover, onSelect, focusRequest, resetSequence,
}: Props) {
  const elementRef = useRef<HTMLDivElement>(null);
  const [instance, setInstance] = useState<{ maps: typeof kakao.maps; map: kakao.maps.Map } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const polygonsByKey = useRef(new Map<string, kakao.maps.Polygon[]>());
  const previousHighlight = useRef<string[]>([]);
  const onHoverRef = useRef(onHover);
  const onSelectRef = useRef(onSelect);
  onHoverRef.current = onHover;
  onSelectRef.current = onSelect;

  // React StrictMode에서 중복 SDK 로딩 및 언마운트 이후 상태 변경을 방지한다.
  useEffect(() => {
    let active = true;
    let resize: ResizeObserver | undefined;
    const node = elementRef.current;
    if (!node) return;

    loadKakaoMaps().then((maps) => {
      if (!active) return;
      const map = new maps.Map(node, {
        center: new maps.LatLng(CENTER.lat, CENTER.lng),
        level: 9,
      });
      map.addControl(new maps.ZoomControl(), maps.ControlPosition.RIGHT);
      resize = new ResizeObserver(() => map.relayout());
      resize.observe(node);
      setInstance({ maps, map });
      setLoading(false);
    }).catch((cause: unknown) => {
      if (!active) return;
      setLoading(false);
      setError(cause instanceof Error ? cause.message : String(cause));
    });

    return () => {
      active = false;
      resize?.disconnect();
      node.replaceChildren();
    };
  }, []);

  // 보기 변경 시 지도 인스턴스는 유지하고 기존 오버레이만 교체한다.
  useEffect(() => {
    if (!instance) return;
    const { maps, map } = instance;
    const features = result.map_layer.geojson.features as ShowcaseFeature[];
    const shapes: kakao.maps.Polygon[] = [];
    const markers: kakao.maps.Marker[] = [];
    const overlays: kakao.maps.CustomOverlay[] = [];
    const removeEvents: Array<() => void> = [];
    const shapesByKey = new Map<string, kakao.maps.Polygon[]>();
    const thresholds = getBivariateBreaks(features);
    const displayPolygons = view === "choropleth" || view === "bivariate" || view === "bubble";

    if (displayPolygons) {
      for (const feature of features) {
        const key = feature.properties.unit_key;
        const fill = view === "choropleth"
          ? facilityColor(feature, result)
          : view === "bivariate"
            ? twoValueColor(feature, thresholds)
            : "#dbeafe";
        const polygonPaths = toKakaoPolygonPaths(feature.geometry, maps);
        const featurePolygons: kakao.maps.Polygon[] = [];

        for (const path of polygonPaths) {
          const polygon = new maps.Polygon({
            map,
            path,
            strokeWeight: 1,
            strokeColor: "#ffffff",
            strokeOpacity: view === "bubble" ? 0.5 : 0.9,
            fillColor: fill,
            fillOpacity: view === "bubble" ? 0.13 : 0.7,
            zIndex: 1,
          });
          featurePolygons.push(polygon);
          shapes.push(polygon);

          const click = () => onSelectRef.current(key);
          const over = () => onHoverRef.current(key);
          const out = () => onHoverRef.current(null);
          maps.event.addListener(polygon, "click", click);
          maps.event.addListener(polygon, "mouseover", over);
          maps.event.addListener(polygon, "mouseout", out);
          removeEvents.push(() => {
            maps.event.removeListener(polygon, "click", click);
            maps.event.removeListener(polygon, "mouseover", over);
            maps.event.removeListener(polygon, "mouseout", out);
          });
        }
        shapesByKey.set(key, featurePolygons);
      }
    }

    // 시연 가독성을 위해 원과 마커는 상위 85개 법정동만 표시한다.
    const notable = [...features]
      .filter((feature) => numberOf(feature, "daycare_count") > 0)
      .sort((a, b) => numberOf(b, "daycare_count") - numberOf(a, "daycare_count"))
      .slice(0, 85);

    if (view === "bubble") {
      const maxCount = Math.max(1, ...notable.map((feature) => numberOf(feature, "daycare_count")));
      for (const feature of notable) {
        const count = numberOf(feature, "daycare_count");
        const radius = Math.max(7, 32 * Math.sqrt(count / maxCount));
        const bubble = document.createElement("button");
        bubble.type = "button";
        bubble.className = "kshow-map-bubble";
        bubble.textContent = String(count);
        bubble.style.width = `${radius * 2}px`;
        bubble.style.height = `${radius * 2}px`;
        bubble.style.fontSize = radius < 13 ? "10px" : "12px";
        bubble.setAttribute("aria-label", `${feature.properties.unit_name} 가상 시설 ${count}개`);
        bubble.addEventListener("click", () => onSelectRef.current(feature.properties.unit_key));
        bubble.addEventListener("mouseenter", () => onHoverRef.current(feature.properties.unit_key));
        bubble.addEventListener("mouseleave", () => onHoverRef.current(null));
        const [lng, lat] = representative(feature);
        const overlay = new maps.CustomOverlay({
          map,
          position: new maps.LatLng(lat, lng),
          content: bubble,
          xAnchor: 0.5,
          yAnchor: 0.5,
          clickable: true,
          zIndex: 4,
        });
        overlays.push(overlay);
      }
    }

    if (view === "point") {
      for (const feature of notable) {
        const [lng, lat] = representative(feature);
        const marker = new maps.Marker({
          map,
          position: new maps.LatLng(lat, lng),
          title: `${feature.properties.unit_name} · 가상 위치`,
        });
        markers.push(marker);
        const click = () => onSelectRef.current(feature.properties.unit_key);
        maps.event.addListener(marker, "click", click);
        removeEvents.push(() => maps.event.removeListener(marker, "click", click));
      }
    }

    polygonsByKey.current = shapesByKey;
    previousHighlight.current = [];

    return () => {
      for (const remove of removeEvents) remove();
      for (const polygon of shapes) polygon.setMap(null);
      for (const marker of markers) marker.setMap(null);
      for (const overlay of overlays) overlay.setMap(null);
      polygonsByKey.current.clear();
      previousHighlight.current = [];
    };
  }, [instance, result, view]);

  // hover / 선택 강조 시 다각형을 재생성하지 않고 스타일만 변경한다.
  useEffect(() => {
    const next = [hoveredKey, selectedKey].filter((key): key is string => key !== null);
    const keys = new Set([...previousHighlight.current, ...next]);
    for (const key of keys) {
      for (const polygon of polygonsByKey.current.get(key) ?? []) {
        const selected = key === selectedKey;
        const hovered = key === hoveredKey;
        polygon.setOptions({
          strokeWeight: selected ? 4 : hovered ? 3 : 1,
          strokeColor: selected ? "#ea580c" : hovered ? "#0f172a" : "#ffffff",
          strokeOpacity: selected || hovered ? 1 : 0.9,
        });
        polygon.setZIndex(selected ? 5 : hovered ? 3 : 1);
      }
    }
    previousHighlight.current = next;
  }, [hoveredKey, selectedKey, instance, view]);

  // 순위표에서 선택한 항목으로 지도를 이동한다.
  useEffect(() => {
    if (!instance || !focusRequest) return;
    const feature = result.map_layer.geojson.features.find(
      (item) => item.properties.unit_key === focusRequest.key,
    ) as ShowcaseFeature | undefined;
    if (!feature) return;
    const [lng, lat] = representative(feature);
    instance.map.panTo(new instance.maps.LatLng(lat, lng));
    instance.map.setLevel(6);
  }, [instance, result, focusRequest]);

  useEffect(() => {
    if (!instance) return;
    instance.map.setLevel(9);
    instance.map.panTo(new instance.maps.LatLng(CENTER.lat, CENTER.lng));
  }, [instance, resetSequence]);

  return (
    <>
      <div className="kshow-map" ref={elementRef} aria-label="카카오지도 시각화 데모" />
      {(loading || error) && (
        <div className="kshow-map-status" role={error ? "alert" : "status"}>
          {error ? (
            <>
              <strong>카카오지도 SDK를 불러오지 못했어.</strong>
              <span>{error}</span>
              <small>카카오맵 활성화, JavaScript SDK 도메인 등록, 키 종류를 확인해 줘.</small>
            </>
          ) : (
            <span>카카오지도를 연결하는 중…</span>
          )}
        </div>
      )}
    </>
  );
}
