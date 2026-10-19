import type { RecentRun } from "../runHistory";

interface Props {
  entries: RecentRun[];
  currentRunId: string | null;
  disabled: boolean;
  onOpen: (runId: string) => void;
  onRemove: (runId: string) => void;
  onClear: () => void;
}

function viewedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("ko-KR");
}

export function RecentRuns({ entries, currentRunId, disabled, onOpen, onRemove, onClear }: Props) {
  return (
    <details className="card p7-recent">
      <summary>최근 열어본 실행 ({entries.length})</summary>
      <p className="muted p7-note">이 브라우저에만 저장됩니다. 서버 전체 이력 목록이 아닙니다.</p>
      {entries.length === 0 ? (
        <p className="muted">아직 저장된 기록이 없습니다.</p>
      ) : (
        <>
          <ul className="p7-history-list">
            {entries.map((entry) => (
              <li key={entry.runId}>
                <button
                  type="button"
                  className="p7-history-open"
                  onClick={() => onOpen(entry.runId)}
                  disabled={disabled}
                  aria-current={entry.runId === currentRunId ? "true" : undefined}
                  title={entry.question}
                >
                  <strong>{entry.question}</strong>
                  <small>{viewedAt(entry.viewedAt)}</small>
                </button>
                <button
                  type="button"
                  className="p7-history-remove"
                  onClick={() => onRemove(entry.runId)}
                  aria-label={`${entry.question} 최근 기록에서 삭제`}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="link-button p7-clear-history" onClick={onClear}>
            이 브라우저의 최근 기록 지우기
          </button>
        </>
      )}
    </details>
  );
}
