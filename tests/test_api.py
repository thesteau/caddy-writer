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
        caddy_target_file=tmp_path / "deploy-target" / "Caddyfile",
        **overrides,
    )
    settings.ensure_directories()
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
        data={"deploy": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_row_count"] == 1
    assert "svc.home" in payload["generated_text"]
    assert (work_tmpdir / "output" / "Caddyfile.generated").exists()


def test_url_translation_works(work_tmpdir: Path, monkeypatch) -> None:
    client = build_client(work_tmpdir)

    def fake_parse_csv_url(url: str):
        import pandas as pd

        return pd.read_csv(io.StringIO("host,upstream\nurl.home,http://192.168.1.4:9000\n"))

    monkeypatch.setattr(main.translator, "parse_csv_url", fake_parse_csv_url)

    response = client.post(
        "/translate/url",
        headers={"accept": "application/json", "content-type": "application/json"},
        json={"url": "https://example.com/sample.csv", "deploy": False},
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
