import { useEffect, useRef } from "react";
import {
  Map as MapLibreMap,
  NavigationControl,
  setWorkerUrl,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { MAP_CHROME, type ColorScheme } from "../theme";
import { MapResetControl } from "./MapResetControl";

setWorkerUrl(workerUrl);

const INITIAL_CENTER: [number, number] = [126.978, 37.5665];
const INITIAL_ZOOM = 10.5;

export function BaseMap({ scheme }: { scheme: ColorScheme }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;

    const map = new MapLibreMap({
      container: container.current,
      style: MAP_CHROME[scheme].basemapStyle,
      center: INITIAL_CENTER,
      zoom: INITIAL_ZOOM,
    });

    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    map.addControl(
      new MapResetControl(
        () => map.easeTo({ center: INITIAL_CENTER, zoom: INITIAL_ZOOM, duration: 350 }),
        "서울 기본 위치로 돌아가기",
      ),
      "top-right",
    );

    return () => map.remove();
  }, [scheme]);

  return (
    <div
      ref={container}
      className="map"
      aria-label="서울 기본 지도"
    />
  );
}
