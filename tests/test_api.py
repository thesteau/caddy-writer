from __future__ import annotations

import io
import shutil
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
    yield path
    shutil.rmtree(path, ignore_errors=True)


def build_client(tmp_path: Path, **overrides) -> TestClient:
    settings = Settings(
        output_dir=tmp_path / "output",
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
