"""The optional web UI server: its local-only protections and its pages (docs/web-ui-plan.md).
Needs the `ui` extra (CI's ui job syncs it); skipped without it."""

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from fin.ui.security import COOKIE
from fin.ui.server import create_app

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"


@pytest.fixture
def client(data_root: Path) -> TestClient:
    (data_root / "inbox" / "acme").mkdir(parents=True)
    (data_root / "inbox" / "acme" / "waiting.xlsx").write_bytes(b"not loaded")
    app = create_app(data_root, TOKEN, PORT)
    return TestClient(app, base_url=f"http://{HOST}", follow_redirects=False)


def signed_in(client: TestClient) -> TestClient:
    client.get(f"/?token={TOKEN}")
    return client


def test_the_link_with_the_token_sets_a_strict_http_only_cookie_and_drops_the_token(
    client: TestClient,
) -> None:
    response = client.get(f"/?token={TOKEN}")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert f"{COOKIE}={TOKEN}" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie


def test_a_wrong_token_is_refused_with_e007_and_sets_no_cookie(client: TestClient) -> None:
    response = client.get("/?token=nope")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "E007"
    assert "set-cookie" not in response.headers


def test_a_token_that_is_not_ascii_is_refused_with_e007(client: TestClient) -> None:
    response = client.get("/?token=%C3%A9")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "E007"


@pytest.mark.parametrize("path", ["/", "/partials/home?company=acme", "/static/app.css"])
def test_every_path_needs_the_cookie(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "E007"


def test_a_host_that_is_not_local_is_refused_even_with_the_cookie(client: TestClient) -> None:
    signed_in(client)
    for host in ("evil.example", "evil.example:8765", "127.0.0.1:9999", "localhost"):
        response = client.get("/", headers={"Host": host})
        assert response.status_code == 400, host
        assert response.json()["error"]["code"] == "E007"


def test_localhost_is_allowed(client: TestClient) -> None:
    signed_in(client)
    assert client.get("/", headers={"Host": f"localhost:{PORT}"}).status_code == 200


def test_no_cors_headers_are_ever_sent(client: TestClient) -> None:
    signed_in(client)
    response = client.get("/", headers={"Origin": "http://evil.example"})
    assert not any(name.startswith("access-control-") for name in response.headers)


def test_the_api_documentation_pages_are_off(client: TestClient) -> None:
    signed_in(client)
    for path in ("/docs", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_the_home_page_shows_the_company_and_what_waits(client: TestClient) -> None:
    page = signed_in(client).get("/")
    assert page.status_code == 200
    assert '<option value="acme" selected>' in page.text
    assert "/static/vendor/htmx.min.js" in page.text
    assert "/static/vendor/basecoat-all.min.js" in page.text
    assert "1 files in the inbox not loaded yet" in page.text
    assert 'hx-get="/chat/panel?company=acme"' in page.text  # the chat panel loads itself


def test_the_home_partial_is_the_screen_only(client: TestClient) -> None:
    partial = signed_in(client).get("/partials/home?company=acme")
    assert partial.status_code == 200
    assert "<html" not in partial.text
    assert "<h1>acme</h1>" in partial.text


def test_an_unknown_company_is_e001_shown_as_an_alert(client: TestClient) -> None:
    response = signed_in(client).get("/partials/home?company=ghost")
    assert response.status_code == 400
    assert "E001" in response.text
    assert 'data-variant="destructive"' in response.text


def test_company_names_are_escaped(client: TestClient) -> None:
    response = signed_in(client).get("/partials/home?company=<script>alert(1)</script>")
    assert "<script>alert(1)" not in response.text


def test_the_page_says_so_when_there_are_no_companies(data_root: Path) -> None:
    data_root.mkdir(parents=True)
    client = TestClient(
        create_app(data_root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    assert "No companies yet" in signed_in(client).get("/").text


def test_the_files_screen_lists_what_waits_in_the_inbox(client: TestClient) -> None:
    page = signed_in(client).get("/files?company=acme")
    assert "waiting.xlsx" in page.text
    assert "waiting in inbox/acme/: press Ingest" in page.text


def test_queries_gates_and_findings_say_so_when_there_is_no_engagement(
    client: TestClient,
) -> None:
    signed_in(client)
    for screen in ("queries", "gates", "findings"):
        assert "no engagement" in client.get(f"/partials/{screen}?company=acme").text


def test_a_report_is_listed_opened_and_a_path_outside_the_folder_is_refused(
    client: TestClient, data_root: Path
) -> None:
    folder = data_root / "reports" / "acme" / "consolidated"
    folder.mkdir(parents=True)
    (folder / "health.html").write_text("<p>report</p>", encoding="utf-8")
    (folder / "narrative.md").write_text("x", encoding="utf-8")
    (data_root / "secret.txt").write_text("secret", encoding="utf-8")
    signed_in(client)
    listing = client.get("/partials/reports?company=acme").text
    assert "consolidated/health.html" in listing
    assert (
        "narrative.md</a>" not in listing
    )  # a narrative is listed with its sections, not as a file
    opened = client.get("/report-file?company=acme&name=consolidated/health.html")
    assert opened.status_code == 200
    assert "content-disposition" not in opened.headers
    sandbox = opened.headers["content-security-policy"].split()
    assert sandbox[0] == "sandbox"
    assert "allow-same-origin" not in sandbox
    for name in ("../../secret.txt", "consolidated/narrative.md"):
        refused = client.get("/report-file", params={"company": "acme", "name": name})
        assert refused.status_code == 400
        assert "E001" in refused.text


def test_a_report_download_is_an_attachment(client: TestClient, data_root: Path) -> None:
    folder = data_root / "reports" / "acme"
    folder.mkdir(parents=True)
    (folder / "acme_financials.xlsx").write_bytes(b"xlsx")
    response = signed_in(client).get("/report-file?company=acme&name=acme_financials.xlsx")
    assert "attachment" in response.headers["content-disposition"]


def test_the_files_and_log_screens_show_a_loaded_company(
    clean_root: Callable[..., Path], data_root: Path
) -> None:
    clean_root("kestrel")
    client = TestClient(
        create_app(data_root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    signed_in(client)
    files = client.get("/partials/files?company=kestrel").text
    assert "Kestrel_Forgings_Financials_FY23-FY25.xlsx" in files
    assert 'data-tone="ok">loaded<' in files
    log = client.get("/partials/log?company=kestrel").text
    assert "ingest" in log
    assert "E803" in client.get("/partials/gates?company=kestrel").text


def test_the_gates_queries_and_findings_screens_show_an_open_engagement(tmp_path: Path) -> None:
    from tests.engagement_root import engagement_root

    root = engagement_root(tmp_path)
    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    signed_in(client)
    gates = client.get("/partials/gates?company=acme").text
    assert "G1" in gates
    assert "Only you approve a gate." in gates
    assert "credit terms 90 days" in gates  # the engagement's own terms (SPEC-17 item 4)
    assert "No queries." in client.get("/partials/queries?company=acme").text
    assert "No FDD findings." in client.get("/partials/findings?company=acme").text


def test_skipped_pdf_pages_offer_a_pdf_add_on_that_installs_as_a_job(
    clean_root: Callable[..., Path], data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    import fin.services.addon
    from fin.db.conn import connect
    from fin.paths import company_db_path
    from fin.services.jobs import list_jobs
    from fin.services.result import Done

    clean_root("kestrel")
    unread = '[{"page": 4, "statement": "cash_flow", "code": "E202", "reason": "no periods"}]'
    with connect(company_db_path(data_root, "kestrel"), write=True) as con:
        con.execute("UPDATE documents SET unread_pages = ?", [unread])
    installed: list[tuple[str, str, str]] = []

    def install(root: Path, name: str, *, via: str, actor: str, progress: object) -> Done:
        installed.append((name, via, actor))
        return Done([f"installed {name}"], "run-1")

    monkeypatch.setattr(fin.services.addon, "install_addon", install)
    client = TestClient(
        create_app(data_root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    signed_in(client)
    files = client.get("/partials/files?company=kestrel").text
    assert "Read the PDF pages fin skipped" in files
    for label in ("Install Docling", "Install LiteParse", "Install LlamaParse"):
        assert label in files
    assert "LiteParse: local, nothing leaves this machine" in files
    assert "LlamaParse uploads the pages it reads to its cloud service" in files  # asked first

    origin = {"Origin": f"http://{HOST}"}
    form = {"action": "addon", "company": "kestrel"}
    refused = client.post("/job", data={**form, "choice": "pdfminer"}, headers=origin)
    assert refused.status_code == 400
    assert "E008" in refused.text
    assert list_jobs(data_root, "kestrel") == []
    started = client.post("/job", data={**form, "choice": "liteparse"}, headers=origin)
    assert started.status_code == 200
    assert 'id="jobs"' in started.text
    for _ in range(300):
        jobs = list_jobs(data_root, "kestrel")
        if jobs and jobs[0]["state"] == "done":
            break
        time.sleep(0.1)
    assert installed == [("liteparse", "ui", "user")]
    assert [(j["action"], j["state"], j["lines"]) for j in list_jobs(data_root, "kestrel")] == [
        ("addon", "done", ["installed liteparse"])
    ]


def test_no_add_on_is_offered_when_every_page_was_read(
    clean_root: Callable[..., Path], data_root: Path
) -> None:
    clean_root("kestrel")
    client = TestClient(
        create_app(data_root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    signed_in(client)
    assert (
        "Read the PDF pages fin skipped" not in client.get("/partials/files?company=kestrel").text
    )
