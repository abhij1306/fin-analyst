"""The optional web UI server: its local-only protections and its pages (docs/web-ui-plan.md).
Needs the `ui` extra (CI's ui job syncs it); skipped without it."""

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from fin.ui.security import COOKIE  # noqa: E402
from fin.ui.server import create_app  # noqa: E402

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
    assert "Chat is not connected yet" in page.text


def test_the_home_partial_is_the_screen_only(client: TestClient) -> None:
    partial = signed_in(client).get("/partials/home?company=acme")
    assert partial.status_code == 200
    assert "<html" not in partial.text
    assert "<h2>acme</h2>" in partial.text


def test_an_unknown_company_is_e001_shown_as_an_alert(client: TestClient) -> None:
    response = signed_in(client).get("/partials/home?company=ghost")
    assert response.status_code == 400
    assert "E001" in response.text
    assert "alert-destructive" in response.text


def test_company_names_are_escaped(client: TestClient) -> None:
    response = signed_in(client).get("/partials/home?company=<script>alert(1)</script>")
    assert "<script>alert(1)" not in response.text


def test_the_page_says_so_when_there_are_no_companies(data_root: Path) -> None:
    data_root.mkdir(parents=True)
    client = TestClient(
        create_app(data_root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    assert "No companies yet" in signed_in(client).get("/").text
