from __future__ import annotations

import io
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.settings import Settings, get_settings


@pytest.fixture
def work_tmpdir() -> Path:
    path = Path(".test-tmp") / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        main.app.dependency_overrides.clear()
        shutil.rmtree(path, ignore_errors=True)


def build_client(tmp_path: Path, **overrides) -> TestClient:
    settings = Settings(
        output_dir=tmp_path / "output",
        script_dir=tmp_path / "scripts-data",
        temp_dir=tmp_path / "tmp",
        caddy_output_dir=tmp_path / "deploy-target",
        **overrides,
    )
    settings.ensure_directories()
    settings.caddy_output_dir.mkdir(parents=True, exist_ok=True)
    main.app.dependency_overrides = {}
    main.app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(main.app)
    return client


def test_health(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_translation_returns_json_and_writes_output(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)

    response = client.post(
        "/translate/upload",
        headers={"accept": "application/json"},
        files={
            "csv_file": (
                "sample.csv",
                io.BytesIO(b"host,upstream\nsvc.home,http://192.168.1.2:8080\n"),
                "text/csv",
            )
        },
        data={"preview_only": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_row_count"] == 1
    assert "svc.home" in payload["generated_text"]
    assert payload["copied_to_caddy_dir"] is True
    assert (work_tmpdir / "output" / "Caddyfile.generated").exists()
    assert (work_tmpdir / "deploy-target" / "Caddyfile").exists()


def test_url_translation_works(work_tmpdir: Path, monkeypatch) -> None:
    client = build_client(work_tmpdir)

    def fake_parse_csv_url(url: str):
        import pandas as pd

        return pd.read_csv(io.StringIO("host,upstream\nurl.home,http://192.168.1.4:9000\n"))

    monkeypatch.setattr(main.translator, "parse_csv_url", fake_parse_csv_url)

    response = client.post(
        "/translate/url",
        headers={"accept": "application/json", "content-type": "application/json"},
        json={"url": "https://example.com/sample.csv", "preview_only": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "url"
    assert "url.home" in payload["generated_text"]


def test_save_custom_script_persists_command_and_uploaded_file(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)

    response = client.post(
        "/custom-script/save",
        data={"command": "bash ./update.sh"},
        files={
            "script_file": (
                "update.sh",
                io.BytesIO(b"#!/bin/sh\necho updated\n"),
                "text/x-shellscript",
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    workspace_dir = work_tmpdir / "scripts-data"
    assert (workspace_dir / "custom-script.json").exists()
    assert (workspace_dir / "update.sh").read_text(encoding="utf-8") == "#!/bin/sh\necho updated\n"
    assert '"command": "bash ./update.sh"' in (workspace_dir / "custom-script.json").read_text(encoding="utf-8")


def test_upload_translation_runs_saved_custom_script_when_requested(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)
    command = (
        f'"{sys.executable}" -c "from pathlib import Path; '
        "Path('ran.txt').write_text(Path.cwd().name + '|' + "
        "Path('Caddyfile.generated').read_text(encoding='utf-8').splitlines()[0], encoding='utf-8')\""
    )
    client.post("/custom-script/save", data={"command": command})

    response = client.post(
        "/translate/upload",
        headers={"accept": "application/json"},
        files={
            "csv_file": (
                "sample.csv",
                io.BytesIO(b"host,upstream\nsvc.home,http://192.168.1.2:8080\n"),
                "text/csv",
            )
        },
        data={"preview_only": "false", "run_custom_script": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["script_execution"]["attempted"] is True
    assert payload["script_execution"]["succeeded"] is True
    ran_file = work_tmpdir / "scripts-data" / "ran.txt"
    assert ran_file.read_text(encoding="utf-8") == "scripts-data|svc.home {"


def test_translation_skips_saved_custom_script_without_flag(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)
    command = f'"{sys.executable}" -c "from pathlib import Path; Path(\'ran.txt\').write_text(\'yes\', encoding=\'utf-8\')"'
    client.post("/custom-script/save", data={"command": command})

    response = client.post(
        "/translate/upload",
        headers={"accept": "application/json"},
        files={
            "csv_file": (
                "sample.csv",
                io.BytesIO(b"host,upstream\nsvc.home,http://192.168.1.2:8080\n"),
                "text/csv",
            )
        },
        data={"preview_only": "false"},
    )

    assert response.status_code == 200
    assert response.json()["script_execution"] is None
    assert not (work_tmpdir / "scripts-data" / "ran.txt").exists()


def test_custom_script_failure_is_reported_without_failing_translation(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)
    command = (
        f'"{sys.executable}" -c "import sys; '
        "sys.stderr.write('boom\\n'); sys.exit(7)\""
    )
    client.post("/custom-script/save", data={"command": command})

    response = client.post(
        "/translate/upload",
        headers={"accept": "application/json"},
        files={
            "csv_file": (
                "sample.csv",
                io.BytesIO(b"host,upstream\nsvc.home,http://192.168.1.2:8080\n"),
                "text/csv",
            )
        },
        data={"run_custom_script": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_row_count"] == 1
    assert payload["script_execution"]["succeeded"] is False
    assert payload["script_execution"]["exit_code"] == 7
    assert "boom" in payload["script_execution"]["stderr"]
    assert "Custom script command failed after translation." in payload["warnings"]


def test_preview_latest_returns_generated_text(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)
    output_file = work_tmpdir / "output" / "Caddyfile.generated"
    output_file.write_text("preview.home {\n    respond \"ok\"\n}\n", encoding="utf-8")

    response = client.get("/preview/latest")

    assert response.status_code == 200
    assert "preview.home" in response.text


def test_translate_upload_surfaces_validation_error(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)

    response = client.post(
        "/translate/upload",
        headers={"accept": "application/json"},
        files={
            "csv_file": (
                "bad.csv",
                io.BytesIO(b"host,upstream,tls_mode\nbroken.home,http://192.168.1.2,wrong\n"),
                "text/csv",
            )
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["details"]


def test_deploy_latest_copies_generated_file(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)
    output_file = work_tmpdir / "output" / "Caddyfile.generated"
    output_file.write_text("manual.home {\n    respond \"ok\"\n}\n", encoding="utf-8")

    response = client.post("/deploy/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "mounted Caddy directory" in payload["message"]
    assert "manual.home" in payload["generated_text"]
    assert payload["caddy_generated_file_path"].endswith("Caddyfile")
    assert (work_tmpdir / "deploy-target" / "Caddyfile").read_text(encoding="utf-8") == "manual.home {\n    respond \"ok\"\n}\n"


def test_saved_url_persists_and_can_be_overwritten(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)
    url = "https://docs.google.com/spreadsheets/d/example/edit#gid=123"
    response = client.post("/source/save", data={"url": url})
    assert response.status_code == 200
    assert "Source URL saved." in response.text
    assert url in response.text
    assert f'href="{url}" target="_blank" rel="noopener noreferrer">Preview Saved URL (new tab)</a>' in response.text
    assert not (work_tmpdir / "output" / "Caddyfile.generated").exists()
    client = build_client(work_tmpdir)
    assert url in client.get("/").text
    replacement = "https://example.com/new.csv"
    response = client.post("/source/save", json={"url": replacement})
    assert response.json()["url"] == replacement
    assert main.saved_source.load_url(main.app.dependency_overrides[get_settings]()) == replacement
    assert url not in client.get("/").text


@pytest.mark.parametrize("url", ["", "file:///tmp/test.csv", "https://", "https://bad host/a"])
def test_invalid_saved_url_preserves_previous(work_tmpdir: Path, url: str) -> None:
    client = build_client(work_tmpdir)
    original = "https://example.com/original.csv"
    client.post("/source/save", json={"url": original})
    response = client.post("/source/save", json={"url": url})
    assert response.status_code == 400
    assert original in client.get("/").text


def test_saved_translation_fetches_fresh_and_runs_requested_script(work_tmpdir: Path, monkeypatch) -> None:
    client = build_client(work_tmpdir)
    url = "https://example.com/saved.csv"
    client.post("/source/save", data={"url": url})
    live_file = work_tmpdir / "deploy-target" / "Caddyfile"
    live_file.write_text("existing config")
    calls = []

    def fetch(source):
        calls.append(source)
        return main.translator.parse_csv_upload(io.StringIO(
            f"host,upstream\nversion{len(calls)}.home,http://localhost:8080\n"))

    script_calls = []

    def run_script(**kwargs):
        script_calls.append(True)
        return main.custom_script.ScriptExecutionResult(attempted=True, succeeded=True)

    monkeypatch.setattr(main.translator, "parse_csv_url", fetch)
    monkeypatch.setattr(main.custom_script, "run_saved_command", run_script)
    for version in (1, 2):
        response = client.post("/translate/saved", data={"preview_only": "true", "run_custom_script": "true"},
                               headers={"accept": "application/json"})
        assert response.status_code == 200
        assert f"version{version}.home" in response.json()["generated_text"]
        assert response.json()["copied_to_caddy_dir"] is False
        assert response.json()["script_execution"]["succeeded"] is True
        assert live_file.read_text() == "existing config"
    response = client.post("/translate/saved", headers={"accept": "application/json"})
    assert response.json()["copied_to_caddy_dir"] is True
    assert "version3.home" in live_file.read_text()
    assert calls == [url, url, url]
    assert script_calls == [True, True]


def test_saved_source_missing_disabled_and_corrupt(work_tmpdir: Path, monkeypatch) -> None:
    client = build_client(work_tmpdir, allow_url_fetch=False)
    headers = {"accept": "application/json"}
    assert client.post("/translate/saved", headers=headers).status_code == 400
    client.post("/source/save", data={"url": "https://example.com/saved.csv"})
    response = client.post("/translate/saved", headers=headers)
    assert response.status_code == 400
    assert "disabled" in response.json()["error"]
    (work_tmpdir / "output" / "saved-source.json").write_text("broken json")
    assert client.get("/").status_code == 200
    assert "No source saved yet" in client.get("/").text


def test_html_translation_uses_shared_dark_layout(work_tmpdir: Path) -> None:
    client = build_client(work_tmpdir)
    response = client.post("/translate/upload", files={
        "csv_file": ("sample.csv", b"host,upstream\nsvc.home,http://localhost:8080\n", "text/csv")},
        data={"preview_only": "true"})
    assert response.status_code == 200
    assert 'name="color-scheme" content="dark"' in response.text
    assert "svc.home" in response.text
    assert "Paste URL" in client.get("/").text


@pytest.mark.parametrize("has_command", [False, True])
def test_one_click_update(work_tmpdir: Path, monkeypatch, has_command: bool) -> None:
    client = build_client(work_tmpdir)
    url = "https://example.com/saved.csv"
    client.post("/source/save", data={"url": url})
    if has_command:
        command = f'"{sys.executable}" -c "from pathlib import Path; Path(\'updated.txt\').write_text(\'done\')"'
        client.post("/custom-script/save", data={"command": command})

    def fetch(source):
        assert source == url
        return main.translator.parse_csv_upload(io.StringIO(
            "host,upstream\nupdated.home,http://localhost:8080\n"))

    monkeypatch.setattr(main.translator, "parse_csv_url", fetch)
    response = client.post("/update", headers={"accept": "application/json"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["copied_to_caddy_dir"] is True
    assert "updated.home" in (work_tmpdir / "deploy-target" / "Caddyfile").read_text()
    assert (work_tmpdir / "scripts-data" / "updated.txt").exists() == has_command
    if has_command:
        assert payload["script_execution"]["succeeded"] is True
    else:
        assert payload["script_execution"] is None
    html = client.get("/").text
    assert 'action="/update"' in html
    assert '>Update</button>' in html
    assert '<summary>Change saved URL</summary>' in html


@pytest.mark.parametrize("endpoint", ["/translate/url", "/source/save"])
def test_malformed_json_is_a_client_error(work_tmpdir: Path, endpoint: str) -> None:
    client = build_client(work_tmpdir)
    response = client.post(endpoint, content="{", headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"] == "Invalid JSON request body."


@pytest.mark.parametrize("accept", ["application/json", "text/html"])
def test_update_without_source_reports_error(work_tmpdir: Path, accept: str) -> None:
    client = build_client(work_tmpdir)
    response = client.post("/update", headers={"accept": accept})
    assert response.status_code == 400
    assert "No saved URL is available" in response.text
    assert not (work_tmpdir / "output" / "Caddyfile.generated").exists()


def test_failed_update_preserves_live_config_and_does_not_run_script(work_tmpdir: Path, monkeypatch) -> None:
    client = build_client(work_tmpdir)
    client.post("/source/save", data={"url": "https://example.com/saved.csv"})
    client.post("/custom-script/save", data={"command": "saved-command"})
    live_file = work_tmpdir / "deploy-target" / "Caddyfile"
    live_file.write_text("existing config")

    def fetch(source):
        return main.translator.parse_csv_upload(io.StringIO("host,upstream\nbroken.home,invalid\n"))

    def forbidden_script(**kwargs):
        pytest.fail("Invalid input must not run the saved command")

    monkeypatch.setattr(main.translator, "parse_csv_url", fetch)
    monkeypatch.setattr(main.custom_script, "run_saved_command", forbidden_script)
    response = client.post("/update", headers={"accept": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"] == "CSV validation failed."
    assert live_file.read_text() == "existing config"
    assert not (work_tmpdir / "output" / "Caddyfile.generated").exists()
