import { exportUrl } from "../api";
import "./result-share.css";

interface Props {
  runId: string;
  onCopyLink: () => void;
  linkCopied: boolean;
}

/** 서버에 저장된 분석 결과를 파일로 내려받거나 링크로 나눈다. 서버 실행이 없는 데모에서는 쓰지 않는다. */
export function ResultShare({ runId, onCopyLink, linkCopied }: Props) {
  return (
    <section className="result-share" aria-label="분석 결과 내려받기와 공유">
      <span className="result-share-title">결과 내려받기·공유</span>
      <div className="result-share-actions">
        <a href={exportUrl(runId, "csv")} download title="분석 단위마다 한 행 (엑셀에서 열림)">CSV</a>
        <a href={exportUrl(runId, "geojson")} download title="지도에 그린 지역 경계와 지표 값 (QGIS 등)">GeoJSON</a>
        <button type="button" onClick={onCopyLink}>{linkCopied ? "링크 복사 완료" : "링크 복사"}</button>
      </div>
    </section>
  );
}
