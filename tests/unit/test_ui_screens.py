"""The screens SPEC-11 adds to the web UI: grouped navigation, Reports that make reports, Questions,
Dashboards, Settings, Health, Log filters and trails, and FDD finding drafts. Needs the `ui` extra
(CI's ui job syncs it); skipped without it."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import fin.dashboard  # noqa: E402
import fin.services.reports  # noqa: E402
import fin.services.system  # noqa: E402
from fin.audit.errors import DependencyMissing  # noqa: E402
from fin.cli import app as cli  # noqa: E402
from fin.config import load_config  # noqa: E402
from fin.dashboard import last_good_build_dir  # noqa: E402
from fin.db.conn import connect  # noqa: E402
from fin.doctor import Check  # noqa: E402
from fin.fdd import findings  # noqa: E402
from fin.paths import audit_db_path  # noqa: E402
from fin.services.jobs import list_jobs  # noqa: E402
from fin.services.questions import saved_questions  # noqa: E402
from fin.services.reports import export_statements, report_lines  # noqa: E402
from fin.services.result import Done  # noqa: E402
from fin.ui import system_screens  # noqa: E402
from fin.ui.nav import SCREENS  # noqa: E402
from fin.ui.server import create_app  # noqa: E402
from fin.ui.system_routes import dashboard_file  # noqa: E402
from tests.engagement_root import engagement_root  # noqa: E402
from tests.qoe_fixtures import run  # noqa: E402

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = {"Origin": f"http://{HOST}"}
FINDING = {
    "area": "qoe",
    "title": "One-off legal fees added back",
    "observation": "Legal fees for a settled dispute are booked in other expenses.",
    "implication": "Adjusted EBITDA depends on the fees not recurring.",
    "impact_type": "ebitda",
    "severity": "high",
    "deal_consideration": "spa_protection",
    "evidence": "Notes to accounts, legal fees note",
}


def signed_in(root: Path) -> TestClient:
    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return client


def finished_job(root: Path, company: str) -> dict[str, Any]:
    for _ in range(240):  # a slow poll: each read makes the job's writes wait
        time.sleep(0.5)
        jobs = list_jobs(root, company)
        if jobs and jobs[0]["state"] not in ("queued", "running"):
            return jobs[0]
    raise AssertionError("the job did not finish")


def job(client: TestClient, company: str, action: str, **form: str) -> Any:
    return client.post("/job", data={"action": action, "company": company, **form}, headers=ORIGIN)


def test_the_top_bar_groups_the_screens_in_menus_and_marks_the_open_one(tmp_path: Path) -> None:
    page = signed_in(engagement_root(tmp_path)).get("/questions?company=acme").text
    assert 'id="tab-home" href="/?company=acme">Home</a>' in page
    for group in ("data", "analysis", "diligence", "system"):
        assert (
            f'id="nav-{group}-trigger" aria-haspopup="menu" aria-controls="nav-{group}-menu"'
            in page
        )
        assert f'role="menu" id="nav-{group}-menu" aria-labelledby="nav-{group}-trigger"' in page
    for screen, label in SCREENS[1:]:
        assert f'role="menuitem" id="tab-{screen}" href="/{screen}?company=acme"' in page
        assert f">{label}</a>" in page or f">{label}<span" in page
    assert page.count('aria-current="page"') == 1
    assert 'id="tab-questions" href="/questions?company=acme" aria-current="page">' in page
    assert 'data-variant="secondary" id="nav-analysis-trigger"' in page
    assert 'data-variant="ghost" id="nav-data-trigger"' in page


@pytest.mark.timeout(240)  # fourteen screens, each page and partial
def test_every_screen_opens_for_a_company_with_no_data(tmp_path: Path) -> None:
    client = signed_in(engagement_root(tmp_path))
    for screen, _ in SCREENS[1:]:
        page = client.get(f"/{screen}?company=acme")
        assert page.status_code == 200, screen
        partial = client.get(f"/partials/{screen}?company=acme")
        assert partial.status_code == 200, screen
        assert "<html" not in partial.text, screen


def test_settings_and_health_open_before_any_company(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root.mkdir(parents=True)
    checks = [
        Check("uv", True, "0.11.2"),
        Check("quarto", False, "not found on PATH", "E001", "run `./install.sh`"),
        Check("poppler", True, "pdftoppm not found", fix="install poppler", warning=True),
    ]
    monkeypatch.setattr(system_screens, "run_checks", lambda root: checks)
    client = signed_in(data_root)
    health = client.get("/partials/health").text
    assert "1 check(s) failed." in health
    assert "failed E001" in health
    assert "<code>./install.sh</code>" in health
    assert "Update fin" not in health  # no audit database for jobs before fin init
    settings = client.get("/partials/settings").text
    assert f"<code>{data_root}</code>" in settings
    assert "config.toml cannot be read" in settings
    assert "Install Docling" not in settings


def test_update_and_add_ons_run_as_jobs_before_any_company(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert CliRunner().invoke(cli, ["init"]).exit_code == 0
    monkeypatch.setattr(fin.services.system, "update", lambda run: [])
    client = signed_in(data_root)
    health = client.get("/partials/health").text
    assert 'name="company" value=""' in health
    assert "Update fin</button>" in health
    assert "Install Docling</button>" in client.get("/partials/settings").text
    assert job(client, "", "update").status_code == 200
    done = finished_job(data_root, "")
    assert (done["action"], done["state"], done["lines"]) == ("update", "done", ["update finished"])
    refused = job(client, "", "export")
    assert "there is no company yet" in refused.text


def test_settings_show_the_config_and_offer_the_add_ons_not_chosen(tmp_path: Path) -> None:
    root = engagement_root(tmp_path)
    settings = signed_in(root).get("/partials/settings?company=acme").text
    shown = load_config(root).display_for("acme")
    assert (
        f"<dt>Numbers shown in</dt><dd>{shown.unit}, {shown.decimals} decimals, "
        f"negatives as {shown.negatives}"
    ) in settings
    assert "<code>[display]</code> in config.toml" in settings
    assert "Chosen now: <strong>none</strong>" in settings
    for name, label in (
        ("docling", "Docling"),
        ("liteparse", "LiteParse"),
        ("llamaparse", "LlamaParse"),
    ):
        assert f'name="choice" value="{name}"' in settings
        assert f"Install {label}</button>" in settings
    assert "other AI providers are on hold" in settings


def test_the_reports_screen_makes_each_kind_of_report_as_a_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = engagement_root(tmp_path)
    calls: list[tuple[str, str, str, str]] = []

    def generate(
        root: Path, company: str, kind: str, *, via: str, actor: str, progress: Any
    ) -> Done:
        calls.append((company, kind, via, actor))
        return Done(
            [f"wrote the narrative stub reports/{company}/standalone/narrative.md"], "run-1"
        )

    monkeypatch.setattr(fin.services.reports, "generate_report", generate)
    client = signed_in(root)
    listing = client.get("/partials/reports?company=acme").text
    for label in ("Health report", "Diligence report", "FDD report"):
        assert f">{label}</button>" in listing
    refused = job(client, "acme", "report", choice="peers")
    assert refused.status_code == 400
    assert "E008" in refused.text
    assert job(client, "acme", "report", choice="fdd").status_code == 200
    done = finished_job(root, "acme")
    assert calls == [("acme", "fdd", "ui", "user")]
    assert (done["action"], done["state"], done["lines"]) == (
        "report", "done", ["wrote the narrative stub reports/acme/standalone/narrative.md"]
    )  # fmt: skip


def test_report_lines_say_what_the_run_did(tmp_path: Path) -> None:
    folder = tmp_path / "reports" / "acme" / "standalone"
    stub = {"status": "stub_written", "narrative_path": str(folder / "narrative.md"),
            "next": "Fill in each section"}  # fmt: skip
    assert report_lines(tmp_path, stub) == [
        "wrote the narrative stub reports/acme/standalone/narrative.md",
        "Fill in each section",
    ]
    outputs = [str(folder / "health.html"), str(folder / "health.pdf")]
    rendered = {"status": "rendered", "outputs": outputs, "missing_values": ["metric:roe:FY25"]}
    assert report_lines(tmp_path, rendered) == [
        "rendered reports/acme/standalone/health.html",
        "rendered reports/acme/standalone/health.pdf",
        "references with no value (n/a): metric:roe:FY25",
    ]


def test_the_reports_screen_lists_the_narratives_with_their_sections(tmp_path: Path) -> None:
    root = engagement_root(tmp_path)
    folder = root / "reports" / "acme" / "standalone"
    folder.mkdir(parents=True)
    (folder / "narrative.md").write_text("# Acme\n\n## Summary\n\ntext\n\n## Growth\n", "utf-8")
    listing = signed_in(root).get("/partials/reports?company=acme").text
    assert '<td class="fname">standalone/narrative.md</td><td>Summary, Growth</td>' in listing


@pytest.mark.timeout(180)  # an ingested company and a job
def test_a_saved_question_opens_downloads_and_runs_again(
    clean_root: Callable[..., Path], data_root: Path
) -> None:
    clean_root("kestrel")
    sql = "SELECT fact_id, value FROM facts WHERE account = 'revenue' ORDER BY fact_id LIMIT 1"
    asked = CliRunner().invoke(
        cli, ["sql", "kestrel", "--sql", sql, "--question", "Revenue?", "--evidence"]
    )
    assert asked.exit_code == 0, asked.output
    [saved] = saved_questions(data_root, "kestrel")
    client = signed_in(data_root)
    screen = client.get("/partials/questions?company=kestrel").text
    assert "Revenue?" in screen
    assert "WHERE account = &#39;revenue&#39;" in screen
    href = f"/evidence-file?company=kestrel&query_id={saved['query_id']}"
    opened = client.get(href)
    assert opened.status_code == 200
    assert "Evidence for query" in opened.text
    assert opened.headers["content-security-policy"].split()[0] == "sandbox"
    assert "attachment" in client.get(href + "&download=true").headers["content-disposition"]

    unknown = job(client, "kestrel", "rerun", item="no-such-query")
    assert unknown.status_code == 400
    assert "E001" in unknown.text
    assert list_jobs(data_root, "kestrel") == []
    assert job(client, "kestrel", "rerun", item=saved["query_id"]).status_code == 200
    done = finished_job(data_root, "kestrel")
    again = saved_questions(data_root, "kestrel")[0]
    assert (done["state"], done["lines"]) == (
        "done",
        [f"1 rows; evidence page {again['evidence_path']}"],
    )
    assert (again["question"], again["sql"]) == ("Revenue?", sql)
    assert again["query_id"] != saved["query_id"]
    refused = client.get("/evidence-file?company=kestrel&query_id=nope")
    assert refused.status_code == 400
    assert "E001" in refused.text


def test_the_dashboard_builds_as_a_job_and_shows_its_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = engagement_root(tmp_path)
    client = signed_in(root)
    assert "Not built yet." in client.get("/partials/dashboards?company=acme").text

    def build(root: Path) -> Path:
        site = last_good_build_dir(root)
        (site / "company").mkdir(parents=True)
        (site / "index.html").write_text("<p>all companies</p>", "utf-8")
        (site / "company" / "index.html").write_text("<p>one company</p>", "utf-8")
        return site

    monkeypatch.setattr(fin.dashboard, "build", build)
    assert job(client, "acme", "dashboard").status_code == 200
    done = finished_job(root, "acme")
    assert (done["state"], done["lines"]) == ("done", ["dashboard built: dashboard/build"])
    screen = client.get("/partials/dashboards?company=acme").text
    assert '<iframe class="dashboard" src="/dashboard-site/" title="Dashboard">' in screen
    assert client.get("/dashboard-site/").text == "<p>all companies</p>"
    assert client.get("/dashboard-site/company/").text == "<p>one company</p>"
    missing = client.get("/dashboard-site/quality/")
    assert missing.status_code == 400
    assert "E001" in missing.text
    with pytest.raises(DependencyMissing, match="no dashboard page"):
        dashboard_file(root, "../config.toml")


def test_update_runs_as_a_job_and_shows_a_failed_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = engagement_root(tmp_path)

    def update(run: Any) -> list[str]:
        run.failed, run.exit_code = 1, 1
        return ["FAIL  quarto: not found on PATH [E001] fix: run ./install.sh"]

    monkeypatch.setattr(fin.services.system, "update", update)
    client = signed_in(root)
    assert "Update fin</button>" in client.get("/partials/health?company=acme").text
    assert job(client, "acme", "update").status_code == 200
    done = finished_job(root, "acme")
    assert done["state"] == "refused"
    assert done["lines"][0] == "FAIL  quarto: not found on PATH [E001] fix: run ./install.sh"
    assert done["lines"][1].startswith("update finished; fin doctor failed (update run_id ")


def test_the_log_filters_by_command_and_actor_and_traces_a_figure(
    clean_root: Callable[..., Path], data_root: Path
) -> None:
    clean_root("kestrel")
    export_statements(data_root, "kestrel", via="ui", actor="user")
    client = signed_in(data_root)
    everything = client.get("/partials/log?company=kestrel").text
    assert '<option value="user">the user</option>' in everything
    assert '<option value="not recorded">not recorded</option>' in everything
    assert everything.count('<span class="run">') == 2
    by_command = client.get("/partials/log?company=kestrel&command=ingest").text
    assert by_command.count('<span class="run">') == 1
    assert "<td>export</td>" not in by_command
    by_actor = client.get("/partials/log?company=kestrel&actor=user").text
    assert by_actor.count('<span class="run">') == 1
    assert "<td>export</td><td>user</td>" in by_actor
    traced = client.get("/partials/log?company=kestrel&fact=1").text
    assert '<pre class="trail">fact 1: ' in traced
    assert "Kestrel_Forgings_Financials_FY23-FY25.xlsx" in traced
    assert (
        "E504: &#39;one&#39; is not a fact id"
        in client.get("/partials/log?company=kestrel&fact=one").text
    )


def _audit_runs(root: Path, command: str) -> list[str]:
    with connect(audit_db_path(root)) as con:
        return [
            row[0]
            for row in con.execute(
                "SELECT args FROM runs WHERE command = ? AND record = 'start'", [command]
            ).fetchall()
        ]


def test_a_draft_finding_is_edited_and_dropped_from_the_findings_screen(tmp_path: Path) -> None:
    root = engagement_root(tmp_path)
    finding_id = findings.propose(run(root), "acme", FINDING)["finding_id"]
    client = signed_in(root)
    screen = client.get("/partials/findings?company=acme").text
    assert "Evidence: Notes to accounts, legal fees note" in screen
    for label in (">Edit</button>", ">Drop</button>", ">Mark final</button>"):
        assert label in screen
    form = {"company": "acme", "item": str(finding_id)}

    edited = client.post(
        "/fdd-edit", data={**form, "title": "Settled dispute fees added back"}, headers=ORIGIN
    )
    assert "Done" in edited.text
    assert "Settled dispute fees added back" in edited.text
    assert '"via": "ui"' in _audit_runs(root, "fdd finding update")[0]
    typed = client.post(
        "/fdd-edit", data={**form, "title": "Fees of 5 lakh added back"}, headers=ORIGIN
    )
    assert "Not done: E970" in typed.text
    assert (
        findings.list_findings(run(root), "acme")[0]["title"] == "Settled dispute fees added back"
    )
    empty = client.post("/fdd-edit", data={**form, "title": "  "}, headers=ORIGIN)
    assert "Not done: E008" in empty.text

    dropped = client.post("/act", data={**form, "action": "fdd-drop"}, headers=ORIGIN)
    assert "Done" in dropped.text
    assert "No FDD findings." in dropped.text
    assert [r["status"] for r in findings.list_findings(run(root), "acme", every=True)] == [
        "dropped"
    ]
