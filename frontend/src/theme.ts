import { useEffect, useState } from "react";
import type { MapLayer, MapStyle } from "./types";

export type ColorScheme = "light" | "dark";

// 5분위 단일 색상(blue) ordinal 램프. 인덱스 0이 가장 눈에 띄는 색이다(라이트는 진하게, 다크는 밝게).
// 강조할 쪽(공급 부족처럼 작은 값, 또는 많은 곳처럼 큰 값)에 인덱스 0을 준다. dataviz validate_palette.js --ordinal 통과.
export const CLASS_COLORS: Record<ColorScheme, string[]> = {
  light: ["#104281", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef"],
  dark: ["#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"],
};

// 배경지도: OpenFreeMap (API 키 불필요, © OpenMapTiles · OpenStreetMap)
export const MAP_CHROME: Record<
  ColorScheme,
  { noData: string; separator: string; hover: string; symbol: string; outline: string; basemapStyle: string }
> = {
  light: {
    noData: "#c3c2b7",
    separator: "#fcfcfb",
    hover: "#0b0b0b",
    symbol: "#2a78d6", // 비례 원
    outline: "#8d8b82", // 비례 원 보기의 지역 경계선
    basemapStyle: "https://tiles.openfreemap.org/styles/positron",
  },
  dark: {
    noData: "#383835",
    separator: "#1a1a19",
    hover: "#ffffff",
    symbol: "#6da7ec",
    outline: "#8a8980",
    basemapStyle: "https://tiles.openfreemap.org/styles/dark",
  },
};

/**
 * 작은 값 구간부터 큰 값 구간 순서의 색. 강조할 쪽 끝이 가장 눈에 띄는 색이다.
 * 구간 수가 5보다 적을 때(동일 값이 많을 때)도 램프 양 끝을 유지하도록 색을 고른다.
 */
export function classColors(scheme: ColorScheme, classCount: number, emphasis: MapStyle["emphasis"] = "low"): string[] {
  const ramp = emphasis === "low" ? CLASS_COLORS[scheme] : [...CLASS_COLORS[scheme]].reverse();
  if (classCount <= 1) return [ramp[2]];
  return Array.from({ length: classCount }, (_, i) => ramp[Math.round((i * (ramp.length - 1)) / (classCount - 1))]);
}

// 이 기능 전에 저장된 실행 기록은 지도 보기 정보가 없어 예전처럼 단계 구분도로만 그린다.
const LEGACY_STYLE: MapStyle = {
  default_view: "choropleth",
  views: ["choropleth"],
  emphasis: "low",
  legend_low: "낮음",
  legend_high: "높음",
  note: "",
};

export function layerStyle(layer: MapLayer): MapStyle {
  return layer.style ?? LEGACY_STYLE;
}

/** 비례 원의 최대 반지름(px). 단위가 많을수록(행정동 400여 곳) 원끼리 덜 겹치도록 줄인다. */
export function symbolMaxRadius(featureCount: number): number {
  return Math.max(10, Math.min(30, 160 / Math.sqrt(Math.max(featureCount, 1))));
}

export function useColorScheme(): ColorScheme {
  const query = "(prefers-color-scheme: dark)";
  const [scheme, setScheme] = useState<ColorScheme>(() => (window.matchMedia(query).matches ? "dark" : "light"));
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setScheme(media.matches ? "dark" : "light");
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return scheme;
}

export function formatValue(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "–";
  return Number.isInteger(value) ? value.toLocaleString("ko-KR") : value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export const NULL_REASONS: Record<string, string> = {
  denominator_zero: "분모(인구)가 0",
  denominator_missing: "인구 자료 없음",
  numerator_missing: "시설 자료 없음",
  no_matching_records: "자료 없음",
  input_null: "지표 값 없음",
};
