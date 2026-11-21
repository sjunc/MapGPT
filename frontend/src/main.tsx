import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { KakaoShowcase } from "./components/KakaoShowcase";
import "./styles.css";

const query = new URLSearchParams(window.location.search);
// map=kakao면 카카오 지도 화면을 연다. 그 화면에 무엇을 띄울지(run, demo=1)는 KakaoShowcase가 주소로 정한다.
const showcase = query.get("map") === "kakao";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {showcase ? <KakaoShowcase /> : <App />}
  </StrictMode>,
);
