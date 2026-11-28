"""실제 데이터와 설정된 LLM으로 질문 묶음을 검증한다 (호출 비용 발생)."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from psycopg_pool import ConnectionPool

from app.config import get_settings
from app.data_sources.acquisition import DataAcquirer
from app.llm.factory import create_embedder, create_llm
from app.workflows.analysis_workflow import AnalysisWorkflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions", type=Path, help="질문 문자열 배열 JSON")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    settings = get_settings()

    def run(question):
        with ConnectionPool(settings.database_url, min_size=1, max_size=2) as pool:
            embedder = create_embedder(settings)
            workflow = AnalysisWorkflow(
                create_llm(settings), pool, embedder, DataAcquirer.from_settings(settings, pool, embedder)
            )
            state = workflow.run(question)
            response = state.final_response
            result = {
                "question": question,
                "run_id": state.run_id,
                "status": state.status.value,
                "datasets": state.selected_datasets,
                "errors": [error.model_dump(mode="json") for error in state.errors],
                "response": response.model_dump(mode="json") if response else None,
            }
            print(question, state.status.value, [error.code for error in state.errors], flush=True)
            return result

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(run, questions))
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
