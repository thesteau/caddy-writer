from __future__ import annotations

import io
import shutil
import uuid
from pathlib import Path

import pandas as pd
import pytest

from app import deploy, translator
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


def test_redirect_translation() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,redirect\n"
        "gateway.home,http://192.168.1.1,public,true\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert result == (
        "gateway.home {\n"
        "    redir http://192.168.1.1{uri} permanent\n"
        "}\n"
    )


def test_header_up_and_skip_verify_share_proxy_block() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,skip_verify,header_up\n"
        "nas.home,https://192.168.1.2:5001,internal,true,{upstream_hostport}\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert result == (
        "nas.home {\n"
        "    tls internal\n"
        "    reverse_proxy https://192.168.1.2:5001 {\n"
        "        transport http {\n"
            "            tls_insecure_skip_verify\n"
        "        }\n"
        "        header_up Host {upstream_hostport}\n"
        "    }\n"
        "}\n"
    )


def test_blank_header_up_does_not_create_proxy_block() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,header_up\n"
        "plain.home,http://192.168.1.20:8080,internal,\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert result == (
        "plain.home {\n"
        "    tls internal\n"
        "    reverse_proxy http://192.168.1.20:8080\n"
        "}\n"
    )


def test_transport_versions_and_multiple_headers_render_when_present() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,skip_verify,transport_versions,header_up,header_up_origin,header_up_x_forwarded_host,header_up_x_forwarded_port,header_up_x_forwarded_proto,header_up_x_real_ip\n"
        "gateway.home,https://192.168.1.1,internal,true,1.1,{host},https://gateway.home,{host},443,https,{remote_host}\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert result == (
        "gateway.home {\n"
        "    tls internal\n"
        "    reverse_proxy https://192.168.1.1 {\n"
        "        transport http {\n"
        "            versions 1.1\n"
        "            tls_insecure_skip_verify\n"
        "        }\n"
        "        header_up Host {host}\n"
        "        header_up Origin https://gateway.home\n"
        "        header_up X-Forwarded-Host {host}\n"
        "        header_up X-Forwarded-Port 443\n"
        "        header_up X-Forwarded-Proto https\n"
        "        header_up X-Real-IP {remote_host}\n"
        "    }\n"
        "}\n"
    )


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


def test_write_and_read_generated_file(work_tmpdir) -> None:
    settings = Settings(
        output_dir=work_tmpdir / "output",
        temp_dir=work_tmpdir / "tmp",
        caddy_output_dir=work_tmpdir / "deploy-target",
    )
    generated_path = deploy.write_generated_file("example.home {\n}\n", settings=settings)
    read_path, generated_text = deploy.read_generated_file(settings=settings)

    assert generated_path == read_path
    assert generated_text == "example.home {\n}\n"


def test_copy_generated_file_to_caddy_dir(work_tmpdir) -> None:
    settings = Settings(
        output_dir=work_tmpdir / "output",
        temp_dir=work_tmpdir / "tmp",
        caddy_output_dir=work_tmpdir / "deploy-target",
    )
    settings.caddy_output_dir.mkdir(parents=True, exist_ok=True)
    source_path = deploy.write_generated_file("example.home {\n}\n", settings=settings)

    copied_path = deploy.copy_generated_file_to_caddy_dir(source_path, settings=settings)

    assert copied_path.endswith("Caddyfile")
    assert (work_tmpdir / "deploy-target" / "Caddyfile").read_text(encoding="utf-8") == "example.home {\n}\n"
