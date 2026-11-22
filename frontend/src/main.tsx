import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// 주소의 map=kakao는 App이 읽어 지도만 카카오 지도로 바꾼다. 화면 틀은 두 지도가 같다.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
