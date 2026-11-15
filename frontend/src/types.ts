// 백엔드 backend/app/api/schemas.py · backend/app/domain/results.py 와 같은 계약
import type { FeatureCollection, MultiPolygon } from "geojson";

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
export type MapView = "choropleth" | "proportional_symbol";

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

export interface MapLayer {
  type: "choropleth";
  metric: string;
  metric_label: string;
  crs: string;
  classification: { method: "quantile"; class_count: number; breaks: number[]; min: number | null; max: number | null };
  feature_count: number;
  null_count: number;
  geojson: FeatureCollection<MultiPolygon, UnitProperties>;
  /** 이 기능 전에 저장된 실행 기록에는 없다 (theme.ts layerStyle이 예전 표시로 채운다) */
  style?: MapStyle;
}

export interface DatasetUsage {
  dataset_id: string;
  title: string;
  provider: string;
  source: string;
  license: string | null;
  reference_date: string | null;
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
