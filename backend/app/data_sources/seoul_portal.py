"""서울 열린데이터광장 데이터셋 상세 화면에서 설명·키워드·이용 조건·갱신주기와 좌표계 안내를 읽는다.

목록·컬럼 API에는 설명이 없고 설명을 모은 파일도 없어서, 자동 후보를 받을 때 그 데이터의 상세 화면 하나만 읽는다.
공식 API가 아니므로 화면 구조가 바뀌면 값이 비고, 그때는 좌표계를 값과 계열 규칙만으로 정한다.
"""

import html
import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

PORTAL_HOST = "data.seoul.go.kr"
_META = r'<meta name="{name}" content="([^"]*)"'
# 표의 한 칸: <th scope="row">갱신주기</th> <td>매일</td> (사이에 주석이 있을 수 있다)
_ROW = r'<th scope="row">\s*{label}\s*</th>\s*(?:<!--.*?-->\s*)*<td[^>]*>(.*?)</td>'


@dataclass(frozen=True)
class PortalPage:
    description: str | None
    keywords: list[str]
    license: str | None
    update_cycle: str | None

    @property
    def crs(self) -> str | None:
        """설명의 좌표계 안내 (예: '중부원점TM(EPSG:5174)' → EPSG:5174, '좌표계 : WGS84' → EPSG:4326)."""
        text = self.description or ""
        if match := re.search(r"EPSG\s*:\s*(\d{4,5})", text):
            return f"EPSG:{match.group(1)}"
        if re.search(r"WGS\s*-?\s*84", text, re.IGNORECASE):
            return "EPSG:4326"
        return None


def parse_portal_page(text: str) -> PortalPage:
    def meta(name: str) -> str | None:
        match = re.search(_META.format(name=name), text)
        return _clean(match.group(1)) if match else None

    def row(label: str) -> str | None:
        match = re.search(_ROW.format(label=label), text, re.DOTALL)
        return _clean(match.group(1)) if match else None

    keywords = meta("keywords") or ""
    return PortalPage(
        description=meta("description"),
        keywords=[keyword.strip() for keyword in keywords.split(",") if keyword.strip()],
        license=row("이용허락범위"),
        update_cycle=row("갱신주기"),
    )


def fetch_portal_page(url: str, timeout: float = 20.0) -> PortalPage | None:
    """상세 화면을 읽는다. 서울 열린데이터광장 주소가 아니거나 읽지 못하면 None이다 (화면 정보 없이 계속한다)."""
    if urlparse(url).hostname != PORTAL_HOST:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return parse_portal_page(response.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _clean(value: str) -> str | None:
    # 설명은 HTML이 한 번 더 이스케이프되어 있다 (&lt;br /&gt;).
    text = re.sub(r"<br\s*/?>", "\n", html.unescape(value))
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line) or None
