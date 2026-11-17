import type { MultiPolygon, Polygon, Position } from "geojson";

/** GeoJSON의 [경도, 위도]를 카카오의 (위도, 경도)로 변환한다. */
export function toKakaoLatLng(
  position: Position,
  maps: typeof kakao.maps,
): kakao.maps.LatLng {
  return new maps.LatLng(position[1], position[0]);
}

/**
 * Polygon 및 MultiPolygon을 카카오 Polygon의 경로 배열들로 변환한다.
 * 하나의 Polygon에 외곽선과 내부 링(구멍)이 있으면 모두 보존한다.
 */
export function toKakaoPolygonPaths(
  geometry: Polygon | MultiPolygon,
  maps: typeof kakao.maps,
): kakao.maps.LatLng[][][] {
  const polygons = geometry.type === "Polygon"
    ? [geometry.coordinates]
    : geometry.coordinates;

  return polygons.map((rings) =>
    rings.map((ring) => ring.map((position) => toKakaoLatLng(position, maps))),
  );
}
