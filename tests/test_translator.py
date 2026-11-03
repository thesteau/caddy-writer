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


@pytest.mark.parametrize("suffix", ["/", "///", "/  "])
@pytest.mark.parametrize("redirect", [False, True])
def test_trailing_slashes_removed_from_addresses(suffix: str, redirect: bool) -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,redirect\n"
        f"app.home{suffix},http://192.168.1.50:8080{suffix},public,{redirect}\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    directive = (
        "redir http://192.168.1.50:8080{uri} permanent"
        if redirect else "reverse_proxy http://192.168.1.50:8080"
    )
    assert result == f"app.home {{\n    {directive}\n}}\n"


def test_trailing_slash_removed_before_building_upstream_with_port() -> None:
    prepared = _prepare(
        "host,upstream_host,upstream_port,tls_mode\n"
        "app.home,192.168.1.50/,8080,public\n"
    )

    assert translator.render_caddyfile(prepared.active_df) == (
        "app.home {\n"
        "    reverse_proxy http://192.168.1.50:8080\n"
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


def test_host_override_and_via_removal_share_proxy_block() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,header_up,header_up_remove\n"
        "duo3.home,http://192.168.5.15,internal,192.168.5.15,Via\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert result == (
        "duo3.home {\n"
        "    tls internal\n"
        "    reverse_proxy http://192.168.5.15 {\n"
        "        header_up -Via\n"
        "        header_up Host 192.168.5.15\n"
        "    }\n"
        "}\n"
    )


@pytest.mark.parametrize(
    ("removed_headers", "expected_headers"),
    [
        ("Via", "        header_up -Via\n"),
        (" Via   X-Debug ", "        header_up -Via\n        header_up -X-Debug\n"),
    ],
)
def test_header_removal_without_host_override(removed_headers: str, expected_headers: str) -> None:
    prepared = _prepare(
        "host,upstream,header_up_remove\n"
        f"duo3.home,http://192.168.5.15,{removed_headers}\n"
    )

    result = translator.render_caddyfile(prepared.active_df)

    assert result == (
        "duo3.home {\n"
        "    tls internal\n"
        "    reverse_proxy http://192.168.5.15 {\n"
        f"{expected_headers}"
        "    }\n"
        "}\n"
    )


@pytest.mark.parametrize("blank_headers", ["", "   "])
def test_blank_header_up_does_not_create_proxy_block(blank_headers: str) -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,header_up,header_up_remove\n"
        f"plain.home,http://192.168.1.20:8080,internal,{blank_headers},{blank_headers}\n"
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
        script_dir=work_tmpdir / "scripts-data",
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
        script_dir=work_tmpdir / "scripts-data",
        temp_dir=work_tmpdir / "tmp",
        caddy_output_dir=work_tmpdir / "deploy-target",
    )
    settings.caddy_output_dir.mkdir(parents=True, exist_ok=True)
    source_path = deploy.write_generated_file("example.home {\n}\n", settings=settings)

    copied_path = deploy.copy_generated_file_to_caddy_dir(source_path, settings=settings)

    assert copied_path.endswith("Caddyfile")
    assert (work_tmpdir / "deploy-target" / "Caddyfile").read_text(encoding="utf-8") == "example.home {\n}\n"


ROMM_BLOCK = (
    "romm.home {\n"
    "    tls internal\n"
    "\n"
    "    redir /streaming /streaming/ 308\n"
    "\n"
    "    handle /streaming/* {\n"
    "        reverse_proxy http://192.168.1.30:3067\n"
    "    }\n"
    "\n"
    "    handle {\n"
    "        reverse_proxy http://10.10.1.2:7331\n"
    "    }\n"
    "}\n"
)


def test_duplicate_host_is_rejected() -> None:
    with pytest.raises(translator.CSVValidationException) as exc_info:
        _prepare(
            "host,upstream\n"
            "romm.home,http://10.10.1.2\n"
            "ROMM.home,http://10.10.1.3\n"
        )

    errors = exc_info.value.errors
    assert [(error.row_number, error.column) for error in errors] == [(3, "host")]
    assert "already has a row" in errors[0].message


def test_disabled_rows_do_not_conflict() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,enabled\n"
        "app.home,http://192.168.1.2,public,false\n"
        "app.home,http://192.168.1.3,internal,true\n"
    )

    assert translator.render_caddyfile(prepared.active_df) == (
        "app.home {\n"
        "    tls internal\n"
        "    reverse_proxy http://192.168.1.3\n"
        "}\n"
    )


def test_http_only_row_stays_a_separate_site() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode\n"
        "app.home,http://192.168.1.2,internal\n"
        "app.home,http://192.168.1.3,off\n"
    )

    assert translator.render_caddyfile(prepared.active_df) == (
        "app.home {\n"
        "    tls internal\n"
        "    reverse_proxy http://192.168.1.2\n"
        "}\n"
        "\n"
        "http://app.home {\n"
        "    reverse_proxy http://192.168.1.3\n"
        "}\n"
    )


def test_sample_csv_renders_path_routed_host() -> None:
    sample_csv = Path(__file__).resolve().parents[1] / "sample" / "sample.csv"
    prepared = translator.prepare_dataframe(pd.read_csv(sample_csv))

    assert ROMM_BLOCK in translator.render_caddyfile(prepared.active_df)


@pytest.mark.parametrize(
    "entry",
    [
        "/streaming=http://192.168.1.30:3067",
        "/streaming/=http://192.168.1.30:3067/",
        "/streaming/*=http://192.168.1.30:3067",
    ],
)
def test_path_routes_column_renders_one_site_from_one_row(entry: str) -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,path_routes\n"
        f"romm.home,http://10.10.1.2:7331,internal,{entry}\n"
    )

    assert translator.render_caddyfile(prepared.active_df) == ROMM_BLOCK
    assert prepared.warnings == []
    assert len(prepared.active_df.index) == 1


def test_path_routes_entries_do_not_inherit_row_options() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,skip_verify,header_up_remove,path_routes\n"
        "app.home,https://192.168.1.2,public,true,Via,/api=https://192.168.1.3 /ws=http://192.168.1.4\n"
    )

    assert translator.render_caddyfile(prepared.active_df) == (
        "app.home {\n"
        "    redir /api /api/ 308\n"
        "    redir /ws /ws/ 308\n"
        "\n"
        "    handle /api/* {\n"
        "        reverse_proxy https://192.168.1.3\n"
        "    }\n"
        "\n"
        "    handle /ws/* {\n"
        "        reverse_proxy http://192.168.1.4\n"
        "    }\n"
        "\n"
        "    handle {\n"
        "        reverse_proxy https://192.168.1.2 {\n"
        "            transport http {\n"
        "                tls_insecure_skip_verify\n"
        "            }\n"
        "            header_up -Via\n"
        "        }\n"
        "    }\n"
        "}\n"
    )


@pytest.mark.parametrize(
    "entry",
    [
        "streaming=http://10.10.1.3",
        "/=http://10.10.1.3",
        "/streaming",
        "/streaming=",
        "/streaming=ftp://10.10.1.3",
        "=http://10.10.1.3",
        "/*=http://10.10.1.3",
        "/a*=http://10.10.1.3",
        "/{host}=http://10.10.1.3",
        "/a#b=http://10.10.1.3",
    ],
)
def test_invalid_path_routes_entry_is_rejected(entry: str) -> None:
    with pytest.raises(translator.CSVValidationException) as exc_info:
        _prepare(f"host,upstream,path_routes\nromm.home,http://10.10.1.2,{entry}\n")

    assert [(error.row_number, error.column) for error in exc_info.value.errors] == [(2, "path_routes")]


def test_path_routes_entry_conflicting_with_same_cell_is_rejected() -> None:
    with pytest.raises(translator.CSVValidationException) as exc_info:
        _prepare(
            "host,upstream,path_routes\n"
            "romm.home,http://10.10.1.2,/streaming=http://10.10.1.3 /streaming/=http://10.10.1.4\n"
        )

    errors = exc_info.value.errors
    assert [(error.row_number, error.column) for error in errors] == [(2, "path_routes")]
    assert "already has a route for /streaming" in errors[0].message


def test_path_column_is_rejected() -> None:
    with pytest.raises(translator.CSVValidationException) as exc_info:
        _prepare("host,upstream,path\nromm.home,http://10.10.1.2,/streaming\n")

    assert [(error.row_number, error.column) for error in exc_info.value.errors] == [(None, "path")]


def test_blank_columns_and_rows_are_ignored_with_warnings() -> None:
    prepared = _prepare(
        "host,upstream,tls_mode,path_routes,,,\n"
        ",,,,,,\n"
        "romm.home,http://10.10.1.2:7331,internal,/streaming=http://192.168.1.30:3067,,,\n"
    )

    assert translator.render_caddyfile(prepared.active_df) == ROMM_BLOCK
    assert prepared.warnings == [
        "Ignored 3 empty column(s) with no header.",
        "Ignored 1 empty row(s).",
    ]
    assert len(prepared.normalized_df.index) == 1


def test_whitespace_only_cells_count_as_blank() -> None:
    prepared = _prepare("host,upstream, \n , , \napp.home,http://10.10.1.2, \n")

    assert prepared.warnings == ["Ignored 1 empty column(s) with no header.", "Ignored 1 empty row(s)."]


def test_error_row_numbers_still_match_the_sheet_after_blank_rows() -> None:
    with pytest.raises(translator.CSVValidationException) as exc_info:
        _prepare("host,upstream\n,\n,\nromm.home,\n")

    assert [(error.row_number, error.column) for error in exc_info.value.errors] == [(4, "upstream")]


def test_headerless_column_with_data_is_rejected_by_sheet_letter() -> None:
    with pytest.raises(translator.CSVValidationException) as exc_info:
        _prepare("host,upstream,,notes,\nromm.home,http://10.10.1.2,oops,RomM,\n")

    errors = exc_info.value.errors
    assert [(error.row_number, error.column) for error in errors] == [(None, "column C")]
    assert str(errors[0]) == "column C: Has data but no header. Add a header or clear the column."
    assert str(errors[0]) in str(exc_info.value)


def test_empty_column_with_unsupported_header_is_still_rejected() -> None:
    with pytest.raises(translator.CSVValidationException) as exc_info:
        _prepare("host,upstream,upstrem\nromm.home,http://10.10.1.2,\n")

    assert [(error.row_number, error.column) for error in exc_info.value.errors] == [(None, "upstrem")]


def test_csv_with_only_blank_rows_reports_no_data() -> None:
    with pytest.raises(translator.CSVValidationException) as exc_info:
        _prepare("host,upstream,\n,,\n,,\n")

    assert [error.column for error in exc_info.value.errors] == ["csv"]


@pytest.mark.parametrize(("position", "letter"), [(1, "A"), (17, "Q"), (26, "Z"), (27, "AA"), (28, "AB")])
def test_column_letter(position: int, letter: str) -> None:
    assert translator._column_letter(position) == letter


def test_disabled_row_ignores_its_path_routes() -> None:
    prepared = _prepare(
        "host,upstream,enabled,path_routes\n"
        "romm.home,http://10.10.1.2,false,/streaming=http://10.10.1.3\n"
        "romm.home,http://10.10.1.4,true,\n"
    )

    assert translator.render_caddyfile(prepared.active_df) == (
        "romm.home {\n"
        "    tls internal\n"
        "    reverse_proxy http://10.10.1.4\n"
        "}\n"
    )
