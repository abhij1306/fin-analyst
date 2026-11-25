"""LlamaParse (fin.extract.llamaparse_run) without calling its API: a response in the SDK's shape
(tests/fixtures/pdf_addon/llamaparse_response.json, hand-built; checked against the SDK's model when
the llamaparse extra is installed) is read into fin's grids, saved responses are reused, and the API
key stays in the environment."""

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fin.audit.errors import PdfAddonFailed
from fin.extract import llamaparse_run
from fin.extract.addon import Request, page_count
from fin.extract.pdf_tables import PageContext, read_tables
from tests.synthetic_pdf import write_pdf

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_addon" / "llamaparse_response.json"
KEY = "llx-test-key-never-written"


def response() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def request(tmp_path: Path) -> Request:
    path = write_pdf(tmp_path / "acme_h1.pdf", *[[(50, 50, f"page {n}")] for n in range(1, 8)])
    return Request(path, [4, 7], tmp_path / "cache" / "llamaparse")


def test_the_fixture_has_the_sdk_shape() -> None:
    types = pytest.importorskip("llama_cloud.types.parsing_get_response")
    parsed = types.ParsingGetResponse.model_validate(response())
    first, failed = parsed.items.pages
    assert first.items[2].rows == response()["items"]["pages"][0]["items"][2]["rows"]
    assert (failed.success, failed.error) == (False, "page timed out")


def test_tables_and_text_are_read_per_original_page() -> None:
    found = llamaparse_run.read_items(response(), [4, 7])

    page = found[4]
    assert page.texts == ["Statement of Assets and Liabilities", "(Rs. in Lakhs)"]
    (table,) = page.tables
    assert table.bbox == (48.0, 66.0, 638.0, 250.0)  # the union of the table's boxes
    assert [c.text for c in table.grid[4]] == [
        "Property, plant and equipment",
        "60",
        "50",
        "70",
        "55",
    ]
    assert [c.text for c in table.grid[8]] == [
        "Cash and cash equivalents",
        "35.5",
        "45.00",
        "(4.00)",
        "-",
    ]
    assert [c.text for c in table.grid[2]] == ["ASSETS", "", "", "", ""]
    assert found[7].tables == []
    assert found[7].error is not None
    assert found[7].error.code == "E206"
    assert found[7].error.message == "page 7: LlamaParse could not parse it: page timed out"


def test_its_grid_is_read_by_fins_rules_with_the_table_box() -> None:
    (table,) = llamaparse_run.read_items(response(), [4, 7])[4].tables
    context = PageContext(4, "balance_sheet", None, "lakh", True, "LlamaParse")

    values = read_tables(context, [table], 4)

    loans = [(v.scope, v.period, v.rupees) for v in values if v.label == "Loans"]
    assert loans == [
        ("standalone", "FY26-H1", Decimal("500000.00")),
        ("standalone", "FY25", Decimal("500000.00")),
        ("consolidated", "FY26-H1", Decimal("600000.00")),
        ("consolidated", "FY25", Decimal("600000.00")),
    ]
    assert {v.bbox for v in values} == {(48.0, 66.0, 638.0, 250.0)}


def test_without_the_key_nothing_is_sent_and_the_message_names_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(llamaparse_run.API_KEY_ENV, raising=False)
    monkeypatch.setattr(llamaparse_run, "_parse", lambda request, key: pytest.fail("API called"))

    req = request(tmp_path)
    with pytest.raises(PdfAddonFailed) as caught:
        llamaparse_run.convert(req, [])

    assert caught.value.code == "E206"
    assert caught.value.message == (
        "acme_h1.pdf: LlamaParse needs an API key in the environment variable "
        "LLAMA_CLOUD_API_KEY, which is not set; pages [4, 7] were not sent"
    )


def test_a_response_is_saved_by_sha256_and_pages_and_reused_without_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def parse(req: Request, key: str) -> dict[str, Any]:
        calls.append(key)
        return response()

    monkeypatch.setattr(llamaparse_run, "_parse", parse)
    monkeypatch.setenv(llamaparse_run.API_KEY_ENV, KEY)
    req = request(tmp_path)
    notices: list[str] = []

    first = llamaparse_run.convert(req, notices)
    monkeypatch.delenv(llamaparse_run.API_KEY_ENV)
    second = llamaparse_run.convert(req, notices)

    assert calls == [KEY]
    saved = llamaparse_run.cache_file(req)
    digest = hashlib.sha256(req.path.read_bytes()).hexdigest()
    assert saved == tmp_path / "cache" / "llamaparse" / f"{digest}-p4-7.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == response()
    assert KEY not in saved.read_text(encoding="utf-8")
    assert KEY not in " ".join(notices)
    assert first[4] == second[4]
    assert str(first[7].error) == str(second[7].error)
    assert notices == [
        f"pages [4, 7] uploaded to LlamaParse; its response saved as {saved.name}",
        f"LlamaParse's saved response for pages [4, 7] used ({saved.name})",
    ]


def test_the_upload_holds_only_the_pages_under_a_neutral_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK is stood in for: fin sends a PDF of only the skipped pages, named pages.pdf (never
    the document's own name), with the key it was given, and asks for the structured items."""
    sent: dict[str, Any] = {}

    class Parsing:
        def parse(self, **kwargs: Any) -> Any:
            sent.update(kwargs)
            job = SimpleNamespace(status="COMPLETED", error_message=None)
            return SimpleNamespace(job=job, model_dump=lambda mode: response())

    class LlamaCloud:
        def __init__(self, api_key: str) -> None:
            sent["api_key"] = api_key
            self.parsing = Parsing()

    class Failure(Exception):
        pass

    fake = SimpleNamespace(
        LlamaCloud=LlamaCloud,
        LlamaCloudError=Failure,
        PollingError=Failure,
        PollingTimeoutError=Failure,
    )
    monkeypatch.setitem(sys.modules, "llama_cloud", fake)
    req = request(tmp_path)

    assert llamaparse_run._parse(req, KEY) == response()

    name, content, kind = sent["upload_file"]
    assert (name, kind, sent["api_key"]) == ("pages.pdf", "application/pdf", KEY)
    assert content.startswith(b"%PDF")
    assert sent["expand"] == ["items"]
    copy = tmp_path / "sent.pdf"
    copy.write_bytes(content)
    assert page_count(copy) == 2


def test_a_failed_job_is_e206(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Parsing:
        def parse(self, **kwargs: Any) -> Any:
            return SimpleNamespace(job=SimpleNamespace(status="FAILED", error_message="bad pdf"))

    fake = SimpleNamespace(
        LlamaCloud=lambda api_key: SimpleNamespace(parsing=Parsing()),
        LlamaCloudError=OSError,
        PollingError=OSError,
        PollingTimeoutError=OSError,
    )
    monkeypatch.setitem(sys.modules, "llama_cloud", fake)

    req = request(tmp_path)
    with pytest.raises(PdfAddonFailed) as caught:
        llamaparse_run._parse(req, KEY)

    assert caught.value.message == "acme_h1.pdf: LlamaParse failed on pages [4, 7]: bad pdf"


def test_a_saved_response_in_another_shape_is_e206(tmp_path: Path) -> None:
    """A response fin cannot read (here a table box without its size) leaves the pages unread."""
    req = request(tmp_path)
    changed = response()
    changed["items"]["pages"][0]["items"][2]["bbox"] = [{"x": 1.0, "y": 2.0}]
    saved = llamaparse_run.cache_file(req)
    saved.parent.mkdir(parents=True)
    saved.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(PdfAddonFailed, match="not in the shape fin reads"):
        llamaparse_run.convert(req, [])
