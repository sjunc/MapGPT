import { useEffect, useState } from "react";
import { getSuggestions } from "../api";
import type { AnalysisScope } from "../types";
import "./suggestion-chips.css";

interface Props {
  scope: AnalysisScope;
  disabled: boolean;
  onPick: (question: string) => void;
}

/** 서버가 등록된 데이터로 답할 수 있다고 돌려준 예시 질문. 누르면 그 질문으로 바로 분석한다. 불러오지 못하면 보이지 않는다. */
export function SuggestionChips({ scope, disabled, onPick }: Props) {
  const [loaded, setLoaded] = useState<{ scope: AnalysisScope; questions: string[] } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getSuggestions(scope, controller.signal)
      .then((questions) => setLoaded({ scope, questions }))
      .catch(() => {
        if (!controller.signal.aborted) setLoaded({ scope, questions: [] });
      });
    return () => controller.abort();
  }, [scope]);

  const questions = loaded?.scope === scope ? loaded.questions : [];
  if (questions.length === 0) return null;
  return (
    <div className="suggestion-chips">
      <span className="suggestion-chips-title">이런 질문을 해 보세요</span>
      <ul>
        {questions.map((question) => (
          <li key={question}>
            <button type="button" onClick={() => onPick(question)} disabled={disabled}>{question}</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
