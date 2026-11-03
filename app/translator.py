from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests


SUPPORTED_COLUMNS = {
    "host",
    "upstream",
    "upstream_host",
    "upstream_scheme",
    "upstream_port",
    "tls_mode",
    "skip_verify",
    "redirect",
    "transport_versions",
    "header_up",
    "header_up_remove",
    "header_up_origin",
    "header_up_x_forwarded_host",
    "header_up_x_forwarded_port",
    "header_up_x_forwarded_proto",
    "header_up_x_real_ip",
    "path_routes",
    "notes",
    "enabled",
}
INTERNAL_ROW_COLUMN = "__row_number__"
BOOLEAN_TRUE = {"true", "1", "yes", "y", "on"}
BOOLEAN_FALSE = {"false", "0", "no", "n", "off", ""}
TLS_MODES = {"internal", "public", "off"}
UPSTREAM_SCHEMES = {"http", "https"}
GOOGLE_SHEETS_RE = re.compile(r"https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)")
ROUTE_PATH_RE = re.compile(r"(/[A-Za-z0-9._~%-]+)+")
HEADERLESS_COLUMN_RE = re.compile(r"(unnamed: \d+)?")


@dataclass
class ValidationError:
    row_number: int | None
    column: str
    message: str

    def __str__(self) -> str:
        if self.row_number is None:
            return f"{self.column}: {self.message}"
        return f"Row {self.row_number} [{self.column}]: {self.message}"


@dataclass
class PreparedTranslation:
    normalized_df: pd.DataFrame
    active_df: pd.DataFrame
    skipped_row_count: int
    warnings: list[str]


class CSVError(ValueError):
    """Raised when CSV input cannot be parsed or translated."""


class CSVValidationException(CSVError):
    def __init__(self, errors: list[ValidationError]):
        # Scheduled updates have no page to show details on, so logs rely on this message.
        super().__init__(f"CSV validation failed: {'; '.join(str(error) for error in errors)}")
        self.errors = errors


def parse_csv_upload(file_obj: Any) -> pd.DataFrame:
    try:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        return pd.read_csv(file_obj)
    except Exception as exc:
        raise CSVError(f"Invalid CSV upload: {exc}") from exc


def parse_csv_url(url: str) -> pd.DataFrame:
    final_url = normalize_csv_url(url)
    try:
        response = requests.get(final_url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise CSVError(f"Unable to fetch CSV URL: {exc}") from exc

    try:
        return pd.read_csv(io.StringIO(response.text))
    except Exception as exc:
        raise CSVError(f"Invalid CSV content at URL: {exc}") from exc


def normalize_csv_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        raise CSVError("A CSV URL is required.")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        raise CSVError("URL must start with http:// or https://.")
    if not parsed.hostname or any(char.isspace() for char in cleaned):
        raise CSVError("URL must include a valid host and contain no whitespace.")
    if parsed.hostname == "docs.google.com" and "/spreadsheets/" in parsed.path:
        return build_google_sheets_csv_url(cleaned)
    return cleaned


def build_google_sheets_csv_url(url: str) -> str:
    match = GOOGLE_SHEETS_RE.match(url.strip())
    if not match:
        raise CSVError("Bad Google Sheets link: could not find a spreadsheet ID.")

    spreadsheet_id = match.group(1)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    fragment = parse_qs(parsed.fragment)
    gid = query.get("gid", [None])[0] or fragment.get("gid", [None])[0]

    export_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv"
    if gid:
        export_url = f"{export_url}&gid={gid}"
    return export_url


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # Spreadsheet exports can pad a sheet with empty cells, which aren't mistakes worth failing on.
    has_data = df.map(lambda value: _normalize_cell(value) is not None)
    rows_with_data = has_data.any(axis=1).tolist()
    columns_with_data = has_data.any(axis=0).tolist()
    headers = [str(column).strip().lower() for column in df.columns]
    named_columns = [not HEADERLESS_COLUMN_RE.fullmatch(header) for header in headers]

    normalized = df.iloc[rows_with_data, named_columns].copy()
    normalized.columns = [header for header, named in zip(headers, named_columns) if named]
    unsupported_columns = [column for column in normalized.columns if column not in SUPPORTED_COLUMNS]
    duplicate_columns = list(pd.Index(normalized.columns)[pd.Index(normalized.columns).duplicated()])

    normalized.attrs["unsupported_columns"] = unsupported_columns
    normalized.attrs["duplicate_columns"] = duplicate_columns
    normalized.attrs["headerless_columns"] = [
        _column_letter(position)
        for position, (named, with_data) in enumerate(zip(named_columns, columns_with_data), start=1)
        if with_data and not named
    ]
    normalized.attrs["ignored_column_count"] = sum(
        not named and not with_data for named, with_data in zip(named_columns, columns_with_data)
    )
    normalized.attrs["ignored_row_count"] = rows_with_data.count(False)
    normalized[INTERNAL_ROW_COLUMN] = [index + 2 for index, with_data in enumerate(rows_with_data) if with_data]

    for column in normalized.columns:
        if column == INTERNAL_ROW_COLUMN:
            continue
        normalized[column] = normalized[column].map(_normalize_cell)

    for column in SUPPORTED_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None

    normalized["host"] = normalized["host"].map(_normalize_address)
    normalized["upstream"] = normalized["upstream"].map(_normalize_address)
    normalized["upstream_host"] = normalized["upstream_host"].map(_normalize_address)
    normalized["upstream_scheme"] = normalized["upstream_scheme"].map(_normalize_scheme)
    normalized["tls_mode"] = normalized["tls_mode"].map(_normalize_tls_mode)
    normalized["skip_verify"] = normalized["skip_verify"].map(lambda value: _parse_bool(value, default=False))
    normalized["redirect"] = normalized["redirect"].map(lambda value: _parse_bool(value, default=False))
    normalized["transport_versions"] = normalized["transport_versions"].map(_normalize_text)
    normalized["header_up"] = normalized["header_up"].map(_normalize_text)
    normalized["header_up_remove"] = normalized["header_up_remove"].map(_normalize_text)
    normalized["header_up_origin"] = normalized["header_up_origin"].map(_normalize_text)
    normalized["header_up_x_forwarded_host"] = normalized["header_up_x_forwarded_host"].map(_normalize_text)
    normalized["header_up_x_forwarded_port"] = normalized["header_up_x_forwarded_port"].map(_normalize_text)
    normalized["header_up_x_forwarded_proto"] = normalized["header_up_x_forwarded_proto"].map(_normalize_text)
    normalized["header_up_x_real_ip"] = normalized["header_up_x_real_ip"].map(_normalize_text)
    normalized["path_routes"] = normalized["path_routes"].map(_normalize_text)
    normalized["enabled"] = normalized["enabled"].map(lambda value: _parse_bool(value, default=True))
    normalized["upstream_port"] = normalized["upstream_port"].map(_parse_port)

    return normalized


def validate_dataframe(df: pd.DataFrame) -> list[ValidationError]:
    errors: list[ValidationError] = []

    for column in df.attrs.get("unsupported_columns", []):
        errors.append(ValidationError(None, column, "Unsupported column."))

    for column in df.attrs.get("duplicate_columns", []):
        errors.append(ValidationError(None, column, "Duplicate column name."))

    for column in df.attrs.get("headerless_columns", []):
        errors.append(
            ValidationError(None, f"column {column}", "Has data but no header. Add a header or clear the column.")
        )

    if df.empty:
        errors.append(ValidationError(None, "csv", "CSV contains no data rows."))
        return errors

    for _, row in df.iterrows():
        row_number = int(row[INTERNAL_ROW_COLUMN])
        host = row.get("host")
        upstream = row.get("upstream")
        upstream_host = row.get("upstream_host")
        tls_mode = row.get("tls_mode")
        enabled = row.get("enabled")
        skip_verify = row.get("skip_verify")
        redirect = row.get("redirect")
        upstream_port = row.get("upstream_port")
        upstream_scheme = row.get("upstream_scheme")

        if not host:
            errors.append(ValidationError(row_number, "host", "Host is required."))

        if not upstream and not upstream_host:
            errors.append(
                ValidationError(
                    row_number,
                    "upstream",
                    "Either upstream or upstream_host is required.",
                )
            )

        if tls_mode not in TLS_MODES:
            errors.append(
                ValidationError(
                    row_number,
                    "tls_mode",
                    "tls_mode must be one of internal, public, or off.",
                )
            )

        if not isinstance(skip_verify, bool):
            errors.append(
                ValidationError(row_number, "skip_verify", "skip_verify must be a boolean.")
            )

        if not isinstance(redirect, bool):
            errors.append(
                ValidationError(row_number, "redirect", "redirect must be a boolean.")
            )

        if not isinstance(enabled, bool):
            errors.append(ValidationError(row_number, "enabled", "enabled must be a boolean."))

        if upstream_port is not None and not isinstance(upstream_port, int):
            errors.append(
                ValidationError(
                    row_number,
                    "upstream_port",
                    "upstream_port must be numeric when provided.",
                )
            )

        if upstream_scheme not in UPSTREAM_SCHEMES:
            errors.append(
                ValidationError(
                    row_number,
                    "upstream_scheme",
                    "upstream_scheme must be http or https.",
                )
            )

        for route_path, route_upstream in _parse_path_routes(row.get("path_routes")):
            if (
                not route_path
                or not ROUTE_PATH_RE.fullmatch(route_path)
                or not route_upstream
                or not _is_valid_upstream_url(route_upstream)
            ):
                errors.append(
                    ValidationError(
                        row_number,
                        "path_routes",
                        "path_routes entries must look like /streaming=http://192.168.1.30:3067.",
                    )
                )

        if upstream:
            if not _is_valid_upstream_url(upstream):
                errors.append(
                    ValidationError(row_number, "upstream", "upstream is malformed.")
                )
        elif upstream_host:
            if "://" in upstream_host:
                errors.append(
                    ValidationError(
                        row_number,
                        "upstream_host",
                        "upstream_host should not include a URL scheme.",
                    )
                )
            else:
                generated_upstream = build_upstream_url(row)
                if not _is_valid_upstream_url(generated_upstream):
                    errors.append(
                        ValidationError(
                            row_number,
                            "upstream_host",
                            "upstream_host/upstream_scheme/upstream_port produce a malformed upstream URL.",
                        )
                    )

    errors.extend(_validate_site_routes(df))
    return errors


def _validate_site_routes(df: pd.DataFrame) -> list[ValidationError]:
    errors: list[ValidationError] = []
    seen_sites: set[tuple[str, bool]] = set()

    for _, row in df.iterrows():
        if row.get("enabled") is not True or not row.get("host"):
            continue

        row_number = int(row[INTERNAL_ROW_COLUMN])
        host = row["host"]
        site_key = _site_key(row)

        if site_key in seen_sites:
            errors.append(
                ValidationError(
                    row_number,
                    "host",
                    f"Host {host} already has a row. Add this route to its path_routes or disable this row.",
                )
            )
        seen_sites.add(site_key)

        seen_paths: set[str | None] = set()
        for path, _ in _parse_path_routes(row.get("path_routes")):
            if path and path in seen_paths:
                errors.append(
                    ValidationError(row_number, "path_routes", f"Host {host} already has a route for {path}.")
                )
            seen_paths.add(path)

    return errors


def prepare_dataframe(df: pd.DataFrame) -> PreparedTranslation:
    normalized_df = normalize_dataframe(df)
    errors = validate_dataframe(normalized_df)
    if errors:
        raise CSVValidationException(errors)

    active_df = normalized_df[normalized_df["enabled"] == True].copy()  # noqa: E712
    skipped_row_count = int((normalized_df["enabled"] == False).sum())  # noqa: E712
    warnings: list[str] = []
    if skipped_row_count:
        warnings.append(f"Skipped {skipped_row_count} disabled row(s).")
    if normalized_df.attrs["ignored_column_count"]:
        warnings.append(f"Ignored {normalized_df.attrs['ignored_column_count']} empty column(s) with no header.")
    if normalized_df.attrs["ignored_row_count"]:
        warnings.append(f"Ignored {normalized_df.attrs['ignored_row_count']} empty row(s).")
    if active_df.empty:
        raise CSVError("Empty output after filtering disabled rows.")

    return PreparedTranslation(
        normalized_df=normalized_df,
        active_df=active_df,
        skipped_row_count=skipped_row_count,
        warnings=warnings,
    )


def render_caddyfile(df: pd.DataFrame) -> str:
    blocks = [_render_site(row) for _, row in df.iterrows()]
    return "\n\n".join(blocks).strip() + "\n"


def _site_key(row: pd.Series) -> tuple[str, bool]:
    return str(row["host"]).lower(), row["tls_mode"] == "off"


def _parse_path_routes(value: Any) -> list[tuple[str | None, str | None]]:
    entries = (entry.partition("=") for entry in (_normalize_text(value) or "").split())
    return [(_normalize_route_path(path), _normalize_address(upstream)) for path, _, upstream in entries]


def _render_site(row: pd.Series) -> str:
    host = str(row["host"])
    tls_mode = str(row["tls_mode"])
    site_label = f"http://{host}" if tls_mode == "off" else host
    tls_lines = ["tls internal"] if tls_mode == "internal" else []

    path_routes = _parse_path_routes(row.get("path_routes"))
    if path_routes:
        sections = [tls_lines] if tls_lines else []
        sections.append([f"redir {path} {path}/ 308" for path, _ in path_routes])
        for path, upstream in path_routes:
            sections.append(_handle_section(f"handle {path}/* {{", [f"reverse_proxy {upstream}"]))
        sections.append(_handle_section("handle {", _build_route_lines(row)))
        body = [line for index, section in enumerate(sections) for line in ([""] if index else []) + section]
    else:
        body = tls_lines + _build_route_lines(row)

    indented = [f"    {line}" if line else "" for line in body]
    return "\n".join([f"{site_label} {{", *indented, "}"])


def _handle_section(opening_line: str, route_lines: list[str]) -> list[str]:
    return [opening_line, *(f"    {line}" for line in route_lines), "}"]


def _build_route_lines(row: pd.Series) -> list[str]:
    upstream_url = build_upstream_url(row)
    skip_verify = bool(row["skip_verify"])
    transport_versions = _normalize_text(row.get("transport_versions"))
    header_up_lines = _build_header_up_lines(row)
    parsed_upstream = urlparse(upstream_url)
    has_transport_block = bool(transport_versions) or (parsed_upstream.scheme == "https" and skip_verify)

    if bool(row["redirect"]):
        return [f"redir {upstream_url}{{uri}} permanent"]
    if not has_transport_block and not header_up_lines:
        return [f"reverse_proxy {upstream_url}"]

    lines = [f"reverse_proxy {upstream_url} {{"]
    if has_transport_block:
        lines.append("    transport http {")
        if transport_versions:
            lines.append(f"        versions {transport_versions}")
        if parsed_upstream.scheme == "https" and skip_verify:
            lines.append("        tls_insecure_skip_verify")
        lines.append("    }")
    lines.extend(f"    {header_up_line}" for header_up_line in header_up_lines)
    lines.append("}")
    return lines


def build_upstream_url(row: pd.Series) -> str:
    upstream = row.get("upstream")
    if upstream:
        return str(upstream)

    scheme = str(row.get("upstream_scheme") or "http")
    upstream_host = str(row.get("upstream_host") or "")
    upstream_port = row.get("upstream_port")
    if upstream_port is not None and not _host_has_port(upstream_host):
        return f"{scheme}://{upstream_host}:{upstream_port}"
    return f"{scheme}://{upstream_host}"


def _build_header_up_lines(row: pd.Series) -> list[str]:
    header_columns = (
        ("header_up", "Host"),
        ("header_up_origin", "Origin"),
        ("header_up_x_forwarded_host", "X-Forwarded-Host"),
        ("header_up_x_forwarded_port", "X-Forwarded-Port"),
        ("header_up_x_forwarded_proto", "X-Forwarded-Proto"),
        ("header_up_x_real_ip", "X-Real-IP"),
    )
    lines: list[str] = []
    removed_headers = _normalize_text(row.get("header_up_remove"))
    if removed_headers:
        lines.extend(f"header_up -{header_name}" for header_name in removed_headers.split())
    for column_name, header_name in header_columns:
        value = _normalize_text(row.get(column_name))
        if value:
            lines.append(f"header_up {header_name} {value}")
    return lines


def _column_letter(position: int) -> str:
    letters = ""
    while position:
        position, remainder = divmod(position - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _normalize_cell(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _normalize_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() or None


def _normalize_address(value: Any) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    return text.rstrip("/") or None


def _normalize_route_path(value: Any) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    return text.removesuffix("/*").rstrip("/") or "/"


def _normalize_scheme(value: Any) -> str:
    text = _normalize_text(value)
    return (text or "http").lower()


def _normalize_tls_mode(value: Any) -> str:
    text = _normalize_text(value)
    return (text or "internal").lower()


def _parse_bool(value: Any, default: bool) -> bool | Any:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(int(value))

    text = str(value).strip().lower()
    if text in BOOLEAN_TRUE:
        return True
    if text in BOOLEAN_FALSE:
        return False
    return value


def _parse_port(value: Any) -> int | Any | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)

    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return value


def _host_has_port(host: str) -> bool:
    parsed = urlparse(f"//{host}")
    try:
        return parsed.port is not None
    except ValueError:
        return False


def _is_valid_upstream_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in UPSTREAM_SCHEMES:
        return False
    if not parsed.netloc:
        return False
    try:
        _ = parsed.port
    except ValueError:
        return False
    return True
