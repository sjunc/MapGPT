"""자동 후보를 받을 때 좌표 모양을 값으로 판정하는 규칙 (네트워크·DB 없음)."""

import pytest

from app.data_sources.acquisition import judge_coordinates


def _rows(pairs):
    return [{"LAT": str(lat), "LNT": str(lon)} for lat, lon in pairs]


@pytest.mark.parametrize(
    ("pairs", "kind"),
    [
        ([(37.56, 126.97), (37.55, 127.06), (0, 0)], "wgs84"),  # (0, 0)은 빈 좌표라 세지 않는다
        ([(126.97, 37.56), (127.06, 37.55)], "swapped"),
        ([(448153.52, 196549.89), (446420.75, 197646.38)], "plane"),  # 중부원점 TM(미터)
        ([(37.56, 126.97), (448153.52, 196549.89)], "unknown"),  # 섞여 있으면 정하지 않는다
        ([("", ""), ("없음", "없음")], "unknown"),
    ],
)
def test_coordinate_shape_is_judged_from_values(pairs, kind):
    assert judge_coordinates(_rows(pairs), "LAT", "LNT") == kind


def test_values_with_spaces_are_still_numbers():
    rows = [{"Y": " 448153.524398611    ", "X": "196549.89584221     "}] * 3

    assert judge_coordinates(rows, "Y", "X") == "plane"
