from __future__ import annotations

import io
import shutil
import uuid
from pathlib import Path

import pandas as pd
import pytest

from app import deploy, translator
from app.models import CommandResult
from app.settings import Settings


@pytest.fixture
def work_tmpdir() -> Path:
    path = Path(".test-tmp") / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _prepare(csv_text: str):
    dataframe = pd.read_csv(io.StringIO(csv_text))
    return translator.prepare_dataframe(dataframe)


def test_http_upstream_translation() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,skip_verify\n"
        "jellyfin.home,http://192.168.1.50:8096,internal,false\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert result == (
        "jellyfin.home {\n"
        "    tls internal\n"
        "    reverse_proxy http://192.168.1.50:8096\n"
        "}\n"
    )


def test_https_upstream_with_skip_verify() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,skip_verify\n"
        "nas.home,https://192.168.1.2:5001,internal,true\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert "tls internal" in result
    assert "tls_insecure_skip_verify" in result


def test_public_tls_translation() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode\n"
        "app.example.com,http://192.168.1.50:8080,public\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert result == (
        "app.example.com {\n"
        "    reverse_proxy http://192.168.1.50:8080\n"
        "}\n"
    )


def test_enabled_false_row_skipped() -> None:
    prepared = _prepare(
        "host,upstream,enabled\n"
        "skip.home,http://192.168.1.9:9000,false\n"
        "keep.home,http://192.168.1.10:9000,true\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert "skip.home" not in result
    assert prepared.skipped_row_count == 1


def test_malformed_row_rejected() -> None:
    dataframe = pd.read_csv(
        io.StringIO(
            "host,upstream,tls_mode,skip_verify\n"
            "bad.home,not-a-url,invalid,maybe\n"
        )
    )

    normalized = translator.normalize_dataframe(dataframe)
    errors = translator.validate_dataframe(normalized)

    assert any(error.column == "upstream" for error in errors)
    assert any(error.column == "tls_mode" for error in errors)
    assert any(error.column == "skip_verify" for error in errors)


def test_url_import_works(monkeypatch) -> None:
    class DummyResponse:
        text = "host,upstream\nsvc.home,http://192.168.1.2:8080\n"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(translator.requests, "get", lambda url, timeout: DummyResponse())

    dataframe = translator.parse_csv_url(
        "https://docs.google.com/spreadsheets/d/test-sheet-id/edit#gid=42"
    )

    assert list(dataframe.columns) == ["host", "upstream"]
    assert dataframe.iloc[0]["host"] == "svc.home"


def test_deployment_blocked_when_target_missing(work_tmpdir: Path) -> None:
    generated = work_tmpdir / "Caddyfile.generated"
    generated.write_text("example.home {\n    respond \"ok\"\n}\n", encoding="utf-8")
    settings = Settings(
        output_dir=work_tmpdir / "output",
        temp_dir=work_tmpdir / "tmp",
        caddy_target_file=work_tmpdir / "missing" / "Caddyfile",
        docker_socket_enabled=True,
    )

    result = deploy.deploy_generated_file(generated, settings=settings)

    assert not result.succeeded
    assert result.copy_result is not None
    assert "missing" in result.copy_result.stderr.lower()


def test_reload_blocked_when_validation_fails(work_tmpdir: Path, monkeypatch) -> None:
    generated = work_tmpdir / "Caddyfile.generated"
    target = work_tmpdir / "Caddyfile"
    generated.write_text("example.home {\n    respond \"ok\"\n}\n", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    settings = Settings(
        output_dir=work_tmpdir / "output",
        temp_dir=work_tmpdir / "tmp",
        caddy_target_file=target,
        docker_socket_enabled=True,
    )

    monkeypatch.setattr(
        deploy,
        "ensure_caddy_container_running",
        lambda settings=None: CommandResult(attempted=True, succeeded=True, stdout="true"),
    )
    monkeypatch.setattr(
        deploy,
        "validate_caddy",
        lambda settings=None: CommandResult(
            attempted=True,
            succeeded=False,
            stderr="validation failed",
        ),
    )

    calls = {"reload": 0}

    def fake_reload(settings=None):
        calls["reload"] += 1
        return CommandResult(attempted=True, succeeded=True)

    monkeypatch.setattr(deploy, "reload_caddy", fake_reload)

    result = deploy.deploy_generated_file(generated, settings=settings)

    assert not result.succeeded
    assert result.validate_result is not None
    assert result.validate_result.stderr == "validation failed"
    assert calls["reload"] == 0
