from __future__ import annotations

from pydantic import BaseModel, Field


class TranslationResponse(BaseModel):
    status: str = "ok"
    source_type: str
    source_name: str
    parsed_row_count: int
    generated_row_count: int
    skipped_row_count: int
    warnings: list[str] = Field(default_factory=list)
    generated_file_path: str
    generated_text: str
    preview_only: bool = False
    copied_to_caddy_dir: bool = False
    caddy_generated_file_path: str | None = None
    caddy_copy_message: str = ""


class UrlTranslateRequest(BaseModel):
    url: str
    preview_only: bool = False
