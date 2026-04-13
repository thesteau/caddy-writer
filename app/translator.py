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
    "notes",
    "enabled",
}
INTERNAL_ROW_COLUMN = "__row_number__"
BOOLEAN_TRUE = {"true", "1", "yes", "y", "on"}
BOOLEAN_FALSE = {"false", "0", "no", "n", "off", ""}
TLS_MODES = {"internal", "public", "off"}
UPSTREAM_SCHEMES = {"http", "https"}
GOOGLE_SHEETS_RE = re.compile(r"https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)")


@dataclass
class ValidationError:
    row_number: int | None
    column: str
    message: str


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
        super().__init__("CSV validation failed.")
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
    if "docs.google.com" in parsed.netloc and "/spreadsheets/" in parsed.path:
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
    normalized = df.copy()
    normalized.columns = [str(column).strip().lower() for column in normalized.columns]
    unsupported_columns = [column for column in normalized.columns if column not in SUPPORTED_COLUMNS]
    duplicate_columns = list(pd.Index(normalized.columns)[pd.Index(normalized.columns).duplicated()])

    normalized.attrs["unsupported_columns"] = unsupported_columns
    normalized.attrs["duplicate_columns"] = duplicate_columns
    normalized[INTERNAL_ROW_COLUMN] = range(2, len(normalized) + 2)

    for column in normalized.columns:
        if column == INTERNAL_ROW_COLUMN:
            continue
        normalized[column] = normalized[column].map(_normalize_cell)

    for column in SUPPORTED_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None

    normalized["host"] = normalized["host"].map(_normalize_text)
    normalized["upstream"] = normalized["upstream"].map(_normalize_text)
    normalized["upstream_host"] = normalized["upstream_host"].map(_normalize_text)
    normalized["upstream_scheme"] = normalized["upstream_scheme"].map(_normalize_scheme)
    normalized["tls_mode"] = normalized["tls_mode"].map(_normalize_tls_mode)
    normalized["skip_verify"] = normalized["skip_verify"].map(lambda value: _parse_bool(value, default=False))
    normalized["enabled"] = normalized["enabled"].map(lambda value: _parse_bool(value, default=True))
    normalized["upstream_port"] = normalized["upstream_port"].map(_parse_port)

    return normalized


def validate_dataframe(df: pd.DataFrame) -> list[ValidationError]:
    errors: list[ValidationError] = []

    for column in df.attrs.get("unsupported_columns", []):
        errors.append(ValidationError(None, column, "Unsupported column."))

    for column in df.attrs.get("duplicate_columns", []):
        errors.append(ValidationError(None, column, "Duplicate column name."))

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
    if active_df.empty:
        raise CSVError("Empty output after filtering disabled rows.")

    return PreparedTranslation(
        normalized_df=normalized_df,
        active_df=active_df,
        skipped_row_count=skipped_row_count,
        warnings=warnings,
    )


def render_caddyfile(df: pd.DataFrame) -> str:
    blocks: list[str] = []
    for _, row in df.iterrows():
        host = str(row["host"])
        upstream_url = build_upstream_url(row)
        tls_mode = str(row["tls_mode"])
        skip_verify = bool(row["skip_verify"])
        parsed_upstream = urlparse(upstream_url)

        site_label = f"http://{host}" if tls_mode == "off" else host
        lines = [f"{site_label} {{"]

        if tls_mode == "internal":
            lines.append("    tls internal")

        if parsed_upstream.scheme == "https" and skip_verify:
            lines.append(f"    reverse_proxy {upstream_url} {{")
            lines.append("        transport http {")
            lines.append("            tls_insecure_skip_verify")
            lines.append("        }")
            lines.append("    }")
        else:
            lines.append(f"    reverse_proxy {upstream_url}")

        lines.append("}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks).strip() + "\n"


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


def _normalize_cell(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


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
