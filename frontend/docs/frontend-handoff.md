# 화면 현황과 향후 과제 — 프론트엔드 담당자 전달 문서

- 작성일: 2026-10-09
- 기준 코드: `main` 브랜치 `2df606c`
- 대상: 화면(`frontend/`)을 넘겨받을 프론트엔드 담당자

이 문서는 세 가지를 담는다.

1. 넘겨받는 화면 초안의 상태와, 백엔드·에이전트와 맞물린 계약 (사실)
2. 아직 없는 것과 개발 쪽 의견 (제안)
3. 프론트엔드 담당자가 결정해 줄 사항 (결정)

화면의 구조와 동작(컴포넌트, 상태 흐름, 지도 렌더링)은 [에이전트 설계 개요 7장](../../backend/docs/agent-design-overview.md#7-프론트엔드)에 자세히 있어 여기서는 반복하지 않는다. 권장안은 개발 쪽 의견이며 결정은 담당자가 한다.

## 요약

- **현재:** Vite + React + TypeScript + MapLibre로 만든 초안이다.
  - 질문을 입력하면 `POST /api/analyses`를 한 번 보내고, 응답으로 지도·순위·요약·분석 방법·가정·한계를 그린다.
  - `src`는 약 770줄이고, 런타임 라이브러리는 React와 MapLibre뿐이다.
  - 타입 검사와 빌드는 통과한다.
- **지도:** 팀은 처음에 카카오맵을 쓰기로 했지만, 구현 기준이었던 개발 전달 문서(23·29·42번)가 MapLibre를 명시해 MapLibre로 만들었다. 어느 쪽으로 갈지는 담당자가 정한다(F1). 바꾸더라도 지도 컴포넌트 하나만 다시 만들면 된다.
- **카카오 지도 화면 (2026-11-21):** `?map=kakao`로 여는 카카오맵 화면이 따로 있고, Vercel 배포의 `/`는 이 화면의 가상 데모로 간다. MapLibre 화면과 같은 지도 보기·주소(`run`)·최근 실행·실패 상세·실행 기록을 갖췄다([3.5절](#35-카카오-지도-화면-2026-11-21)).
- **첫 분석 주제에 묶인 부분:** 순위 표의 "행정동" 같은 문구가 코드에 고정되어 있다. 범례 양끝 문구와 색 방향은 응답(`map_layer.style`)에서 받는다.
- **지도 보기 (2026-11-15):** 개수·합계 지표는 비례 원이 기본이고 "원 크기"·"색 구간" 버튼으로 단계 구분도와 바꿔 볼 수 있다. 비율·평균은 단계 구분도만 쓴다. 보기와 강조 방향은 백엔드가 정해 `map_layer.style`로 보내고, 원 위치는 각 단위의 `properties.label_point`다. 지표가 시설 개수면 `map_layer.points`(시설 위치)가 함께 와서 "점"(가까운 곳은 묶음)·"밀도" 보기가 더해지고, 위치를 물으면 점, 밀집한 곳을 물으면 밀도가 기본이다. 같은 크기 육각형 칸마다 센 `map_layer.hexbins`로 "육각 격자" 보기도 있고, 점이 1만 개를 넘어 위치가 오지 않으면 위치·밀집 질문은 육각 격자가 기본이다. 비율 지표는 `map_layer.bivariate`(분모·분자 3분위)로 "두 지표"(이변량) 보기를 더한다(2026-11-16).
- **백엔드와 함께 정할 것:** 진행 상황 표시(지금은 경과 시간만), 배포 시 서빙 방식, 응답 타입 동기화.
- **결정 요청:** 6장. 먼저 정할 것은 지도 라이브러리(F1)와 진행 상황 표시 방식(F3)이다.

## 목차

1. [넘겨받는 것](#1-넘겨받는-것)
2. [백엔드·에이전트와의 계약](#2-백엔드에이전트와의-계약)
3. [지도: MapLibre와 카카오맵](#3-지도-maplibre와-카카오맵)
4. [첫 분석 주제에 묶인 부분](#4-첫-분석-주제에-묶인-부분)
5. [아직 없는 것과 개발 쪽 의견](#5-아직-없는-것과-개발-쪽-의견)
6. [프론트엔드 담당자 결정 사항](#6-프론트엔드-담당자-결정-사항)
7. [참고](#7-참고)

## 1. 넘겨받는 것

### 1.1 구성

| 구분 | 내용 |
|---|---|
| 스택 | Vite 8.3, React 19.3, TypeScript 7.0(strict), MapLibre GL 6.13 |
| 런타임 의존성 | `react`, `react-dom`, `maplibre-gl`, `simpleheat`(카카오 화면의 밀도 지도, 2026-11-21). 카카오 지도 SDK는 실행 중에 카카오 서버에서 불러온다. 상태 관리·UI·라우터 라이브러리는 없다 |
| 스타일 | `styles.css` 하나. 색은 CSS 변수로 두고, `prefers-color-scheme`으로 라이트·다크를 바꾼다. 860px 미만에서는 세로로 쌓는다 |
| 배경지도 | OpenFreeMap positron(라이트), dark(다크). API 키가 필요 없다 |
| 개발 서버 | `:5173`. `/api`를 FastAPI `:8000`으로 프록시한다(타임아웃 300초) |
| 로컬 검증 환경 | Node 23.11, npm 10.9. README는 Node 20 이상으로 안내한다 |

파일별 역할은 [개요 문서 7.2절](../../backend/docs/agent-design-overview.md#72-디렉터리-구조)에 있다. 크기는 다음과 같다.

| 파일 | 줄 수 |
|---|---|
| `ChoroplethMap.tsx` | 149 |
| `App.tsx` | 123 |
| `RankingTable.tsx` | 80 |
| `types.ts` | 78 |
| `theme.ts` | 61 |
| `ResultDetails.tsx` | 54 |
| `Legend.tsx` | 40 |
| `ErrorPanel.tsx` | 39 |
| `api.ts` | 15 |
| `styles.css` | 125 |

### 1.2 명령

```bash
cd frontend
npm install
npm run dev         # 개발 서버 :5173 (API 서버 :8000이 떠 있어야 함)
npm run typecheck   # tsc --noEmit
npm run build       # 타입 검사 후 frontend/dist에 빌드
```

API 서버와 데이터 준비는 [README](../../backend/README.md)와 [백엔드 담당자 전달 문서 3.3절](../../backend/docs/backend-handoff.md#33-실행-방법)에 있다.

### 1.3 빌드 결과

| 파일 | 크기 | gzip |
|---|---|---|
| `index-*.js` (앱 + React + MapLibre) | 1,276KB | 355KB |
| `maplibre-gl-worker-*.js` | 508KB | (보고 없음) |
| `index-*.css` (대부분 MapLibre CSS) | 88KB | 12KB |

Vite가 "500KB를 넘는 청크" 경고를 낸다.

### 1.4 확인된 것과 없는 것

- **확인된 것:**
  - 실제 데이터(행정동 427개)로 지도·표·범례·툴팁이 동작한다.
  - 표와 지도 강조가 양방향으로 연동된다.
  - 라이트·다크 전환과 좁은 화면 배치가 동작한다.
  - 지도 색 램프는 라이트·다크 모두 순서형 팔레트 검사를 통과했다.
- **없는 것:**
  - 테스트, ESLint·Prettier 설정, CI가 없다. 검사는 타입 검사와 빌드뿐이고, 저장소 공통 작업 후 검증(`python scripts/check.py`)에 들어 있다.
  - `ChoroplethMap.tsx`에 `eslint-disable` 주석이 하나 있지만, ESLint는 설치되어 있지 않다.

## 2. 백엔드·에이전트와의 계약

### 2.1 API 사용

분리 배포에서는 빌드 환경 변수 `VITE_API_BASE_URL`에 공개 HTTPS API 주소를 지정한다(`/api` 제외).
`api.ts`의 `apiUrl`을 통해 분석 요청·기록 조회·원본 JSON 링크에 동일하게 적용한다. 비우면 개발용 `/api` 프록시를 쓴다.
백엔드는 `CORS_ALLOW_ORIGINS`에 화면 Origin을 등록해야 하며, Vercel 환경 변수를 변경하면 재배포한다.

| API | 화면에서 쓰는 곳 |
|---|---|
| `POST /api/analyses` | 분석 요청 ([api.ts](../src/api.ts)). 본문은 `{question, scope}`이고, `scope`는 헤더의 "분석 범위" 토글(서울·전국, 기본 서울, 이 브라우저에 기억)에서 온다 ([scope.ts](../src/scope.ts)) |
| `GET /api/analyses/{run_id}` | "실행 기록" 링크로 원본 JSON을 새 탭에 연다 |
| `GET /api/analyses/{run_id}/export?format=csv\|geojson` | 두 화면 결과 패널의 "결과 내려받기·공유"(`ResultShare.tsx`)가 링크로 연다(`api.ts`의 `exportUrl`). 서버가 attachment로 보내 fetch 없이 내려받는다. 데모에서는 보이지 않는다 |
| `GET /api/suggestions?scope=` | 빈 상태의 예시 질문(`SuggestionChips.tsx`). 지금 분석 범위로 받고, 누르면 입력창을 채우고 바로 분석한다. 불러오지 못하면 보이지 않는다 |
| `GET /api/datasets`, `GET /api/health` | 쓰지 않는다 |

- **응답 처리:**
  - 분석이 실패해도 HTTP 200에 `status: "failed"`와 `errors[]`가 온다. `ErrorPanel`이 단계 이름, 코드, 메시지, 세부 issues를 보여준다. 데이터가 없다는 답(`DATA_NOT_AVAILABLE`, `NO_MATCHING_DATA`)이면 제목을 "데이터가 없습니다"로 바꿔 분석 실패와 구분한다.
  - 422(형식 오류)나 503(LLM·임베딩 설정 누락) 같은 HTTP 오류는 "요청을 처리하지 못했습니다" 카드에 `detail`을 보여준다.
- **단계 이름:** `errors[].step`은 백엔드 워크플로의 9개 단계 이름이다. 화면은 `ErrorPanel.tsx`의 `STEP_NAMES`로 한국어 이름을 붙이므로, 단계가 늘면 함께 고친다.
- **응답 크기와 시간:**
  - 성공 응답은 행정동 427개의 GeoJSON을 포함해 약 590KB다.
  - 걸리는 시간은 gpt-4o-mini 기준 15~21초, 로컬 LLM은 최대 3분 정도다.
  - 개발 서버 프록시는 300초에 끊는다.

### 2.2 타입 동기화

[frontend/src/types.ts](../src/types.ts)는 백엔드의 `app/api/schemas.py`, `app/domain/results.py`, `app/domain/state.py`를 손으로 옮긴 것이다. 코드 생성이 없으므로 한쪽을 바꾸면 다른 쪽도 함께 바꿔야 한다. FastAPI가 `/openapi.json`을 제공하므로 타입 생성 도구로 바꿀 수 있다(F10). 다만 `GET /api/analyses/{run_id}`는 응답 스키마가 느슨하다.

### 2.3 화면이 지켜야 할 것

1. **필수 표시 항목:** 개발 전달 문서 22번이 요구하는 항목을 모두 보여준다. 지금은 모두 있다.
   - 지도, 순위, 분석 지표, 사용 데이터, 데이터 기준 시점, 분석 방법, 가정, 한계
2. **수치는 응답 값 그대로:**
   - 순위, 분위 경계값, 결측 사유는 백엔드가 계산한 값을 그대로 쓰고, 화면에서 다시 계산하지 않는다.
   - 범례 구간은 `[경계값 이상, 다음 경계값 미만)`으로, 지도 색 식(MapLibre `step`)과 같다.
3. **LLM이 만든 문자열은 텍스트로만 넣는다:**
   - 해당 문자열은 요약, 지표·연산 라벨, 가정, 지원하지 않는 이유, 필요한 데이터 이름(오류 메시지와 실행 기록)이다.
   - React는 기본으로 이스케이프하고, 지도 툴팁(HTML 문자열)은 `escapeHtml`을 거친다.
   - 이 문자열을 `dangerouslySetInnerHTML`이나 HTML 조립에 그대로 넣지 않는다.
4. **출처·라이선스 표시:** 사용 데이터 카드에 각 데이터의 라이선스를 보여준다.
   - 어린이집 데이터는 **공공누리 제4유형(출처표시, 상업적 이용금지, 변경금지)**이라 출처 표시가 필요하다.
   - 지도 하단의 배경지도 표기(OpenMapTiles, OpenStreetMap)와 "경계: 통계청 SGIS"를 없애지 않는다.
5. **색:**
   - 램프는 단일 색상 5단계이고, `map_layer.style.emphasis` 쪽 값(공급 부족처럼 작은 값, 또는 많은 곳처럼 큰 값)이 가장 눈에 띈다. 라이트는 진할수록, 다크는 밝을수록 강조된다.
   - 비례 원은 한 가지 색이고, 원 넓이가 값에 비례한다(반지름은 제곱근).
   - 점 지도의 점·묶음은 비례 원과 같은 색이고, 묶음 안 숫자는 `symbolText` 색이다.
   - 밀도 지도는 같은 파랑 7단계 연속 램프(`HEAT_COLORS`)이고, 밀도 0은 투명하다. 밀도는 화면 배율에 따라 상대적이다.
   - 육각 격자는 단계 구분도와 같은 램프이고, 칸 수가 많은 쪽이 항상 가장 눈에 띈다.
   - 이변량 지도는 파랑(분자)·주황(분모)을 섞은 3×3 색(`BIVARIATE_COLORS`)이다. 다크 모드는 일반 시각의 칸 구분이 약해(ΔE 10.3) 툴팁에 두 값이 함께 보인다.
   - 값 없음은 회색이다.
   - 색을 바꾸면 라이트·다크 모두에서 순서가 구분되는지 다시 확인한다.

## 3. 지도: MapLibre와 카카오맵

### 3.1 경위

팀에서는 처음에 카카오맵을 쓰기로 했다. 하지만 구현 기준으로 삼은 개발 전달 문서가 지도를 MapLibre로 명시했고, 카카오맵 결정은 이 문서에 반영되어 있지 않아 MapLibre로 구현했다.
- 23번(지도 출력): "React + MapLibre GL JS"
- 29번(UI 연결): MapLibre
- 42번(핵심 기술): MapLibre

### 3.2 이 화면의 지도에 필요한 것

- 행정동 427개를 값에 따라 칠하는 지도(Choropleth)
- 분위 구간별 색, 값 없음 회색
- 표와 지도의 hover 강조, 툴팁
- 지명 라벨을 가리지 않는 레이어 순서
- 라이트·다크 배경지도

### 3.3 비교

| 항목 | MapLibre + OpenFreeMap (현재) | 카카오맵 JavaScript API |
|---|---|---|
| GeoJSON | API 응답을 GeoJSON 소스로 그대로 올린다 | 공식 레퍼런스에서 GeoJSON을 바로 그리는 기능을 찾지 못했다. 좌표를 변환해 행정동마다 다각형 객체를 만들어야 한다 |
| 값에 따른 색 | 스타일 식(`step`) 하나로 지정 | 다각형마다 색을 계산해 지정 |
| 강조·툴팁 | `feature-state`와 레이어 이벤트 | 다각형마다 이벤트 연결 |
| 배경지도 스타일 | 스타일 URL만 바꾸면 라이트·다크 전환 | 배경지도 색·스타일을 바꾸는 공식 기능을 찾지 못했다 |
| 키·등록 | 필요 없다. MapLibre는 BSD-3 오픈소스이고 OpenFreeMap은 가입·키가 없다 | JavaScript 앱 키 발급과 사이트 도메인 등록이 필요하다. 등록한 도메인에서만 동작한다 |
| 호출 한도·비용 | 없다. OpenFreeMap은 요청 수 제한이 없고 상업 이용도 허용한다 | 지도 SDK 무료 쿼터는 하루 30만 건이고, 개발자 계정의 첫 번째 앱에만 준다. 넘으면 건당 0.1원이 과금되고, 유료 설정이 없으면 429 오류가 난다. 2026-07-21에 사용 방법과 무료 쿼터 방식이 바뀌었다 |
| 국내 지도 품질 | OpenStreetMap 기반이다. 국내 상호·건물 정보는 국내 포털 지도보다 적은 편이다 | 국내 상호·건물 정보가 풍부하고 사용자에게 익숙하다. 로드뷰·스카이뷰가 있다 |
| 확장 | 데이터가 커지면 벡터 타일로 넘어갈 수 있다 | 직접 만든 벡터 타일을 그리는 기능은 확인하지 못했다 |

- 카카오의 주소 검색·장소 검색은 지도 SDK와 별개인 REST API라서, MapLibre를 써도 함께 쓸 수 있다.
- 이 API는 데이터 담당자 문서의 지오코딩 결정(D16)에도 후보로 들어 있다.

### 3.4 바꿀 때 범위

- **다시 만들 것:**
  - `ChoroplethMap.tsx`(149줄)
  - `theme.ts`의 배경지도 설정(`MAP_CHROME.basemapStyle`)
- **그대로 두는 것:** 범례, 순위 표, 타입, API 호출. 백엔드와 에이전트도 영향이 없다. API는 GeoJSON을 그대로 돌려준다.
- **카카오맵으로 갈 때 할 일:**
  - 앱 키 발급과 개발·운영 도메인 등록
  - SDK 로드
  - GeoJSON MultiPolygon을 카카오 다각형으로 변환. 구멍 있는 경계를 어떻게 처리할지 확인해야 한다
  - 구간별 색 계산
  - 다각형별 hover·툴팁 연결
- **잃는 것:** 다크 배경지도
- **절충안:** MapLibre를 유지하면서 국내 배경지도 타일(예: 브이월드)로 바꾸는 방법도 있다. 키와 약관은 따로 확인해야 하고, 아직 검증하지 않았다.

### 3.5 카카오 지도 화면 (2026-11-21)

팀원이 만든 카카오맵 화면(FE/jsm)을 분석 API에 연결했고, MapLibre 화면과 같은 기능을 갖추도록 채웠다. Vercel 배포의 `/`는 이 화면의 가상 데모(`/?demo=1&map=kakao`)로 간다(`vercel.json`). MapLibre 화면(`App`)과 백엔드는 그대로다.

**주소:** `main.tsx`는 `map=kakao`가 있으면 카카오 화면을, 없으면 MapLibre 화면을 연다. 카카오 화면에서는 주소가 무엇을 보일지 정한다.

| 주소 | 화면 |
|---|---|
| `/?map=kakao` | 빈 지도와 안내 문구 |
| `/?map=kakao&run=<run_id>` | 저장된 실행. 성공이면 결과, 실패면 빈 지도와 오류 상세 |
| `/?demo=1&map=kakao` | 가상 데모(`public/demo/analysis.json`) |
| `/?run=<run_id>` | MapLibre 화면에서 같은 실행 |

- 분석하거나 실행을 열면 주소를 `?map=kakao&run=<id>`로 바꾼다(`pushState`). 새로고침과 뒤로·앞으로 가기는 같은 규칙으로 화면을 되살리고, 늦게 온 이전 응답은 버린다.
- "가상 데모로 돌아가기"는 주소를 `?demo=1&map=kakao`로 바꾼다. 데모 데이터는 늘 불러 두지만 주소가 데모일 때만 보인다.

**기능:** MapLibre 화면과 같다.

- 지도 보기는 단계 구분도, 비례 원, 점(시설 위치), 밀도, 육각 격자, 이변량이다.
  - 서버가 `map_layer.style.views`로 허용하고 필요한 데이터(`points`, `hexbins`, `bivariate`)가 있는 보기만 보인다.
  - 처음에는 서버 기본 보기로 연다.
  - 데모 데이터에는 점과 격자가 없어 데모는 원래 네 가지(단계 구분도·비례 원·점·이변량)만 쓴다.
- 최근 실행(`runHistory.ts`)은 이 브라우저에만 저장되고, MapLibre 화면과 같은 목록이다.
- 실패하면 `ErrorPanel`로 상세를 보인다. 직전 성공 결과는 지도에 남기고 "직전 결과"로 표시한다.
- 결과와 실패 모두 Agent 실행 기록(`ExecutionTrace`)과 원본 JSON 링크를 보인다.
- 범례는 구간 경계값, 육각형 칸 크기·넓이, 이변량 경계값, 서버가 보기를 고른 이유(`style.note`)를 보인다.
- 결과에 지도를 맞출 때 상단 바·보기 전환·지도 버튼·범례·질문 입력창의 위치를 재서 결과를 가리지 않게 여백을 둔다. 순위를 누르면 같은 여백으로 그 지역에 맞춘다.
- 확대·축소는 지도 버튼 묶음(전체 보기·선택 해제 옆)의 +/− 버튼으로 하고, 결과가 없어도 보인다. 카카오 기본 막대 컨트롤(`ZoomControl`)은 오른쪽 위의 지도 위 UI와 겹쳐 쓰지 않는다.
- 순위는 상위 10개를 보이고, "전체 순위 보기"로 모든 단위를 펼친다. 응답의 순위 값으로 정렬만 한다.

**카카오맵으로 그리는 방법:** 카카오 지도 API는 GeoJSON 레이어와 히트맵이 없어 보기마다 객체를 만든다.

| 보기 | 구현 |
|---|---|
| 단계 구분도·이변량 | 단위마다 `kakao.maps.Polygon`. 색은 서버 구간(`classification.breaks`, `bivariate.*.breaks`)과 `theme.ts`의 같은 색. 이변량의 시연용 팔레트는 데모에만 쓴다 |
| 비례 원 | `CustomOverlay`에 단 DOM 버튼. 반지름은 `symbolMaxRadius(feature_count) × √(값 ÷ 최댓값)`이고, 12px 미만인 원은 숫자 없이 `aria-label`만 단다 |
| 점 | 기본 마커와 `MarkerClusterer`(SDK `libraries=clusterer`, 평균 위치, 레벨 5 이상에서 묶음). 마커를 누르면 그 시설의 단위를 고른다 |
| 밀도 | 지도 위에 `pointer-events: none`인 canvas를 겹치고 `simpleheat`로 그린다. 지도가 움직이면 `requestAnimationFrame`으로 다시 그린다 |
| 육각 격자 | 칸마다 `Polygon`을 그리고, 아래에 단위 경계를 `Polyline`으로 깐다. 칸을 누르면 그 칸의 개수를 보인다 |

- 밀도 크기는 MapLibre와 같다. 줌 9→13에서 반지름 6→22px, 세기 0.12→0.9, 불투명도는 0.85다.
  - 카카오 레벨 L은 MapLibre 줌 약 18.8 − L로 바꿔 쓴다. 레벨 9의 축척 막대가 4km=58px(약 69m/px)이다.
  - simpleheat는 밀도를 불투명도로도 쓴다. 그래서 같은 매개변수에서도 드문 곳이 MapLibre보다 옅게 보인다.
- 화면에 보이는 LLM 문자열은 모두 React 텍스트나 `textContent`로 넣는다.

**환경 변수**

| 이름 | 쓰는 곳 | 내용 |
|---|---|---|
| `VITE_KAKAO_MAP_APP_KEY` | 화면 빌드(`frontend/.env`, Vercel 프로젝트 환경 변수) | 카카오 JavaScript 키. 공개 클라이언트 키만 넣는다. 화면을 여는 도메인(포트 포함)을 카카오 개발자 콘솔에 등록해야 지도가 뜨므로, 로컬 개발 서버도 등록한 포트로 띄운다 |
| `MAPGPT_BACKEND_URL` | 개발 서버(`vite.config.ts`)만 | `/api` 프록시 대상. 비우면 `vite.config.ts`의 기본 대상(외부 서버)으로 간다. 로컬 API를 쓰려면 `MAPGPT_BACKEND_URL=http://127.0.0.1:8000 npm run dev`로 띄운다. 브라우저 코드에는 들어가지 않는다 |
| `VITE_API_BASE_URL` | 화면 빌드 | 분리 배포의 API 주소([2.1절](#21-api-사용)). 비우면 `/api` 프록시를 쓴다 |

**아직 없는 것**

- 다크 모드가 없다. 화면은 라이트로 고정하고, 함께 쓰는 카드 컴포넌트도 라이트 색을 쓴다.
- 마우스를 올렸을 때의 툴팁이 없다.
- 진행 중인 실행을 자동으로 조회하지 않는다.
- 좁은 화면에서는 범례가 지도의 큰 부분을 가려, 결과를 맞출 영역이 작다. MapLibre 화면은 좁은 화면에서 범례를 결과 패널로 옮긴다.

## 4. 첫 분석 주제에 묶인 부분

지금 화면은 "인구 대비 시설 공급 부족을 행정동별로 보는" 첫 분석 주제에 맞춰 문구와 방향이 고정되어 있다. 두 번째 분석 주제(예: 지하철역 500m 밖 지역)나 다른 공간 단위가 생기면 고쳐야 한다.

| 위치 | 고정된 것 |
|---|---|
| `RankingTable.tsx` | (2026-11-22 고침) 제목은 "<순위 라벨> 상위 N개 지역"·"전체 N개 지역", 열 이름은 "지역". 공간 단위 이름이 응답에 생기면 "지역" 대신 쓴다 |
| `theme.ts` | 결측 사유 문구("분모(인구)가 0", "인구 자료 없음", "시설 자료 없음") |
| `App.tsx` | 예시 질문, 진행 표시용 단계 목록(7개, 실제 단계는 9개), 안내 문구 |

- **지금 응답에 있는 것:**
  - 지표 이름·라벨·해석 문장(`metric.interpretation`, 예: "값이 작을수록 공급 부족")
  - 순위 지표 이름(`rank_metric`)
  - 지도 보기와 값의 방향(`map_layer.style`: 기본 보기, 바꿔 볼 수 있는 보기, 강조할 쪽, 범례 양끝 문구, 고른 이유). 범례와 지도 색은 이 값을 쓴다(2026-11-15)
  - 지도 지표로 센 시설 위치(`map_layer.points`: Dataset 이름, 분석 지역 안 점 수, 점 GeoJSON의 `name`·`unit_key`). 지표가 시설 개수가 아니거나 점이 1만 개를 넘으면 없다
  - 육각 격자(`map_layer.hexbins`: 칸 한 변 길이, 넓이, 센 점 수, 칸별 수의 분위 구간, 칸 GeoJSON의 `count`)와 이변량 구간(`map_layer.bivariate`: 분모 `x`·분자 `y`의 지표 이름·라벨·3분위 경계값)
- **없는 것:** 공간 단위 이름(행정동·시군구)
- **의견:** 공간 단위 이름도 응답 필드로 받아 순위 표 문구를 정하는 것을 권한다. 응답 형식을 바꾸는 일이므로 에이전트 담당과 함께 정한다.

## 5. 아직 없는 것과 개발 쪽 의견

### 5.1 진행 상황과 긴 대기 (백엔드 B1과 함께, 먼저)

지금은 응답이 올 때까지 경과 시간과 고정된 단계 목록만 보여준다.
- 백엔드가 비동기 실행으로 바뀌면 `run_id`를 바로 받는다.
- 그 뒤로는 폴링(`GET /api/analyses/{run_id}`)이나 SSE로 실제 단계 진행을 받아 보여줄 수 있다.
- 백엔드 쪽 권장안은 [백엔드 담당자 전달 문서 8.1절](../../backend/docs/backend-handoff.md#81-비동기-실행과-진행-상황-먼저)에 있다.
- 취소 버튼도 이 구조 위에서 만든다.

### 5.2 결과 URL과 새로고침 (지금 바로 가능)

- 지금은 새로고침하면 결과가 사라지고, 결과를 링크로 공유할 수 없다.
- `GET /api/analyses/{run_id}`가 이미 `result`(성공 시 `FinalResponse`)와 `provenance`(실패 시 `errors` 포함)를 돌려준다.
- 그래서 `?run=<run_id>`처럼 URL에 실행 ID를 남기면, 백엔드 변경 없이 결과를 다시 열 수 있다.

### 5.3 실행 기록 보기

- 지금은 "실행 기록" 링크가 원본 JSON을 연다.
- 같은 응답의 `provenance`에 해석 결과, 데이터 후보와 유사도, 데이터 선택 근거, 계획 시도와 검증 오류가 모두 있다.
- 이것을 읽기 쉬운 패널로 보여주면 사용자가 결과를 믿을 근거가 되고, 실패 원인을 찾기도 쉬워진다.

### 5.4 오류 안내 (2026-11-22 반영)

- `ErrorPanel`이 자주 나는 코드(`REGION_OUT_OF_SCOPE`, `REGION_NOT_SUPPORTED`, `UNIT_NOT_SUPPORTED`, `UNSUPPORTED_QUESTION`, `INCOMPLETE_INTERPRETATION`, `DATA_NOT_AVAILABLE`, `NO_MATCHING_DATA`, `PLAN_INVALID`, `LLM_FAILED`, 서버 문제 코드)에 사용자 안내 문장을 먼저 보이고, 서버 메시지와 issues, 단계·원래 코드(작은 글씨)를 그 아래에 둔다. 안내 문구는 `ERROR_HINTS`에 있다.
- 서울 범위에서 `REGION_OUT_OF_SCOPE`면 "전국 범위로 바꿔 다시 분석" 버튼이 범위 토글을 전국으로 바꾸고 같은 질문을 다시 보낸다(두 화면).
- MapLibre 화면은 실패 패널 아래에도 예시 질문을 보인다. 카카오 화면은 결과가 없으면 현재 질문 칸에 보인다.
- 코드별 의미는 [개요 문서 3.7절](../../backend/docs/agent-design-overview.md#37-실패-코드-모음-workflowerrorcode)과 [10.2절](../../backend/docs/agent-design-overview.md#102-자주-보는-오류)에 있다. 코드가 늘면 `ERROR_HINTS`도 함께 본다.

### 5.5 접근성과 입력 장치

- 툴팁과 표·지도 강조는 마우스 hover로만 동작한다. 터치 기기와 키보드 사용은 검증하지 않았다.
- 지도는 `role="img"`와 설명 라벨이 있고, 같은 정보는 순위 표("전체 표 보기")로도 볼 수 있다.

### 5.6 성능

- 주 번들이 1.28MB(gzip 355KB)다. 지도 컴포넌트를 동적 `import()`로 나누면 첫 화면이 가벼워진다.
- 응답 약 590KB는 백엔드 쪽 압축(백엔드 문서 8.9절)으로 줄일 수 있다.
- 테마가 바뀌면 배경지도 스타일이 달라 지도를 다시 만든다.

### 5.7 도구와 배포

- **도구:**
  - ESLint·Prettier 설정
  - 컴포넌트 테스트(Vitest + Testing Library)
  - API를 흉내 낸 화면 E2E(Playwright 등)
  - Node 버전 고정(`.nvmrc`나 `engines`)
- **배포:**
  - `npm run build` 결과(`frontend/dist`)를 리버스 프록시가 서빙하고 `/api`를 API로 넘기는 구성을 권한다(백엔드 B8).
  - 화면은 `/api`를 상대 경로로 부르므로, 같은 출처로 서빙하면 설정이 필요 없다.
  - 다른 출처에 두면 API 주소 설정과 CORS가 필요하다.

### 5.8 그 밖에

- 어떤 질문을 할 수 있는지는 예시 질문(`GET /api/suggestions`, 2026-11-22)으로 보인다. `GET /api/datasets`로 등록된 데이터와 지원 지역 목록까지 보여주는 것은 아직 없다.
- 분석 결과는 CSV·GeoJSON으로 내려받고 링크로 나눌 수 있다(2026-11-22). 지도 이미지 저장은 없다.
- 다크 모드는 운영체제 설정만 따른다. 직접 바꾸는 버튼은 없다.

## 6. 프론트엔드 담당자 결정 사항

"우선"에 "먼저"라고 표시한 항목은 다음 개발을 시작하기 전에 정해야 하는 것이다. 권장은 개발 쪽 의견이다.

| # | 결정 사항 | 선택지 | 권장 | 우선 |
|---|---|---|---|---|
| F1 | 지도 라이브러리 | MapLibre(현재) / 카카오맵 | 분석 지도(Choropleth)에는 MapLibre를 유지한다. 국내 지도 품질이 중요하면 F2로 보완한다 | 먼저 |
| F2 | 배경지도 | OpenFreeMap(현재) / 국내 배경지도 타일(브이월드 등) / 카카오(F1에 따름) | 우선 유지하고, 사용자 반응을 보고 국내 배경지도를 검토한다. 키·약관 확인 필요 | |
| F3 | 진행 상황 표시 | 경과 시간(현재) / 폴링 / SSE | 백엔드 B1과 함께 정한다. 실제 단계 진행과 취소를 보여준다 | 먼저 |
| F4 | 결과 URL | 없음(현재) / `run_id`를 URL에 남김 | URL에 남기고 `GET /api/analyses/{run_id}`로 다시 연다 | |
| F5 | 실행 기록 보기 | 원본 JSON 링크(현재) / 읽기 쉬운 패널 | 해석, 데이터 후보·선택 근거, 계획 시도를 패널로 보여준다 | |
| F6 | 상태 관리·라우팅 | React state만(현재) / 라우터 + 서버 상태 라이브러리(TanStack Query 등) | F3·F4를 할 때 도입을 검토한다 | |
| F7 | UI 구성 요소 | 직접 작성한 CSS(현재) / UI 라이브러리·디자인 시스템 | 화면이 늘어날 때 정한다. 지금 색 토큰(CSS 변수)은 그대로 옮길 수 있다 | |
| F8 | 지원 환경 | 데스크톱 우선, 860px 미만 세로 배치(현재) / 모바일·터치 정식 지원 | 공개 대상에 맞춰 정한다. 터치에서는 hover 대신 탭으로 정보를 보여준다 | |
| F9 | 테스트·린트 | typecheck만(현재) / ESLint·Prettier, Vitest, E2E | ESLint·Prettier와 Vitest부터 두고, CI는 백엔드와 함께 구성한다 | |
| F10 | 타입 동기화 | 수동(현재) / OpenAPI에서 타입 생성(openapi-typescript 등) | 생성 방식으로 바꾼다. 백엔드가 응답 스키마를 모두 선언해야 한다 | |
| F11 | 분석 주제가 늘 때의 문구·색 방향 | 화면에 고정(현재) / 응답 필드로 받음 | 공간 단위 이름과 값의 방향을 응답에서 받는다. 에이전트 담당과 협의한다 | |

## 7. 참고

### 7.1 코드 위치

| 내용 | 위치 |
|---|---|
| 화면 구성, 요청, 진행 표시 | [frontend/src/App.tsx](../src/App.tsx) |
| API 호출 | [frontend/src/api.ts](../src/api.ts) |
| 응답 타입 | [frontend/src/types.ts](../src/types.ts) |
| 지도 | [frontend/src/components/ChoroplethMap.tsx](../src/components/ChoroplethMap.tsx) |
| 범례 | [frontend/src/components/Legend.tsx](../src/components/Legend.tsx) |
| 순위 표 | [frontend/src/components/RankingTable.tsx](../src/components/RankingTable.tsx) |
| 데이터·방법·가정·한계 | [frontend/src/components/ResultDetails.tsx](../src/components/ResultDetails.tsx) |
| 오류 | [frontend/src/components/ErrorPanel.tsx](../src/components/ErrorPanel.tsx) |
| 색 램프, 배경지도, 값 포맷, 결측 사유 | [frontend/src/theme.ts](../src/theme.ts) |
| 레이아웃·색 토큰 | [frontend/src/styles.css](../src/styles.css) |
| 화면 고르기(`map=kakao`) | [frontend/src/main.tsx](../src/main.tsx) |
| 카카오 화면: 주소, 패널, 범례, 순위 | [frontend/src/components/KakaoShowcase.tsx](../src/components/KakaoShowcase.tsx), [kakao-showcase.css](../src/components/kakao-showcase.css) |
| 카카오 지도: 보기별 그리기, 지도 맞춤 | [frontend/src/components/KakaoShowcaseMap.tsx](../src/components/KakaoShowcaseMap.tsx), [frontend/src/maps/](../src/maps/) |
| 개발 서버·프록시 | [frontend/vite.config.ts](../vite.config.ts) |
| 백엔드 응답 모델 | [app/api/schemas.py](../../backend/app/api/schemas.py), [app/domain/results.py](../../backend/app/domain/results.py), [app/domain/state.py](../../backend/app/domain/state.py) |

### 7.2 관련 문서

- [에이전트 설계 개요](../../backend/docs/agent-design-overview.md): 7장 화면 구조와 동작, 3.7절 오류 코드, 6.3절 API 응답 예시
- [백엔드 담당자 전달 문서](../../backend/docs/backend-handoff.md): API 계약, 실행 모델, 비동기 실행(B1), 화면 서빙(B8)
- [데이터 담당자 전달 문서](../../backend/docs/data-handoff.md): 데이터 출처와 라이선스, 지오코딩 검토(D16)
- [개발 전달 문서](../../backend/docs/공간%20분석%20AI%20Agent%20개발%20전달%20문서.md): 원 요구사항. 결과 출력은 22번, 지도 출력은 23번

### 7.3 외부 자료 (2026-10 확인)

- [카카오맵 Web API 가이드](https://apis.map.kakao.com/web/guide/): 앱 키, 도메인 등록
- [카카오 디벨로퍼스 쿼터](https://developers.kakao.com/docs/ko/getting-started/quota): 지도 SDK 일 30만 건, 초과 요금
- [카카오맵 API 이해하기](https://developers.kakao.com/docs/ko/kakaomap/common): 첫 앱 무료 쿼터, 2026-07-21 변경
- [데브톡: GeoJSON 좌표를 직접 변환해 폴리곤을 그리는 사례](https://devtalk.kakao.com/t/topic/133798)
- [OpenFreeMap](https://openfreemap.org/): 키·요청 제한 없음, 상업 이용 허용, 출처 표기
- [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js): BSD-3 라이선스
