"""Persistent source URL, independent of translation and deployment."""
from __future__ import annotations

import json
import os
from tempfile import NamedTemporaryFile

from app.settings import Settings
from app.translator import normalize_csv_url


def load_url(settings: Settings) -> str | None:
    path = settings.output_dir / "saved-source.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        url = payload["url"]
        if not isinstance(url, str):
            return None
        normalize_csv_url(url)
        return url
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


def save_url(url: str, settings: Settings) -> str:
    url = url.strip()
    normalize_csv_url(url)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    # Replace atomically so readers never see a partially written setting.
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=settings.output_dir,
                            suffix=".tmp", delete=False) as handle:
        temporary_path = handle.name
        try:
            json.dump({"url": url}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            os.unlink(temporary_path)
            raise
    try:
        os.replace(temporary_path, settings.output_dir / "saved-source.json")
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return url
