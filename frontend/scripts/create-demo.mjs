import { readFile, writeFile } from "node:fs/promises";

const input = "public/demo/seoul-boundaries-2015.geojson";
const output = "public/demo/analysis.json";
const boundaries = JSON.parse(await readFile(input, "utf8"));

const rows = boundaries.features
  .filter((feature) =>
    (feature.geometry?.type === "Polygon" || feature.geometry?.type === "MultiPolygon") &&
    feature.properties?.EMD_CD && feature.properties?.EMD_KOR_NM,
  )
  .map((feature, index) => {
    const population = 500 + ((index * 67) % 2100);
    const facilities = 1 + ((index * 17) % 18);
    const value = Number(((facilities / population) * 1000).toFixed(2));
    const properties = {
      unit_key: String(feature.properties.EMD_CD),
      unit_name: String(feature.properties.EMD_KOR_NM),
      parent_name: null,
      null_reason: null,
      children_count: population,
      daycare_count: facilities,
      daycare_per_1000_children: value,
      demo_rank: null,
    };
    return {
      type: "Feature",
      geometry: {
        type: "MultiPolygon",
        coordinates: feature.geometry.type === "Polygon"
          ? [feature.geometry.coordinates]
          : feature.geometry.coordinates,
      },
      properties,
    };
  });

if (!rows.length) throw new Error("서울 경계 데이터를 읽지 못했습니다.");

const byValue = [...rows].sort(
  (a, b) => a.properties.daycare_per_1000_children - b.properties.daycare_per_1000_children ||
    a.properties.unit_key.localeCompare(b.properties.unit_key),
);
byValue.forEach((feature, index) => { feature.properties.demo_rank = index + 1; });

const values = byValue.map((feature) => feature.properties.daycare_per_1000_children);
const breaks = [...new Set([1, 2, 3, 4].map((part) => values[Math.floor((values.length * part) / 5)]))];
const runId = "demo-local";
const metric = "daycare_per_1000_children";
const payload = {
  run_id: runId,
  status: "succeeded",
  errors: [],
  result: {
    run_id: runId,
    result_id: "demo-static",
    question: "서울에서 어린이 인구 대비 어린이집이 부족한 동을 알려줘.",
    summary: "프로토타입 시연 결과입니다. 서울의 2015년 법정동 경계를 사용했으며, 인구·어린이집 수와 순위는 모두 가상 수치입니다. 실제 공급 부족 지역을 의미하지 않습니다.",
    metric: { name: metric, label: "어린이 1,000명당 어린이집 수 (가상)", interpretation: "값이 낮을수록 공급이 적은 것으로 표시하는 데모입니다. 실제 통계가 아닙니다." },
    metric_labels: {
      [metric]: "어린이 1,000명당 어린이집 수 (가상)",
      children_count: "어린이 인구 (가상)",
      daycare_count: "어린이집 수 (가상)",
    },
    rank_metric: "demo_rank",
    ranking: byValue.slice(0, 10).map((feature) => ({
      rank: feature.properties.demo_rank,
      unit_key: feature.properties.unit_key,
      unit_name: feature.properties.unit_name,
      parent_name: null,
      value: feature.properties[metric],
      values: {
        [metric]: feature.properties[metric],
        children_count: feature.properties.children_count,
        daycare_count: feature.properties.daycare_count,
        demo_rank: feature.properties.demo_rank,
      },
    })),
    map_layer: {
      type: "choropleth",
      metric,
      metric_label: "어린이 1,000명당 어린이집 수 (가상)",
      crs: "EPSG:4326",
      classification: { method: "quantile", class_count: breaks.length + 1, breaks, min: values[0], max: values.at(-1) },
      feature_count: rows.length,
      null_count: 0,
      geojson: { type: "FeatureCollection", features: rows },
    },
    analysis_method: [
      "2015년 법정동 경계를 지도에 표시합니다. 이 경계는 최신 행정동 경계와 다를 수 있습니다.",
      "각 구역에 프로토타입용 가상 인구와 가상 시설 수를 배정합니다.",
      "가상 시설 수 ÷ 가상 인구 × 1,000을 계산하고, 낮은 값부터 순위를 매깁니다.",
    ],
    datasets: [
      { dataset_id: "demo-seoul-2015-boundary", title: "서울 법정동 경계 (2015년)", provider: "southkorea/seoul-maps (원천: JUSO)", source: "https://github.com/southkorea/seoul-maps", license: "Apache-2.0 (가공 저장소)", reference_date: "2015" },
      { dataset_id: "demo-synthetic-stats", title: "가상 어린이 인구·어린이집 수", provider: "MAPGPT 프로토타입", source: "시연용 생성 데이터. 실제 공공통계가 아닙니다.", license: null, reference_date: null },
    ],
    assumptions: ["데모 지표는 화면 구현 검증을 위한 가상 수치입니다."],
    limitations: ["실제 의료·보육 시설 수급을 평가하는 근거로 사용할 수 없습니다.", "경계는 2015년 법정동 기준으로 최신 행정동 경계와 일치하지 않습니다."],
  },
};

await writeFile(output, JSON.stringify(payload));
console.log(`Demo response created: ${rows.length} features -> ${output}`);
