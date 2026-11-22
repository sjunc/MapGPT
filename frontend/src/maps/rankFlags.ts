import type { FinalResponse, UnitProperties } from "../types";
import "./rank-flags.css";

/** 깃발을 다는 순위. 동순위(1,2,2,4)는 모두 단다. */
const FLAG_MAX_RANK = 3;

/** 깃발 그림의 크기(px). rank-flags.css의 크기와 같다 */
export const FLAG_SIZE = { width: 36, height: 36 };

type Position = [number, number];

export interface RankFlag {
  rank: number;
  properties: UnitProperties;
  /** 깃대를 꽂을 단위의 대표점 [경도, 위도] */
  position: Position;
  /** 천을 펼치는 쪽 */
  side: "left" | "right";
}

/** 가장 가까운 다른 깃발의 반대쪽. 붙어 있는 두 지역(행정동처럼 작은 단위)의 깃발이 서로 덮지 않게 한다. */
function clothSide(position: Position, others: Position[]): RankFlag["side"] {
  let nearest: Position | null = null;
  let best = Infinity;
  for (const other of others) {
    const distance = (other[0] - position[0]) ** 2 + (other[1] - position[1]) ** 2;
    if (distance < best) {
      best = distance;
      nearest = other;
    }
  }
  return nearest && nearest[0] > position[0] ? "left" : "right";
}

/**
 * 순위 결과에서 깃발을 달 1~3위 지역. 순위 목록(result.ranking)에 있는 지역만 써서 깃발과 목록이 늘 같은 지역을 가리킨다.
 * 순위가 없는 결과와, 대표점(label_point)이 없는 예전 실행 기록에는 깃발이 없다.
 * 1위가 맨 위에 그려지도록 3위부터 돌려준다.
 */
export function rankFlags(result: FinalResponse): RankFlag[] {
  const units = new Map(result.map_layer.geojson.features.map((feature) => [feature.properties.unit_key, feature.properties]));
  const placed = result.ranking
    .filter((item) => item.rank <= FLAG_MAX_RANK)
    .flatMap((item) => {
      const properties = units.get(item.unit_key);
      return properties?.label_point ? [{ rank: item.rank, properties, position: properties.label_point }] : [];
    });
  return placed
    .map((flag, index) => ({
      ...flag,
      side: clothSide(flag.position, placed.filter((_, other) => other !== index).map((item) => item.position)),
    }))
    .reverse();
}

/** 깃발 그림 안에서 지역 대표점에 오는 깃대 밑동의 위치(px) */
export function flagFoot(flag: RankFlag): { x: number; y: number } {
  return { x: flag.side === "left" ? FLAG_SIZE.width - 3 : 3, y: 33 };
}

// 천이 오른쪽인 깃발. 왼쪽 깃발은 도형만 좌우로 뒤집고 글자는 그대로 둔다.
const FLAG_SHAPES =
  '<path class="rank-flag-halo" d="M3 33V4H33L28 11.5L33 19H3"/>' +
  '<path class="rank-flag-cloth" d="M3 4H33L28 11.5L33 19H3Z"/>' +
  '<path class="rank-flag-pole" d="M3 33V4"/>' +
  '<circle class="rank-flag-foot" cx="3" cy="33" r="2.25"/>';

/** 깃대와 "N위" 천으로 된 깃발 요소. 어느 지역의 깃발인지는 data-unit-key에 둔다. */
export function rankFlagElement(flag: RankFlag): HTMLDivElement {
  const { width, height } = FLAG_SIZE;
  const left = flag.side === "left";
  const element = document.createElement("div");
  element.className = "rank-flag";
  element.dataset.unitKey = flag.properties.unit_key;
  element.innerHTML =
    `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">` +
    `<g${left ? ` transform="matrix(-1 0 0 1 ${width} 0)"` : ""}>${FLAG_SHAPES}</g>` +
    `<text class="rank-flag-label" x="${left ? width - 16 : 16}" y="16"></text></svg>`;
  element.querySelector("text")!.textContent = `${flag.rank}위`;
  return element;
}

/** 이벤트가 일어난 곳이 순위 깃발이면 그 깃발이 가리키는 지역 키 */
export function flagUnitKey(target: EventTarget | null): string | null {
  return (target instanceof Element && target.closest<HTMLElement>(".rank-flag")?.dataset.unitKey) || null;
}
