from app.catalog.definitions import TRUSTED_DATASETS
from app.catalog.suggestions import SUGGESTIONS, suggested_questions
from app.domain.enums import AnalysisScope


def test_every_suggestion_uses_trusted_datasets():
    assert all(s.datasets <= TRUSTED_DATASETS.keys() for s in SUGGESTIONS)
    assert 4 <= len(SUGGESTIONS) <= 6


def test_suggestions_need_all_their_datasets_registered():
    registered = {"emd_boundary", "daycare_facility"}

    assert suggested_questions(AnalysisScope.SEOUL, registered) == ["강남구 어린이집이 어디에 몰려 있나요?"]
    assert suggested_questions(AnalysisScope.SEOUL, set()) == []


def test_questions_outside_seoul_are_only_suggested_in_national_scope():
    registered = set(TRUSTED_DATASETS)
    seoul = suggested_questions(AnalysisScope.SEOUL, registered)
    national = suggested_questions(AnalysisScope.NATIONAL, registered)

    assert "부산 중구 행정동별 인구 순위" not in seoul
    assert national == [*seoul, "부산 중구 행정동별 인구 순위"]
