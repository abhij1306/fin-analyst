"""How the UI shows timestamps, status tones and grouped log problems."""

import pytest

pytest.importorskip("jinja2")

from fin.ui.format import code, problems_view, tone, when
from fin.ui.views import tab_counts


def test_a_timestamp_is_day_month_year_and_minutes() -> None:
    assert when("2026-11-26 13:18:14.402962") == "26 Nov 2026, 13:18"
    assert when("2026-11-05T09:03:00Z") == "5 Nov 2026, 09:03"
    assert when(None) == ""
    assert when("not a time") == "not a time"


def test_status_words_have_one_tone_each() -> None:
    assert [tone(s) for s in ("loaded", "open", "failed", "failures", "draft", "")] == [
        "ok",
        "warn",
        "bad",
        "bad",
        "neutral",
        "neutral",
    ]


def test_identical_problems_are_grouped_with_a_count() -> None:
    warning = {"level": "warning", "error_code": "E101", "message": "sheet skipped"}
    error = {"level": "error", "error_code": None, "message": "boom"}
    view = problems_view([warning] * 13 + [error])
    assert view is not None
    assert view["label"] == "1 error, 13 warnings"
    assert view["tone"] == "bad"
    assert view["groups"] == [
        {"code": "E101", "message": "sheet skipped", "count": 13},
        {"code": "error", "message": "boom", "count": 1},
    ]
    assert problems_view([]) is None


def test_tab_counts_list_only_what_waits() -> None:
    status = {
        "open_findings": 2,
        "inbox_not_ingested": ["a.xlsx"],
        "pending_mappings": 0,
        "diligence": {
            "queries": {"draft": 3, "approved": 1, "sent": 2, "answered": 1},
            "proposed_adjustments": 1,
            "gates": {"G1": "approved", "G2": "open"},
        },
    }
    # drafts to approve, approved to mark sent, answered to close; sent waits on the accountant
    assert tab_counts(status) == {"files": 3, "queries": 6, "gates": 1}


def test_backticked_commands_show_as_code_and_the_rest_is_escaped() -> None:
    assert code("E803: run `fin engagement open x` <b>") == (
        "E803: run <code>fin engagement open x</code> &lt;b&gt;"
    )
    assert code(None) == ""
