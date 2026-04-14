from __future__ import annotations

import shutil
from pathlib import Path

from app.settings import Settings, get_settings


def write_generated_file(text: str, settings: Settings | None = None) -> str:
    app_settings = settings or get_settings()
    app_settings.ensure_directories()
    output_path = app_settings.output_dir / "Caddyfile.generated"
    output_path.write_text(text, encoding="utf-8")
    return str(output_path)


def read_generated_file(settings: Settings | None = None) -> tuple[str, str]:
    app_settings = settings or get_settings()
    output_path = app_settings.output_dir / "Caddyfile.generated"
    if not output_path.exists():
        raise FileNotFoundError("No generated Caddyfile is available yet.")
    return str(output_path), output_path.read_text(encoding="utf-8")


def copy_generated_file_to_caddy_dir(
    source_path: str | Path,
    settings: Settings | None = None,
) -> str:
    app_settings = settings or get_settings()
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Generated file not found: {source}")

    target_dir = app_settings.caddy_output_dir
    if not target_dir.exists():
        raise FileNotFoundError(f"Caddy output directory does not exist: {target_dir}")
    if not target_dir.is_dir():
        raise NotADirectoryError(f"Caddy output path is not a directory: {target_dir}")

    target_path = target_dir / app_settings.caddy_output_filename
    shutil.copy2(source, target_path)
    return str(target_path)
