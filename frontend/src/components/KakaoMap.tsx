import { useEffect, useRef, useState } from "react";
import type { Feature, MultiPolygon } from "geojson";
import SimpleHeat, { type HeatPoint } from "simpleheat";
// 지도 버튼은 MapLibre 화면과 같은 컨트롤 모양(.maplibregl-ctrl-*)과 위치 규칙(styles.css, p6.css)을 쓴다.
import "maplibre-gl/dist/maplibre-gl.css";
import { loadKakaoMaps } from "../maps/kakaoLoader";
import { toKakaoPolygonPaths } from "../maps/kakaoGeometry";
import { fitPadding } from "../maps/padding";
import { FLAG_SIZE, flagFoot, rankFlagElement, rankFlags } from "../maps/rankFlags";
import { clusterHtml, facilityHtml, hexHtml, tooltipHtml } from "../maps/tooltips";
import { BIVARIATE_COLORS, classColors, classIndex, HEAT_COLORS, layerStyle, MAP_CHROME, symbolMaxRadius } from "../theme";
import type { FinalResponse, MapView, UnitProperties } from "../types";
import type { FocusRequest } from "./ChoroplethMap";
import "./kakao-map.css";

/** MapLibre 지도(ChoroplethMap·BaseMap) 자리에 들어가는 카카오 지도. 입력값과 동작은 ChoroplethMap과 같다. */
interface Props {
  /** 없으면 결과 없이 배경 지도만 보인다 (MapLibre 화면의 BaseMap) */
  result: FinalResponse | null;
  view: MapView;
  hoveredKey: string | null;
  onHover: (key: string | null) => void;
  selectedKey: string | null;
  onSelect: (key: string | null) => void;
  focusRequest: FocusRequest | null;
  panelOpen: boolean;
  panelExpanded: boolean;
}

type UnitFeature = Feature<MultiPolygon, UnitProperties>;
type UnitShape = kakao.maps.Polygon | kakao.maps.Polyline;
type Instance = { maps: typeof kakao.maps; map: kakao.maps.Map };
type Emphasis = "none" | "hover" | "selected";

// 카카오 지도는 밝은 배경지도뿐이라 화면을 밝은 테마로 고정하고(App), 지도 색도 라이트 값을 쓴다.
const CHROME = MAP_CHROME.light;
// 결과가 없을 때의 위치: MapLibre 화면 BaseMap의 중심과 줌 10.5 (카카오 레벨 L은 MapLibre 줌 약 18.8 − L)
const CENTER = { lat: 37.5665, lng: 126.978 };
const BASE_LEVEL = 8;
// 이보다 확대하지 않는 레벨: 결과 맞춤은 MapLibre maxZoom 12, 순위에서 고른 지역은 maxZoom 13.5에 맞춘다.
const FIT_MIN_LEVEL = 7;
const FOCUS_MIN_LEVEL = 5;
// 지역 경계선: 단계 구분도·이변량은 면 사이의 흰 구분선, 그 밖의 보기는 MapLibre 화면의 옅은 지역 경계선
const FILL_LINE = { strokeWeight: 1, strokeColor: "#ffffff", strokeOpacity: 0.9 };
const UNIT_LINE = { strokeWeight: 1, strokeColor: CHROME.outline, strokeOpacity: 0.8 };
// 배경지도에 따라 바뀌는 값. 위성 사진(스카이뷰) 위에서는 사진이 비치도록 면을 옅게 칠하고,
// 지역 경계선과 hover·선택 테두리는 MapLibre 다크 화면(어두운 배경)처럼 흰색으로 그린다.
const BASEMAP = {
  roadmap: { fillOpacity: 0.7, hexOpacity: 0.85, unitLine: UNIT_LINE, highlight: CHROME.hover },
  satellite: {
    fillOpacity: 0.45,
    hexOpacity: 0.6,
    unitLine: { ...UNIT_LINE, strokeColor: "#ffffff", strokeOpacity: 0.7 },
    highlight: MAP_CHROME.dark.hover,
  },
};
// 묶음 원: MapLibre 화면처럼 시설 10·50·200곳 미만이면 반지름 10·13·17·22px, 그 이상은 22px
const CLUSTER_STEPS = [10, 50, 200];
const CLUSTER_RADII = [10, 13, 17, 22];
// 시설 점 크기 (MapLibre 화면의 반지름 3.5~6px 점과 비슷하게)
const DOT_SIZE = 11;

/** 면을 칠하는 보기. 나머지 보기는 지역 경계선만 그린다. */
function filled(view: MapView): boolean {
  return view === "choropleth" || view === "bivariate";
}

/** 점·밀도·육각 격자 보기: 점·칸이 없는 곳에서 지역 hover·클릭을 받는 투명한 면을 깐다 (MapLibre 화면의 units-hit). */
function hitArea(view: MapView): boolean {
  return view === "point" || view === "heatmap" || view === "hexbin";
}

/** "#rrggbb"를 투명도를 준 rgba 색으로 바꾼다. */
function rgba(hex: string, alpha: number): string {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// 밀도 색: MapLibre 화면의 heatmap-color처럼 밀도 0.08부터 램프를 쓴다. simpleheat는 밀도를 불투명도로도 쓴다.
const HEAT_GRADIENT = Object.fromEntries(
  HEAT_COLORS.light.map((color, i) => [0.08 + (0.92 * i) / (HEAT_COLORS.light.length - 1), color]),
);

/** 밀도 크기는 MapLibre 화면과 같게 줌 9→13에서 반지름 6→22px, 세기 0.12→0.9로 키운다. */
function heatSize(level: number): { radius: number; intensity: number } {
  const t = Math.min(1, Math.max(0, (18.8 - level - 9) / 4));
  return { radius: 6 + 16 * t, intensity: 0.12 + 0.78 * t };
}

const CLUSTER_STYLES = CLUSTER_RADII.map((radius) => ({
  width: `${radius * 2}px`,
  height: `${radius * 2}px`,
  lineHeight: `${radius * 2 - 3}px`,
  boxSizing: "border-box",
  borderRadius: "50%",
  border: `1.5px solid ${CHROME.separator}`,
  background: rgba(CHROME.symbol, 0.85),
  color: CHROME.symbolText,
  textAlign: "center",
  fontSize: "11px",
  fontWeight: "700",
}));

function clusterRadius(size: number): number {
  const step = CLUSTER_STEPS.findIndex((limit) => size < limit);
  return CLUSTER_RADII[step === -1 ? CLUSTER_RADII.length - 1 : step];
}

function dotImage(maps: typeof kakao.maps): kakao.maps.MarkerImage {
  const half = DOT_SIZE / 2;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${DOT_SIZE}" height="${DOT_SIZE}">` +
    `<circle cx="${half}" cy="${half}" r="${half - 1}" fill="${CHROME.symbol}" stroke="${CHROME.separator}"/></svg>`;
  return new maps.MarkerImage(
    `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`,
    new maps.Size(DOT_SIZE, DOT_SIZE),
    { offset: new maps.Point(half, half) },
  );
}

function numberOf(feature: UnitFeature, field: string): number | null {
  const value = feature.properties[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** 단계 구분도 색: 서버 구간 [경계값 이상, 다음 경계값 미만)의 색 (MapLibre 화면의 step 식과 같다). 값이 없으면 회색이다. */
function choroplethColor(feature: UnitFeature, result: FinalResponse): string {
  const value = numberOf(feature, result.map_layer.metric);
  if (value === null) return CHROME.noData;
  const { classification } = result.map_layer;
  const colors = classColors("light", classification.class_count, layerStyle(result.map_layer).emphasis);
  return colors[Math.min(classIndex(value, classification.breaks), colors.length - 1)] ?? CHROME.noData;
}

/** 이변량 축의 3분위 칸 번호(0~2). 같은 값이 많아 경계값이 2개보다 적으면 양 끝 칸을 쓴다 (MapLibre 화면의 axisClass와 같다). */
function axisCell(value: number, breaks: number[]): number {
  return breaks.length === 0 ? 1 : Math.round((classIndex(value, breaks) * 2) / breaks.length);
}

/** 분모(x)·분자(y) 칸 번호로 고른 3×3 색. 두 값 중 하나라도 없으면 회색이다. */
function bivariateColor(feature: UnitFeature, result: FinalResponse): string {
  const spec = result.map_layer.bivariate;
  if (!spec) return CHROME.noData;
  const x = numberOf(feature, spec.x.metric);
  const y = numberOf(feature, spec.y.metric);
  if (x === null || y === null) return CHROME.noData;
  return BIVARIATE_COLORS.light[axisCell(y, spec.y.breaks)][axisCell(x, spec.x.breaks)];
}

/** 비례 원 테두리: MapLibre 화면처럼 기본은 1px 밝은 선, hover는 2px·선택은 3px 강조선(일반 지도는 검정, 위성 사진은 흰색) */
function strokeBubble(element: HTMLElement, emphasis: Emphasis, highlight: string) {
  element.style.borderColor = emphasis === "none" ? CHROME.separator : highlight;
  element.style.boxShadow = emphasis === "none" ? "" : `0 0 0 ${emphasis === "selected" ? 2 : 1}px ${highlight}`;
}

/** 다각형 외곽 링으로 만든 범위 (MapLibre 화면의 fitResultBounds·focusFeature와 같다) */
function outerBounds(maps: typeof kakao.maps, features: UnitFeature[]): kakao.maps.LatLngBounds {
  const bounds = new maps.LatLngBounds();
  for (const feature of features) {
    for (const polygon of feature.geometry.coordinates) {
      for (const [x, y] of polygon[0] ?? []) bounds.extend(new maps.LatLng(y, x));
    }
  }
  return bounds;
}

/** 지도 위 UI를 피해 범위 전체가 보이게 맞춘다. minLevel보다 확대되면 범위 가운데를 기준으로 그 레벨까지 되돌린다. */
function fitBounds(
  { maps, map }: Instance, node: HTMLElement, bounds: kakao.maps.LatLngBounds, panelOpen: boolean, minLevel: number,
) {
  if (bounds.isEmpty()) return;
  const { top, right, bottom, left } = fitPadding(node, panelOpen);
  map.setBounds(bounds, top, right, bottom, left);
  if (map.getLevel() < minLevel) {
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();
    const center = new maps.LatLng((sw.getLat() + ne.getLat()) / 2, (sw.getLng() + ne.getLng()) / 2);
    map.setLevel(minLevel, { anchor: center });
  }
}

type MapTooltip = ReturnType<typeof createMapTooltip>;

/**
 * MapLibre 화면의 hover·클릭 팝업과 같은 내용·모양의 지도 툴팁 (maps/tooltips.ts가 이스케이프한 HTML).
 * 마우스를 올리면 따라다니고, 누르면 그 자리에 고정되며 × 버튼이 생긴다.
 * 고정한 툴팁은 ×, 지도 빈 곳 클릭, 선택 해제로 닫힌다.
 */
function createMapTooltip(maps: typeof kakao.maps, map: kakao.maps.Map) {
  const element = document.createElement("div");
  element.className = "kakao-map-tooltip";
  const body = document.createElement("div");
  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.className = "maplibregl-popup-close-button"; // MapLibre 클릭 팝업의 닫기 버튼과 같은 모양
  closeButton.setAttribute("aria-label", "툴팁 닫기");
  closeButton.textContent = "×";
  element.append(body, closeButton);
  const overlay = new maps.CustomOverlay({
    content: element, position: map.getCenter(), xAnchor: 0.5, yAnchor: 1, zIndex: 10, clickable: true,
  });
  let pinned = false;
  let shownHtml = "";

  const show = (html: string, position: kakao.maps.LatLng, offset: number, pin: boolean) => {
    pinned = pin;
    element.classList.toggle("pinned", pin);
    element.style.setProperty("--tip-offset", `${offset}px`);
    if (html !== shownHtml) {
      body.innerHTML = html;
      shownHtml = html;
      overlay.setContent(element); // 내용 크기가 바뀌면 기준점(아래 가운데)을 다시 잰다
    }
    overlay.setPosition(position);
    overlay.setMap(map);
    // 감싼 요소는 마우스를 통과시켜, 툴팁이 마우스 아래를 덮어도 도형의 mouseout이 일어나게 한다. 고정한 툴팁만 마우스를 받는다.
    if (element.parentElement) element.parentElement.style.pointerEvents = "none";
  };
  const close = () => {
    pinned = false;
    shownHtml = "";
    overlay.setMap(null);
  };
  closeButton.addEventListener("click", close);

  return {
    overlay,
    hover(html: string, position: kakao.maps.LatLng, offset = 12) {
      if (!pinned) show(html, position, offset, false);
    },
    leave() {
      if (!pinned) close();
    },
    pin(html: string, position: kakao.maps.LatLng, offset = 12) {
      show(html, position, offset, true);
    },
    close,
  };
}

export function KakaoMap({
  result, view, hoveredKey, onHover, selectedKey, onSelect, focusRequest, panelOpen, panelExpanded,
}: Props) {
  const elementRef = useRef<HTMLDivElement>(null);
  const heatRef = useRef<HTMLCanvasElement>(null);
  const [instance, setInstance] = useState<Instance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // 위성 사진(스카이뷰) 켜짐. 새 분석·결과 초기화·보기 전환에도 유지하고, 새로고침하면 일반 지도로 돌아간다.
  const [satellite, setSatellite] = useState(false);
  const outlinesByKey = useRef(new Map<string, UnitShape[]>());
  const bubblesByKey = useRef(new Map<string, HTMLElement>());
  const previousHighlight = useRef<string[]>([]);
  const tooltipRef = useRef<MapTooltip | null>(null);
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
      const map = new maps.Map(node, { center: new maps.LatLng(CENTER.lat, CENTER.lng), level: BASE_LEVEL });
      // 카카오 로고·축척 묶음. 좁은 화면에서 MapLibre 출처 표기처럼 결과 시트 위로 올려 가리지 않게 한다 (kakao-map.css).
      // SDK가 클래스 없이 만드는 요소라, 카카오맵으로 가는 로고 링크를 품은 지도 바로 아래 요소를 찾는다.
      const logo = node.querySelector('a[href*="map.kakao.com"]');
      [...node.children].find((child) => logo && child.contains(logo))?.classList.add("kakao-copyright");
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

  function fitResult(target: Instance, current: FinalResponse) {
    const node = elementRef.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    if (rect.width < 120 || rect.height < 120) return;
    const features = current.map_layer.geojson.features as UnitFeature[];
    fitBounds(target, node, outerBounds(target.maps, features), panelOpen, FIT_MIN_LEVEL);
  }

  function goHome(target: Instance) {
    target.map.setLevel(BASE_LEVEL);
    target.map.setCenter(new target.maps.LatLng(CENTER.lat, CENTER.lng));
  }

  // 보기나 배경지도(위성 사진)를 바꾸면 지도 인스턴스는 유지하고 기존 도형·오버레이만 교체한다.
  useEffect(() => {
    if (!instance || !result) return;
    const { maps, map } = instance;
    const basemap = BASEMAP[satellite ? "satellite" : "roadmap"];
    const features = result.map_layer.geojson.features as UnitFeature[];
    const shapes: UnitShape[] = [];
    const markers: kakao.maps.Marker[] = [];
    const overlays: kakao.maps.CustomOverlay[] = [];
    const removeEvents: Array<() => void> = [];
    const outlines = new Map<string, UnitShape[]>();
    const bubbles = new Map<string, HTMLElement>();
    let clusterer: kakao.maps.MarkerClusterer | null = null;
    let shapeClickedAt = -Infinity;

    const listen = (target: kakao.maps.event.EventTarget, handlers: Record<string, Function>) => {
      for (const [type, handler] of Object.entries(handlers)) maps.event.addListener(target, type, handler);
      removeEvents.push(() => {
        for (const [type, handler] of Object.entries(handlers)) maps.event.removeListener(target, type, handler);
      });
    };

    const tooltip = createMapTooltip(maps, map);
    tooltipRef.current = tooltip;
    overlays.push(tooltip.overlay);
    listen(map, {
      // 빈 곳을 누르면 MapLibre 화면처럼 선택을 풀고 고정한 툴팁을 닫는다.
      // 카카오는 도형을 누르면 지도 click도 함께 보내므로, 같은 클릭은 빈 곳 클릭으로 보지 않는다.
      click: () => {
        if (performance.now() - shapeClickedAt < 100) return;
        tooltip.close();
        onSelectRef.current(null);
      },
    });

    // 누를 수 있는 면 위에서는 MapLibre 화면처럼 손가락 커서를 보인다.
    const pointer = (on: boolean) => map.setCursor(on ? "pointer" : "");

    // 지역: hover는 따라다니는 툴팁과 순위표 강조, 클릭은 선택과 고정 툴팁 (MapLibre 화면과 같다)
    const unitEvents = (target: kakao.maps.Polygon, feature: UnitFeature) => {
      const key = feature.properties.unit_key;
      const html = tooltipHtml(result, feature.properties);
      listen(target, {
        click: (event: kakao.maps.event.MouseEvent) => {
          shapeClickedAt = performance.now();
          onSelectRef.current(key);
          tooltip.pin(html, event.latLng);
        },
        mouseover: (event: kakao.maps.event.MouseEvent) => {
          pointer(true);
          onHoverRef.current(key);
          tooltip.hover(html, event.latLng);
        },
        mousemove: (event: kakao.maps.event.MouseEvent) => tooltip.hover(html, event.latLng),
        mouseout: () => {
          pointer(false);
          onHoverRef.current(null);
          tooltip.leave();
        },
      });
    };

    for (const feature of features) {
      const key = feature.properties.unit_key;
      const paths = toKakaoPolygonPaths(feature.geometry, maps);
      if (filled(view)) {
        const fillColor = view === "choropleth" ? choroplethColor(feature, result) : bivariateColor(feature, result);
        const polygons = paths.map((path) => new maps.Polygon({
          map, path, ...FILL_LINE, fillColor, fillOpacity: basemap.fillOpacity, zIndex: 1,
        }));
        for (const polygon of polygons) unitEvents(polygon, feature);
        shapes.push(...polygons);
        outlines.set(key, polygons);
        continue;
      }
      // 그 밖의 보기는 지역 경계선만 그린다. 선택한 지역의 선이 위로 올라와도 면이 없어 칸·점 클릭을 막지 않는다.
      const lines = paths.map((path) => new maps.Polyline({ map, path, ...basemap.unitLine, zIndex: 1 }));
      shapes.push(...lines);
      outlines.set(key, lines);
      if (hitArea(view)) {
        for (const path of paths) {
          const hit = new maps.Polygon({ map, path, strokeWeight: 0, fillColor: "#ffffff", fillOpacity: 0.01, zIndex: 0 });
          unitEvents(hit, feature);
          shapes.push(hit);
        }
      }
    }

    if (view === "proportional_symbol") {
      // MapLibre 화면과 같은 크기: 원 넓이가 서버 구간 최댓값 대비 값에 비례하고, 대표점(label_point)이 있는 단위만 그린다.
      // 큰 원부터 올려 작은 원이 위에 보인다 (MapLibre 화면의 circle-sort-key).
      const metric = result.map_layer.metric;
      const max = result.map_layer.classification.max ?? 0;
      const maxRadius = symbolMaxRadius(result.map_layer.feature_count);
      const sized = features
        .map((feature) => ({ feature, value: numberOf(feature, metric) ?? 0 }))
        .filter(({ feature, value }) => max > 0 && value > 0 && feature.properties.label_point)
        .sort((a, b) => b.value - a.value);
      for (const { feature, value } of sized) {
        const radius = maxRadius * Math.sqrt(value / max);
        const [lng, lat] = feature.properties.label_point!;
        const center = new maps.LatLng(lat, lng);
        const key = feature.properties.unit_key;
        const html = tooltipHtml(result, feature.properties);
        const element = document.createElement("div");
        element.className = "kakao-bubble";
        element.style.width = `${radius * 2}px`;
        element.style.height = `${radius * 2}px`;
        element.style.background = rgba(CHROME.symbol, 0.78);
        strokeBubble(element, "none", basemap.highlight);
        element.addEventListener("click", () => {
          onSelectRef.current(key);
          tooltip.pin(html, center, radius + 6);
        });
        element.addEventListener("mouseenter", () => {
          onHoverRef.current(key);
          tooltip.hover(html, center, radius + 6);
        });
        element.addEventListener("mouseleave", () => {
          onHoverRef.current(null);
          tooltip.leave();
        });
        overlays.push(new maps.CustomOverlay({
          map, position: center, content: element, xAnchor: 0.5, yAnchor: 0.5, clickable: true, zIndex: 4,
        }));
        bubbles.set(key, element);
      }
    }

    if (view === "hexbin" && result.map_layer.hexbins) {
      const { hexbins } = result.map_layer;
      const colors = classColors("light", hexbins.classification.class_count, "high");
      for (const hex of hexbins.geojson.features) {
        const fillColor = colors[Math.min(classIndex(hex.properties.count, hexbins.classification.breaks), colors.length - 1)];
        const html = hexHtml(result, hex.properties.count);
        for (const path of toKakaoPolygonPaths(hex.geometry, maps)) {
          const polygon = new maps.Polygon({
            map, path, fillColor, fillOpacity: basemap.hexOpacity,
            strokeWeight: 1, strokeColor: CHROME.separator, strokeOpacity: 0.8, zIndex: 2,
          });
          shapes.push(polygon);
          listen(polygon, {
            mouseover: (event: kakao.maps.event.MouseEvent) => {
              pointer(true);
              tooltip.hover(html, event.latLng);
            },
            mousemove: (event: kakao.maps.event.MouseEvent) => tooltip.hover(html, event.latLng),
            mouseout: () => {
              pointer(false);
              tooltip.leave();
            },
            click: (event: kakao.maps.event.MouseEvent) => {
              shapeClickedAt = performance.now();
              tooltip.pin(html, event.latLng);
            },
          });
        }
      }
    }

    if (view === "point") {
      // 모든 시설을 점으로 그리되 가까운 점은 묶어 개수로 보인다. 레벨 5 미만으로 확대하면 묶음이 풀리고, 묶음을 누르면 확대한다.
      const image = dotImage(maps);
      for (const facility of result.map_layer.points?.geojson.features ?? []) {
        const [lng, lat] = facility.geometry.coordinates;
        const position = new maps.LatLng(lat, lng);
        const marker = new maps.Marker({ position, image, clickable: true });
        markers.push(marker);
        const html = facilityHtml(result, facility.properties);
        listen(marker, {
          click: () => {
            shapeClickedAt = performance.now();
            onSelectRef.current(facility.properties.unit_key);
            tooltip.pin(html, position);
          },
          mouseover: () => {
            onHoverRef.current(facility.properties.unit_key);
            tooltip.hover(html, position);
          },
          mouseout: () => {
            onHoverRef.current(null);
            tooltip.leave();
          },
        });
      }
      clusterer = new maps.MarkerClusterer({
        map, markers, averageCenter: true, minLevel: 5, calculator: CLUSTER_STEPS, styles: CLUSTER_STYLES,
      });
      // 묶음은 여러 지역에 걸칠 수 있어 지역을 강조하지 않는다 (MapLibre 화면과 같다).
      listen(clusterer, {
        clusterover: (cluster: kakao.maps.Cluster) => {
          onHoverRef.current(null);
          tooltip.hover(clusterHtml(cluster.getSize()), cluster.getCenter(), clusterRadius(cluster.getSize()) + 6);
        },
        clusterout: () => tooltip.leave(),
        clusterclick: () => {
          shapeClickedAt = performance.now();
          tooltip.leave();
        },
      });
    }

    // 순위 1~3위 지역의 대표점에 깃발을 단다 (MapLibre 화면과 같다). 깃발은 이웃 지역 위에 걸칠 수 있어,
    // 깃발에 올리거나 누르면 깃발이 가리키는 지역을 강조·선택하고 툴팁은 깃발 위에 띄운다. 1위가 맨 위다.
    for (const flag of rankFlags(result)) {
      const [lng, lat] = flag.position;
      const position = new maps.LatLng(lat, lng);
      const key = flag.properties.unit_key;
      const html = tooltipHtml(result, flag.properties);
      const foot = flagFoot(flag);
      const element = rankFlagElement(flag);
      element.addEventListener("click", () => {
        onSelectRef.current(key);
        tooltip.pin(html, position, foot.y + 4);
      });
      element.addEventListener("mouseenter", () => {
        onHoverRef.current(key);
        tooltip.hover(html, position, foot.y + 4);
      });
      element.addEventListener("mouseleave", () => {
        onHoverRef.current(null);
        tooltip.leave();
      });
      overlays.push(new maps.CustomOverlay({
        map, position, content: element, clickable: true, zIndex: 8 - flag.rank,
        xAnchor: foot.x / FLAG_SIZE.width, yAnchor: foot.y / FLAG_SIZE.height,
      }));
    }

    outlinesByKey.current = outlines;
    bubblesByKey.current = bubbles;
    previousHighlight.current = [];

    return () => {
      for (const remove of removeEvents) remove();
      pointer(false);
      for (const shape of shapes) shape.setMap(null);
      clusterer?.clear();
      clusterer?.setMap(null);
      for (const marker of markers) marker.setMap(null);
      for (const overlay of overlays) overlay.setMap(null);
      outlinesByKey.current = new Map();
      bubblesByKey.current = new Map();
      previousHighlight.current = [];
      tooltipRef.current = null;
    };
  }, [instance, result, view, satellite]);

  // 선택을 풀면(선택 해제 버튼 등) 고정한 툴팁도 닫는다 (MapLibre 화면의 클릭 팝업과 같다).
  useEffect(() => {
    if (selectedKey === null) tooltipRef.current?.close();
  }, [selectedKey]);

  // hover·선택 강조: 도형을 다시 만들지 않고 테두리만 바꾼다. MapLibre 화면처럼 hover 2.5px·선택 4px 선이고,
  // 일반 지도에서는 검정, 위성 사진에서는 흰색이다.
  useEffect(() => {
    const next = [hoveredKey, selectedKey].filter((key): key is string => key !== null);
    const { unitLine, highlight } = BASEMAP[satellite ? "satellite" : "roadmap"];
    const base = filled(view) ? FILL_LINE : unitLine;
    for (const key of new Set([...previousHighlight.current, ...next])) {
      const emphasis: Emphasis = key === selectedKey ? "selected" : key === hoveredKey ? "hover" : "none";
      for (const shape of outlinesByKey.current.get(key) ?? []) {
        shape.setOptions({
          strokeWeight: emphasis === "selected" ? 4 : emphasis === "hover" ? 2.5 : base.strokeWeight,
          strokeColor: emphasis === "none" ? base.strokeColor : highlight,
          strokeOpacity: emphasis === "none" ? base.strokeOpacity : 1,
        });
        shape.setZIndex(emphasis === "selected" ? 5 : emphasis === "hover" ? 3 : 1);
      }
      const bubble = bubblesByKey.current.get(key);
      if (bubble) strokeBubble(bubble, emphasis, highlight);
    }
    previousHighlight.current = next;
  }, [hoveredKey, selectedKey, instance, result, view, satellite]);

  // 위성 사진(스카이뷰): 지도 객체는 그대로 두고 지도 종류만 바꾼다(쿼터를 더 쓰지 않는다). 지명·도로 이름이 함께 나오는 HYBRID다.
  useEffect(() => {
    if (!instance) return;
    const { maps, map } = instance;
    map.setMapTypeId(satellite ? maps.MapTypeId.HYBRID : maps.MapTypeId.ROADMAP);
  }, [instance, satellite]);

  // 결과가 없으면 서울 기본 위치로 돌아간다 (MapLibre 화면의 BaseMap).
  useEffect(() => {
    if (instance && !result) goHome(instance);
  }, [instance, result]);

  // 새 결과가 오거나 결과 패널을 열고 닫으면 지도 위 UI를 피해 결과 전체가 보이게 맞춘다 (MapLibre 화면과 같다).
  useEffect(() => {
    if (instance && result) fitResult(instance, result);
    // panelOpen은 fitResult가 읽는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instance, result, panelOpen, panelExpanded]);

  // 창 크기가 바뀌면 MapLibre 화면처럼 결과 전체에 다시 맞춘다.
  useEffect(() => {
    if (!instance || !result) return;
    const refit = () => {
      instance.map.relayout();
      fitResult(instance, result);
    };
    window.addEventListener("resize", refit);
    return () => window.removeEventListener("resize", refit);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instance, result, panelOpen]);

  // 순위표에서 고른 지역으로 옮긴다. 지도 위 UI를 피해 그 지역 전체가 보이게 맞추되 레벨 5보다 더 확대하지 않는다.
  useEffect(() => {
    const node = elementRef.current;
    if (!instance || !node || !focusRequest || !result) return;
    tooltipRef.current?.close();
    const feature = result.map_layer.geojson.features.find((item) => item.properties.unit_key === focusRequest.unitKey);
    if (!feature) return;
    fitBounds(instance, node, outerBounds(instance.maps, [feature as UnitFeature]), panelOpen, FOCUS_MIN_LEVEL);
  }, [instance, result, focusRequest, panelOpen]);

  // 밀도 보기: 카카오 지도 API에는 히트맵이 없어, 지도 위에 겹친 canvas에 simpleheat로 그리고 지도가 움직이면 다시 그린다.
  useEffect(() => {
    const canvas = heatRef.current;
    const points = result?.map_layer.points;
    if (!instance || !canvas || view !== "heatmap" || !points) return;
    const { maps, map } = instance;
    const coords = points.geojson.features.map(({ geometry }) => new maps.LatLng(geometry.coordinates[1], geometry.coordinates[0]));
    const heat = new SimpleHeat(canvas).gradient(HEAT_GRADIENT);
    let radius = 0;
    let frame = 0;

    const draw = () => {
      frame = 0;
      if (canvas.width !== canvas.clientWidth) canvas.width = canvas.clientWidth;
      if (canvas.height !== canvas.clientHeight) canvas.height = canvas.clientHeight;
      const size = heatSize(map.getLevel());
      if (size.radius !== radius) {
        radius = size.radius;
        heat.radius(radius / 2, radius / 2); // 원과 번짐을 합친 범위가 MapLibre의 반지름과 같다
      }
      const projection = map.getProjection();
      const data: HeatPoint[] = [];
      for (const coord of coords) {
        const { x, y } = projection.containerPointFromCoords(coord);
        if (x > -radius && y > -radius && x < canvas.width + radius && y < canvas.height + radius) {
          data.push([x, y, size.intensity]);
        }
      }
      heat.data(data).draw();
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(draw);
    };

    const events = ["center_changed", "zoom_changed", "bounds_changed", "idle"];
    for (const type of events) maps.event.addListener(map, type, schedule);
    const resize = new ResizeObserver(schedule);
    resize.observe(canvas);
    draw(); // 첫 그림은 바로 그린다 (탭이 숨겨져 있으면 requestAnimationFrame이 멈춘다)
    return () => {
      cancelAnimationFrame(frame);
      for (const type of events) maps.event.removeListener(map, type, schedule);
      resize.disconnect();
      canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
    };
  }, [instance, result, view]);

  // MapLibre 화면의 확대·축소 버튼. 카카오 레벨은 작을수록 확대이고, 일반 지도의 범위는 1~14다.
  function zoom(delta: 1 | -1) {
    if (!instance) return;
    const { map } = instance;
    map.setLevel(Math.min(14, Math.max(1, map.getLevel() - delta)), { animate: true });
  }

  // MapLibre 화면의 ↺ 버튼: 결과가 있으면 결과 전체에, 없으면 서울 기본 위치로 돌아간다.
  function reset() {
    if (!instance) return;
    if (result) fitResult(instance, result);
    else goHome(instance);
  }

  const resetLabel = result ? "분석 범위 전체 보기" : "서울 기본 위치로 돌아가기";

  return (
    <>
      <div
        ref={elementRef}
        className="map kakao-map"
        role="img"
        aria-label={result ? `${result.map_layer.metric_label} 지도` : "서울 기본 지도"}
      />
      <canvas ref={heatRef} className="kakao-heat" hidden={view !== "heatmap" || !result} aria-hidden="true" />
      {instance && (
        <div className="maplibregl-ctrl-top-right">
          <div className="maplibregl-ctrl maplibregl-ctrl-group">
            <button type="button" className="maplibregl-ctrl-zoom-in" title="확대" aria-label="확대" onClick={() => zoom(1)}>
              <span className="maplibregl-ctrl-icon" aria-hidden="true" />
            </button>
            <button type="button" className="maplibregl-ctrl-zoom-out" title="축소" aria-label="축소" onClick={() => zoom(-1)}>
              <span className="maplibregl-ctrl-icon" aria-hidden="true" />
            </button>
          </div>
          <div className="maplibregl-ctrl maplibregl-ctrl-group">
            <button type="button" className="p4-reset-view" title={resetLabel} aria-label={resetLabel} onClick={reset}>
              ↺
            </button>
          </div>
          <div className="maplibregl-ctrl maplibregl-ctrl-group">
            <button
              type="button"
              className="kakao-satellite-toggle"
              title="위성 사진"
              aria-label="위성 사진"
              aria-pressed={satellite}
              onClick={() => setSatellite((on) => !on)}
            >
              위성
            </button>
          </div>
        </div>
      )}
      {(loading || error) && (
        <div className="kakao-map-status" role={error ? "alert" : "status"}>
          {error ? (
            <>
              <strong>카카오 지도를 불러오지 못했습니다.</strong>
              <span>{error}</span>
              <small>카카오맵 사용 설정, JavaScript SDK 도메인 등록, 키 종류를 확인해 주세요.</small>
            </>
          ) : (
            <span>카카오 지도를 불러오는 중입니다…</span>
          )}
        </div>
      )}
    </>
  );
}
