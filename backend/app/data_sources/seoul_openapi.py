"""서울 열린데이터광장 OpenAPI 커넥터. 일반 인증키 하나로 서비스명만 바꿔 같은 방식으로 받는다.

주소: http://openapi.seoul.go.kr:8088/{인증키}/json/{서비스명}/{시작 위치}/{끝 위치}/
"""

import json
import urllib.request
from typing import Any
from urllib.parse import quote

PAGE_SIZE = 1000  # 서울 OpenAPI 1회 최대 요청 건수


class SeoulApiError(RuntimeError):
    pass


def fetch_service(
    api_key: str, service: str, max_rows: int | None = None, timeout: float = 30.0, params: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """서비스의 전체 행을 페이지 단위로 받는다. 인증키는 로그·예외 메시지에 남기지 않는다.

    첫 페이지에서 확인한 전체 건수가 max_rows보다 많으면 나머지 페이지를 받지 않고 멈춘다.
    params는 끝 위치 뒤에 붙는 요청 인자다 (예: 입출력 인자 조회의 서비스 ID).
    """
    rows: list[dict[str, Any]] = []
    total: int | None = None
    start = 1
    suffix = "".join(f"{quote(param, safe='')}/" for param in params)
    while total is None or start <= total:
        end = start + PAGE_SIZE - 1
        url = f"http://openapi.seoul.go.kr:8088/{api_key}/json/{service}/{start}/{end}/{suffix}"
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
        body = payload.get(service)
        if body is None:
            result = payload.get("RESULT", {})
            raise SeoulApiError(f"서울 OpenAPI 오류 {result.get('CODE')}: {result.get('MESSAGE')}")
        if body["RESULT"]["CODE"] != "INFO-000":
            raise SeoulApiError(f"서울 OpenAPI 오류 {body['RESULT']['CODE']}: {body['RESULT']['MESSAGE']}")
        total = int(body["list_total_count"])
        if max_rows is not None and total > max_rows:
            raise SeoulApiError(f"{service} 전체 {total:,}건이 한도 {max_rows:,}건보다 많습니다")
        rows.extend(body["row"])
        start = end + 1
    if len(rows) != total:
        raise SeoulApiError(f"수집 건수({len(rows)})가 전체 건수({total})와 다릅니다")
    return rows
