import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { KakaoShowcase } from "./components/KakaoShowcase";
import "./styles.css";

const query = new URLSearchParams(window.location.search);
const showcase = query.get("demo") === "1" && query.get("map") === "kakao";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {showcase ? <KakaoShowcase /> : <App />}
  </StrictMode>,
);
