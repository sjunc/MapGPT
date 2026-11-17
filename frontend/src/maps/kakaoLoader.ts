/** 카카오 지도 SDK를 한 번만 불러오고 완료까지 기다린다. */
let sdkPromise: Promise<typeof kakao.maps> | null = null;

function availableMaps(): typeof kakao.maps | undefined {
  return (window as Window & { kakao?: typeof kakao }).kakao?.maps;
}

export function loadKakaoMaps(): Promise<typeof kakao.maps> {
  if (sdkPromise) return sdkPromise;

  const key = import.meta.env.VITE_KAKAO_MAP_APP_KEY;
  if (!key) {
    return Promise.reject(new Error("VITE_KAKAO_MAP_APP_KEY 환경변수가 없습니다."));
  }

  sdkPromise = new Promise<typeof kakao.maps>((resolve, reject) => {
    const existing = availableMaps();
    if (existing && typeof existing.Map === "function") {
      resolve(existing);
      return;
    }

    const script = document.createElement("script");
    script.async = true;
    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(key)}&autoload=false`;

    let settled = false;
    const timeoutId = window.setTimeout(() => {
      fail(new Error("카카오지도 SDK 로딩 시간이 초과됐습니다."));
    }, 15000);

    const succeed = (maps: typeof kakao.maps) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeoutId);
      resolve(maps);
    };
    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeoutId);
      script.remove();
      sdkPromise = null;
      reject(error);
    };

    script.onerror = () => {
      fail(new Error("카카오지도 SDK 요청에 실패했습니다. JavaScript 키, 카카오맵 활성화 및 SDK 도메인을 확인하세요."));
    };
    script.onload = () => {
      const maps = availableMaps();
      if (!maps || typeof maps.load !== "function") {
        fail(new Error("카카오지도 SDK 초기화 함수를 찾지 못했습니다."));
        return;
      }
      try {
        maps.load(() => {
          if (typeof maps.Map === "function") succeed(maps);
          else fail(new Error("카카오지도 SDK 로딩 후 Map 객체가 없습니다."));
        });
      } catch (error) {
        fail(error instanceof Error ? error : new Error(String(error)));
      }
    };
    document.head.appendChild(script);
  });

  return sdkPromise;
}
