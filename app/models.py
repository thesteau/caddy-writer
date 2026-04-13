from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CommandResult(BaseModel):
    command: str | None = None
    attempted: bool = False
    succeeded: bool = False
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    skipped_reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DeployResult(BaseModel):
    attempted: bool = False
    succeeded: bool = False
    copied: bool = False
    source_path: str | None = None
    target_path: str | None = None
    backup_path: str | None = None
    message: str = ""
    availability_result: CommandResult | None = None
    copy_result: CommandResult | None = None
    validate_result: CommandResult | None = None
    reload_result: CommandResult | None = None


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
    deploy_requested: bool = False
    deploy_attempted: bool = False
    preview_only: bool = False
    deploy_result: DeployResult | None = None


class UrlTranslateRequest(BaseModel):
    url: str
    deploy: bool = False
    preview_only: bool = False
