import { useEffect, useRef, useState } from "react";
import type { Feature, MultiPolygon, Position } from "geojson";
import { loadKakaoMaps } from "../maps/kakaoLoader";
import { toKakaoPolygonPaths } from "../maps/kakaoGeometry";
import type { FinalResponse, UnitProperties } from "../types";
import { classColors, formatValue, layerStyle, MAP_CHROME, symbolMaxRadius } from "../theme";

export type ShowcaseView = "choropleth" | "bubble" | "point" | "hexbin" | "bivariate";
export type ShowcaseFeature = Feature<MultiPolygon, UnitProperties>;

interface Props {
  /** 없으면 결과 없이 배경 지도만 보인다 */
  result: FinalResponse | null;
  demo: boolean;
  view: ShowcaseView;
  hoveredKey: string | null;
  selectedKey: string | null;
  onHover: (key: string | null) => void;
  onSelect: (key: string | null) => void;
  focusRequest: { key: string; sequence: number } | null;
  resetSequence: number;
}

const CENTER = { lat: 37.5665, lng: 126.978 };
// 육각 격자 칸 아래에 까는 옅은 단위 경계선 (MapLibre 화면의 지역 경계선 색)
const UNIT_LINE = { strokeWeight: 1, strokeColor: MAP_CHROME.light.outline, strokeOpacity: 0.7 };

type UnitShape = kakao.maps.Polygon | kakao.maps.Polyline;

/** 강조를 풀 때 돌아갈 보기별 기본 선 */
function baseStroke(view: ShowcaseView) {
  return view === "hexbin" ? UNIT_LINE : { strokeWeight: 1, strokeColor: "#ffffff", strokeOpacity: view === "bubble" ? 0.5 : 0.9 };
}
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
  if (typeof value !== "number" || !Number.isFinite(value)) return "#cbd5e1";
  const { classification } = result.map_layer;
  const colors = classColors("light", classification.class_count, layerStyle(result.map_layer).emphasis);
  const index = classIndex(value, classification.breaks);
  return colors[Math.min(index, colors.length - 1)] ?? "#cbd5e1";
}

function twoValueColor(feature: ShowcaseFeature, result: FinalResponse, demo: boolean, thresholds: ReturnType<typeof getBivariateBreaks>) {
  const bivariate = result.map_layer.bivariate;
  if (!demo && !bivariate) return "#cbd5e1";
  const xMetric = demo ? "children_count" : bivariate!.x.metric;
  const yMetric = demo ? "daycare_count" : bivariate!.y.metric;
  const xValue = feature.properties[xMetric];
  const yValue = feature.properties[yMetric];
  if (typeof xValue !== "number" || typeof yValue !== "number") return "#cbd5e1";
  const x = classIndex(xValue, demo ? thresholds.children : bivariate!.x.breaks);
  const y = classIndex(yValue, demo ? thresholds.facilities : bivariate!.y.breaks);
  return BIVARIATE_COLORS[Math.min(y, 2)][Math.min(x, 2)];
}

export function KakaoShowcaseMap({
  result, demo, view, hoveredKey, selectedKey, onHover, onSelect, focusRequest, resetSequence,
}: Props) {
  const elementRef = useRef<HTMLDivElement>(null);
  const [instance, setInstance] = useState<{ maps: typeof kakao.maps; map: kakao.maps.Map } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const polygonsByKey = useRef(new Map<string, UnitShape[]>());
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
    if (!instance || !result) return;
    const { maps, map } = instance;
    const features = result.map_layer.geojson.features as ShowcaseFeature[];
    const shapes: UnitShape[] = [];
    const markers: kakao.maps.Marker[] = [];
    const overlays: kakao.maps.CustomOverlay[] = [];
    let clusterer: kakao.maps.MarkerClusterer | null = null;
    const removeEvents: Array<() => void> = [];
    const shapesByKey = new Map<string, UnitShape[]>();
    const thresholds = getBivariateBreaks(features);
    const displayPolygons = view === "choropleth" || view === "bivariate" || view === "bubble";

    if (displayPolygons) {
      for (const feature of features) {
        const key = feature.properties.unit_key;
        const fill = view === "choropleth"
          ? facilityColor(feature, result)
          : view === "bivariate"
            ? twoValueColor(feature, result, demo, thresholds)
            : "#dbeafe";
        const polygonPaths = toKakaoPolygonPaths(feature.geometry, maps);
        const featurePolygons: kakao.maps.Polygon[] = [];

        for (const path of polygonPaths) {
          const polygon = new maps.Polygon({
            map,
            path,
            ...baseStroke(view),
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

    // 정적 데모는 가상 시설 개수를 쓰지만, 실제 분석에서는 서버 지표와 실제 좌표만 사용한다.
    // 실제 결과는 값이 0보다 큰 모든 단위를, 정적 데모는 큰 값부터 표본 85개를 그린다. 작은 원을 나중에 올려 위에 보인다.
    const bubbleMetric = demo ? "daycare_count" : result.map_layer.metric;
    const positive = [...features]
      .filter((feature) => numberOf(feature, bubbleMetric) > 0)
      .sort((a, b) => numberOf(b, bubbleMetric) - numberOf(a, bubbleMetric));
    const notable = demo ? positive.slice(0, 85) : positive;

    if (view === "bubble") {
      const maxValue = Math.max(1, ...notable.map((feature) => numberOf(feature, bubbleMetric)));
      // 실제 결과는 MapLibre 화면과 같은 크기로 그린다: 원 넓이가 서버 구간의 최댓값 대비 값에 비례한다.
      const liveMax = result.map_layer.classification.max ?? maxValue;
      const liveMaxRadius = symbolMaxRadius(result.map_layer.feature_count);
      for (const feature of notable) {
        const value = numberOf(feature, bubbleMetric);
        const radius = demo
          ? Math.max(7, 32 * Math.sqrt(value / maxValue))
          : liveMaxRadius * Math.sqrt(value / liveMax);
        // 작은 원에는 숫자를 넣지 않고 aria-label만 단다.
        const small = !demo && radius < 12;
        const bubble = document.createElement("button");
        bubble.type = "button";
        bubble.className = small ? "kshow-map-bubble small" : "kshow-map-bubble";
        if (!small) bubble.textContent = formatValue(value, 0);
        bubble.style.width = `${radius * 2}px`;
        bubble.style.height = `${radius * 2}px`;
        bubble.style.fontSize = radius < 13 ? "10px" : "12px";
        bubble.setAttribute("aria-label", `${feature.properties.unit_name} ${result.map_layer.metric_label}: ${formatValue(value)}`);
        bubble.addEventListener("click", () => onSelectRef.current(feature.properties.unit_key));
        bubble.addEventListener("mouseenter", () => onHoverRef.current(feature.properties.unit_key));
        bubble.addEventListener("mouseleave", () => onHoverRef.current(null));
        const [lng, lat] = !demo && feature.properties.label_point
          ? feature.properties.label_point
          : representative(feature);
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

    if (view === "hexbin") {
      // 단위 경계는 칸 아래에 옅은 선으로만 깐다. 면이 없어서 선택한 단위가 위로 올라와도 칸 클릭을 막지 않는다.
      for (const feature of features) {
        const lines = toKakaoPolygonPaths(feature.geometry, maps)
          .map((rings) => new maps.Polyline({ map, path: rings, ...UNIT_LINE, zIndex: 1 }));
        shapes.push(...lines);
        shapesByKey.set(feature.properties.unit_key, lines);
      }

      const hexbins = result.map_layer.hexbins;
      if (hexbins) {
        const colors = classColors("light", hexbins.classification.class_count, "high");
        // 칸을 누르면 그 칸의 개수를 보인다. 내용은 DOM과 textContent로만 만든다.
        const tip = document.createElement("div");
        tip.className = "kshow-hex-tip";
        const tipCount = document.createElement("strong");
        const tipText = document.createElement("span");
        tipText.textContent = `이 육각형 칸(한 변 ${hexbins.cell_size_m.toLocaleString("ko-KR")}m) 안의 수`;
        tip.append(tipCount, tipText);
        // 칸을 누르기 전에는 지도에 올리지 않으므로 위치는 아무 곳이나 둔다.
        const tipOverlay = new maps.CustomOverlay({
          content: tip, position: new maps.LatLng(CENTER.lat, CENTER.lng), xAnchor: 0.5, yAnchor: 1, zIndex: 6,
        });
        overlays.push(tipOverlay);
        // 카카오는 도형을 누르면 지도 click도 함께 보낸다. 같은 클릭으로 연 표시를 바로 닫지 않도록 연 시각을 둔다.
        let tipOpenedAt = 0;

        for (const hex of hexbins.geojson.features) {
          const index = classIndex(hex.properties.count, hexbins.classification.breaks);
          const fillColor = colors[Math.min(index, colors.length - 1)];
          for (const path of toKakaoPolygonPaths(hex.geometry, maps)) {
            const polygon = new maps.Polygon({
              map, path, fillColor, fillOpacity: 0.85,
              strokeWeight: 1, strokeColor: MAP_CHROME.light.separator, strokeOpacity: 0.8, zIndex: 2,
            });
            shapes.push(polygon);
            const click = (event: kakao.maps.event.MouseEvent) => {
              tipOpenedAt = performance.now();
              tipCount.textContent = `${hex.properties.count.toLocaleString("ko-KR")}곳`;
              tipOverlay.setPosition(event.latLng);
              tipOverlay.setMap(map);
            };
            maps.event.addListener(polygon, "click", click);
            removeEvents.push(() => maps.event.removeListener(polygon, "click", click));
          }
        }
        const closeTip = () => {
          if (performance.now() - tipOpenedAt > 100) tipOverlay.setMap(null);
        };
        maps.event.addListener(map, "click", closeTip);
        removeEvents.push(() => maps.event.removeListener(map, "click", closeTip));
      }
    }

    if (view === "point") {
      if (demo) {
        for (const feature of notable) {
          const [lng, lat] = representative(feature);
          const marker = new maps.Marker({
            map, position: new maps.LatLng(lat, lng),
            title: `${feature.properties.unit_name} · 시연용 가상 위치`,
          });
          markers.push(marker);
          const click = () => onSelectRef.current(feature.properties.unit_key);
          maps.event.addListener(marker, "click", click);
          removeEvents.push(() => maps.event.removeListener(marker, "click", click));
        }
      } else {
        // 모든 시설을 그리되 가까운 마커는 묶어 개수로 보인다. 레벨 5 미만으로 확대하면 묶음이 풀린다.
        for (const feature of result.map_layer.points?.geojson.features ?? []) {
          const [lng, lat] = feature.geometry.coordinates;
          const marker = new maps.Marker({
            position: new maps.LatLng(lat, lng),
            title: feature.properties.name ?? "시설 위치",
          });
          markers.push(marker);
          const click = () => onSelectRef.current(feature.properties.unit_key);
          maps.event.addListener(marker, "click", click);
          removeEvents.push(() => maps.event.removeListener(marker, "click", click));
        }
        clusterer = new maps.MarkerClusterer({ map, markers, averageCenter: true, minLevel: 5 });
      }
    }

    polygonsByKey.current = shapesByKey;
    previousHighlight.current = [];

    return () => {
      for (const remove of removeEvents) remove();
      for (const polygon of shapes) polygon.setMap(null);
      clusterer?.clear();
      clusterer?.setMap(null);
      for (const marker of markers) marker.setMap(null);
      for (const overlay of overlays) overlay.setMap(null);
      polygonsByKey.current.clear();
      previousHighlight.current = [];
    };
  }, [instance, result, view, demo]);

  // hover / 선택 강조 시 다각형을 재생성하지 않고 스타일만 변경한다.
  useEffect(() => {
    const next = [hoveredKey, selectedKey].filter((key): key is string => key !== null);
    const keys = new Set([...previousHighlight.current, ...next]);
    const base = baseStroke(view);
    for (const key of keys) {
      for (const polygon of polygonsByKey.current.get(key) ?? []) {
        const selected = key === selectedKey;
        const hovered = key === hoveredKey;
        polygon.setOptions({
          strokeWeight: selected ? 4 : hovered ? 3 : base.strokeWeight,
          strokeColor: selected ? "#ea580c" : hovered ? "#0f172a" : base.strokeColor,
          strokeOpacity: selected || hovered ? 1 : base.strokeOpacity,
        });
        polygon.setZIndex(selected ? 5 : hovered ? 3 : 1);
      }
    }
    previousHighlight.current = next;
  }, [hoveredKey, selectedKey, instance, view]);

  // 순위표에서 선택한 항목으로 지도를 이동한다.
  useEffect(() => {
    if (!instance || !focusRequest || !result) return;
    const feature = result.map_layer.geojson.features.find(
      (item) => item.properties.unit_key === focusRequest.key,
    ) as ShowcaseFeature | undefined;
    if (!feature) return;
    const [lng, lat] = feature.properties.label_point ?? representative(feature);
    instance.map.panTo(new instance.maps.LatLng(lat, lng));
    instance.map.setLevel(6);
  }, [instance, result, focusRequest]);

  // 새 분석 결과가 들어오거나 전체 보기 버튼을 누르면 결과 영역 전체가 보이도록 이동한다. 결과가 없으면 기본 중심으로.
  useEffect(() => {
    if (!instance) return;
    const bounds = new instance.maps.LatLngBounds();
    let count = 0;
    for (const feature of result?.map_layer.geojson.features ?? []) {
      for (const polygon of feature.geometry.coordinates) {
        for (const ring of polygon) {
          for (const position of ring) {
            if (!Number.isFinite(position[0]) || !Number.isFinite(position[1])) continue;
            bounds.extend(new instance.maps.LatLng(position[1], position[0]));
            count += 1;
          }
        }
      }
    }
    if (count > 2) instance.map.setBounds(bounds);
    else {
      instance.map.setLevel(9);
      instance.map.panTo(new instance.maps.LatLng(CENTER.lat, CENTER.lng));
    }
  }, [instance, result, resetSequence]);

  return (
    <>
      <div className="kshow-map" ref={elementRef} aria-label="카카오지도 분석 시각화" />
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
