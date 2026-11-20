"""MAPGPT FE/jsm의 카카오지도/채팅 UI를 유지하면서 BE/jjc API URL 변경만 반영한다.

실행: C:\\zxcv\\frontend 폴더에 이 파일을 놓고 `python apply_be_jjc_api.py`
원격 GitHub/브랜치/커밋은 변경하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent

API_HEADER = 'import type { AnalysisResponse, AnalysisScope, FinalResponse, ValidationIssue, WorkflowError } from "./types";'
API_HELPER = (
    'const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").trim().replace(/\\/+$/, "");\n\n'
    'export function apiUrl(path: string): string {\n'
    '  return `${API_BASE_URL}${path}`;\n'
    '}\n'
)

# 기존 파일에서 확인된 고유한 코드 조각만 변경한다. 이미 적용됐다면 건너뛴다.
REPLACEMENTS: dict[str, list[tuple[str, str]]] = {
    'src/api.ts': [
        (API_HEADER + '\n\nexport async function requestAnalysis',
         API_HEADER + '\n\n' + API_HELPER + '\nexport async function requestAnalysis'),
        ('fetch("/api/analyses", {', 'fetch(apiUrl("/api/analyses"), {'),
        ('fetch(`/api/analyses/${encodeURIComponent(runId)}`, { signal });',
         'fetch(apiUrl(`/api/analyses/${encodeURIComponent(runId)}`), { signal });'),
    ],
    'src/components/KakaoShowcase.tsx': [
        ('import { getStoredAnalysis, requestAnalysis } from "../api";',
         'import { apiUrl, getStoredAnalysis, requestAnalysis } from "../api";'),
        ('fetch("/api/health", { cache: "no-store" })',
         'fetch(apiUrl("/api/health"), { cache: "no-store" })'),
    ],
    'src/App.tsx': [
        ('import { getStoredAnalysis, requestAnalysis } from "./api";',
         'import { apiUrl, getStoredAnalysis, requestAnalysis } from "./api";'),
        ('href={`/api/analyses/${encodeURIComponent(runningRunId)}`}',
         'href={apiUrl(`/api/analyses/${encodeURIComponent(runningRunId)}`)}'),
    ],
    'src/components/ErrorPanel.tsx': [
        ('import type { WorkflowError } from "../types";',
         'import type { WorkflowError } from "../types";\nimport { apiUrl } from "../api";'),
        ('href={`/api/analyses/${runId}`}',
         'href={apiUrl(`/api/analyses/${encodeURIComponent(runId)}`)}'),
    ],
    'src/components/ResultDetails.tsx': [
        ('import type { FinalResponse } from "../types";',
         'import type { FinalResponse } from "../types";\nimport { apiUrl } from "../api";'),
        ('href={`/api/analyses/${result.run_id}`}',
         'href={apiUrl(`/api/analyses/${encodeURIComponent(result.run_id)}`)}'),
    ],
}


def main() -> int:
    if not (ROOT / 'package.json').is_file():
        print(f'[중단] 프런트엔드 루트 폴더에서 실행해야 합니다: {ROOT}')
        return 1

    pending: dict[Path, str] = {}
    errors: list[str] = []
    already: list[str] = []

    for relative, rules in REPLACEMENTS.items():
        file = ROOT / relative
        if not file.is_file():
            errors.append(f'{relative}: 파일이 없습니다')
            continue
        original = file.read_text(encoding='utf-8')
        updated = original
        changes = 0

        for before, after in rules:
            if before == after:
                continue
            # 완성된 치환문이 들어 있다면 반복 실행 때 재치환하지 않는다.
            if after in updated:
                continue
            hits = updated.count(before)
            if hits != 1:
                errors.append(f'{relative}: 예상 코드가 {hits}회 발견됨 (예상 1회). 안전하게 중단합니다.\n  {before[:110]}')
                continue
            updated = updated.replace(before, after, 1)
            changes += 1

        if changes:
            pending[file] = updated
        else:
            already.append(relative)

    # FE/jsm에서는 마지막 `.env*` 규칙이 앞선 `!.env.example`을 다시 가릴 수 있다.
    # 예제 파일만 Git에 나타나도록 마지막에 한 번 더 예외를 둔다.
    ignore_file = ROOT / '.gitignore'
    if ignore_file.is_file():
        ignore_text = ignore_file.read_text(encoding='utf-8')
        relevant = [line.strip() for line in ignore_text.splitlines()
                    if line.strip() in {'.env*', '!.env.example'}]
        if relevant and relevant[-1] == '.env*':
            pending[ignore_file] = ignore_text.rstrip('\n') + '\n!.env.example\n'

    if errors:
        print('[중단] 아무 파일도 수정하지 않았습니다. 현재 코드가 예상한 버전과 다릅니다.')
        print('\n'.join(errors))
        return 1

    # git 리포지토리 밖의 인접 폴더에 백업을 만들어 사용자 변경분을 보존한다.
    if pending:
        backup_root = ROOT.parent / f'mapgpt-api-backup-{datetime.now():%Y%m%d-%H%M%S}'
        for source in pending:
            relative = source.relative_to(ROOT)
            target = backup_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for file, updated in pending.items():
            file.write_text(updated, encoding='utf-8')
            print(f'[수정] {file.relative_to(ROOT)}')
        print(f'[백업] {backup_root}')
    else:
        print('[확인] API 주소 연결 변경이 이미 적용되어 있습니다.')

    # 실제 .env / .env.local을 덮어쓰지 않는다.
    example = ROOT / '.env.example'
    if not example.exists():
        example.write_text(
            '# 공개 HTTPS API 주소. 로컬 Vite 프록시를 쓸 때는 비워 둡니다. (/api 제외)\n'
            'VITE_API_BASE_URL=\n'
            '\n# 카카오 JavaScript 키: 공개 클라이언트용 키만 지정\n'
            'VITE_KAKAO_MAP_APP_KEY=\n', encoding='utf-8'
        )
        print('[추가] .env.example (실제 키 값은 작성하지 않음)')

    print('[완료] 현재 브랜치 유지, Git 커밋/푸시 없음')
    print('다음: npm run typecheck && npm run build')
    return 0


if __name__ == '__main__':
    sys.exit(main())
