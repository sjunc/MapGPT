/** 지도를 결과·지역에 맞출 때 지도 위 UI가 가리는 쪽을 비우는 여백 (px). MapLibre 지도와 카카오 지도가 함께 쓴다. */
export interface FitPadding {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

// 데스크톱에서 오른쪽 아래 범례가 결과 지역을 가리지 않도록, 지도를 맞출 때 범례 윗변 아래를 비운다.
// 모바일에서는 범례가 지도 위에 없다(크기 0).
function legendClearance(rect: DOMRect): number {
  const legend = document.querySelector<HTMLElement>(".map-legend")?.getBoundingClientRect();
  return legend && legend.height > 0 ? Math.ceil(rect.bottom - legend.top + 16) : 0;
}

/**
 * 결과 패널이 열려 있으면 데스크톱은 패널 오른쪽까지(왼쪽 여백), 모바일은 시트 윗변까지(아래 여백)를 비운다.
 * 그 밖에는 아래를 범례 윗변까지(최소 110px) 비우고, 지도가 가로·세로 80px 이상 남도록 여백을 줄인다.
 */
export function fitPadding(container: HTMLElement, panelOpen: boolean): FitPadding {
  const rect = container.getBoundingClientRect();
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
    mobile && panelRect ? Math.ceil(rect.bottom - panelRect.top + 24) : Math.max(110, legendClearance(rect)),
    rect.height - top - 80,
  ));
  return { top, right, bottom, left };
}
