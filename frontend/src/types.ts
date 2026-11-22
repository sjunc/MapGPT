// 백엔드 backend/app/api/schemas.py · backend/app/domain/results.py 와 같은 계약
import type { FeatureCollection, MultiPolygon, Point, Polygon } from "geojson";

export type MetricValue = number | null;

/** 분석 범위 (backend AnalysisScope). 질문마다 함께 보낸다. */
export type AnalysisScope = "seoul" | "national";

export interface RankingItem {
  rank: number;
  unit_key: string;
  unit_name: string;
  parent_name: string | null;
  value: number;
  values: Record<string, MetricValue>;
}

export interface UnitProperties {
  unit_key: string;
  unit_name: string;
  parent_name: string | null;
  null_reason: string | null;
  /** 비례 원을 그릴 단위 안의 대표점 [경도, 위도]. 이 기능 전에 저장된 실행 기록에는 없다 */
  label_point?: [number, number];
  [metric: string]: MetricValue | string | null | [number, number] | undefined;
}

/** 결과 지도를 그리는 방식 (backend MapView) */
export type MapView = "choropleth" | "proportional_symbol" | "point" | "heatmap" | "hexbin" | "bivariate";

/** 지도 지표의 성격과 순위 방향으로 백엔드가 정한 지도 보기 (backend MapStyle) */
export interface MapStyle {
  default_view: MapView;
  /** 화면에서 바꿔 볼 수 있는 보기 (기본 보기 포함) */
  views: MapView[];
  /** 진하게 강조할 쪽 값 (순위와 같은 방향) */
  emphasis: "low" | "high";
  legend_low: string;
  legend_high: string;
  /** 이 보기를 고른 이유 */
  note: string;
}

export interface FacilityProperties {
  /** 시설 이름 (Dataset에 이름 컬럼이 없으면 null) */
  name: string | null;
  /** 이 점을 센 분석 단위 */
  unit_key: string;
}

/** 지도 지표로 센 Point(시설)의 위치 (backend PointLayer). 점 지도·밀도 지도에 쓴다 */
export interface PointLayer {
  dataset_id: string;
  title: string;
  count: number;
  geojson: FeatureCollection<Point, FacilityProperties>;
}

/** 분위 구간 (backend Classification). 구간은 [경계값 이상, 다음 경계값 미만)이다 */
export interface Classification {
  method: "quantile";
  class_count: number;
  breaks: number[];
  min: number | null;
  max: number | null;
}

/** 지도 지표로 센 점을 같은 크기 육각형 칸마다 센 격자 (backend HexLayer). 점이 있는 칸만 온다 */
export interface HexLayer {
  /** 육각형 한 변 길이(m) */
  cell_size_m: number;
  cell_area_km2: number;
  count: number;
  classification: Classification;
  geojson: FeatureCollection<Polygon, { count: number }>;
}

export interface BivariateAxis {
  metric: string;
  label: string;
  /** 3분위 경계값 (최대 2개, 같은 값이 많으면 줄어든다) */
  breaks: number[];
}

/** 비율 지표의 분모(x)·분자(y)를 3분위씩 나눈 이변량 지도 구간 (backend BivariateSpec) */
export interface BivariateSpec {
  x: BivariateAxis;
  y: BivariateAxis;
}

export interface MapLayer {
  type: "choropleth";
  metric: string;
  metric_label: string;
  crs: string;
  classification: Classification;
  feature_count: number;
  null_count: number;
  geojson: FeatureCollection<MultiPolygon, UnitProperties>;
  /** 이 기능 전에 저장된 실행 기록에는 없다 (theme.ts layerStyle이 예전 표시로 채운다) */
  style?: MapStyle;
  /** 지도 지표가 점 개수일 때만 있다 (점이 너무 많으면 없다). 이 기능 전에 저장된 실행 기록에는 없다 */
  points?: PointLayer | null;
  /** 지도 지표가 점 개수일 때만 있다 */
  hexbins?: HexLayer | null;
  /** 지도 지표가 비율일 때만 있다 */
  bivariate?: BivariateSpec | null;
}

export interface DatasetUsage {
  dataset_id: string;
  title: string;
  provider: string;
  source: string;
  license: string | null;
  reference_date: string | null;
  /** 포털 정보로 자동 등록한 출처(사람 검토 전)면 false. 데모 데이터에는 없다 */
  reviewed?: boolean;
}

export interface FinalResponse {
  run_id: string;
  result_id: string;
  question: string;
  summary: string;
  metric: { name: string; label: string; interpretation: string };
  metric_labels: Record<string, string>;
  rank_metric: string | null;
  ranking: RankingItem[];
  map_layer: MapLayer;
  analysis_method: string[];
  datasets: DatasetUsage[];
  assumptions: string[];
  limitations: string[];
}

export interface ValidationIssue {
  code: string;
  severity: "error" | "warning";
  message: string;
  path: string | null;
}

export interface WorkflowError {
  step: string | null;
  code: string;
  message: string;
  issues: ValidationIssue[];
}

export interface AnalysisResponse {
  run_id: string;
  status: "running" | "succeeded" | "failed";
  result: FinalResponse | null;
  errors: WorkflowError[];
}

/** GET /api/stats?days=7 — 최근 실행 기록 통계 (backend/app/api/schemas.py RunStats) */
export interface RunStats {
  days: number;
  since: string;
  total: number;
  succeeded: number;
  failed: number;
  success_rate: number | null;
  duration_p50_seconds: number | null;
  duration_p90_seconds: number | null;
  top_error_codes: { code: string; runs: number }[];
  by_llm_model: { llm_model: string; runs: number }[];
}
