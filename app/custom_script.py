from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.settings import Settings, get_settings


CONFIG_FILENAME = "custom-script.json"
LEGACY_WORKSPACE_NAME = "new"


@dataclass
class CustomScriptConfig:
    command: str = ""
    uploaded_script_name: str | None = None

    @property
    def has_command(self) -> bool:
        return bool(self.command.strip())


@dataclass
class ScriptExecutionResult:
    attempted: bool
    command: str = ""
    working_directory: str = ""
    succeeded: bool = False
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None


def get_workspace_dir(settings: Settings | None = None) -> Path:
    app_settings = settings or get_settings()
    workspace_dir = app_settings.script_dir
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


def load_config(settings: Settings | None = None) -> CustomScriptConfig:
    _migrate_legacy_workspace_if_needed(settings=settings)
    config_path = _get_config_path(settings=settings)
    if not config_path.exists():
        return CustomScriptConfig()

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return CustomScriptConfig()

    return CustomScriptConfig(
        command=str(payload.get("command", "") or ""),
        uploaded_script_name=_normalize_filename(payload.get("uploaded_script_name")),
    )


def save_config(
    command: str,
    uploaded_script: BinaryIO | None = None,
    uploaded_script_name: str | None = None,
    settings: Settings | None = None,
) -> CustomScriptConfig:
    _migrate_legacy_workspace_if_needed(settings=settings)
    config = load_config(settings=settings)
    workspace_dir = get_workspace_dir(settings=settings)

    normalized_command = (command or "").strip()
    script_name = config.uploaded_script_name
    if uploaded_script is not None and uploaded_script_name:
        script_name = _save_uploaded_script(
            uploaded_script=uploaded_script,
            uploaded_script_name=uploaded_script_name,
            workspace_dir=workspace_dir,
        )

    saved = CustomScriptConfig(command=normalized_command, uploaded_script_name=script_name)
    _get_config_path(settings=settings).write_text(
        json.dumps(
            {
                "command": saved.command,
                "uploaded_script_name": saved.uploaded_script_name,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return saved


def sync_generated_file_into_workspace(
    generated_file_path: str | Path,
    settings: Settings | None = None,
) -> str:
    _migrate_legacy_workspace_if_needed(settings=settings)
    source_path = Path(generated_file_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Generated file not found: {source_path}")

    workspace_dir = get_workspace_dir(settings=settings)
    target_path = workspace_dir / source_path.name
    shutil.copy2(source_path, target_path)
    return str(target_path)


def run_saved_command(settings: Settings | None = None) -> ScriptExecutionResult:
    _migrate_legacy_workspace_if_needed(settings=settings)
    config = load_config(settings=settings)
    workspace_dir = get_workspace_dir(settings=settings)

    if not config.has_command:
        return ScriptExecutionResult(
            attempted=False,
            command="",
            working_directory=str(workspace_dir),
            error_message="No saved custom command is configured.",
        )

    try:
        completed = subprocess.run(
            config.command,
            cwd=str(workspace_dir),
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return ScriptExecutionResult(
            attempted=True,
            command=config.command,
            working_directory=str(workspace_dir),
            succeeded=False,
            error_message=str(exc),
        )

    return ScriptExecutionResult(
        attempted=True,
        command=config.command,
        working_directory=str(workspace_dir),
        succeeded=completed.returncode == 0,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _get_config_path(settings: Settings | None = None) -> Path:
    return get_workspace_dir(settings=settings) / CONFIG_FILENAME


def _get_legacy_workspace_dir(settings: Settings | None = None) -> Path:
    app_settings = settings or get_settings()
    return app_settings.output_dir / LEGACY_WORKSPACE_NAME


def _migrate_legacy_workspace_if_needed(settings: Settings | None = None) -> None:
    workspace_dir = get_workspace_dir(settings=settings)
    legacy_dir = _get_legacy_workspace_dir(settings=settings)
    if workspace_dir == legacy_dir or not legacy_dir.exists():
        return

    config_path = workspace_dir / CONFIG_FILENAME
    if config_path.exists():
        return

    for path in legacy_dir.iterdir():
        target_path = workspace_dir / path.name
        if target_path.exists():
            continue
        if path.is_dir():
            shutil.copytree(path, target_path)
        else:
            shutil.copy2(path, target_path)


def _save_uploaded_script(
    uploaded_script: BinaryIO,
    uploaded_script_name: str,
    workspace_dir: Path,
) -> str:
    filename = _normalize_filename(uploaded_script_name)
    if not filename:
        raise ValueError("Uploaded script file must have a name.")

    target_path = workspace_dir / filename
    if hasattr(uploaded_script, "seek"):
        uploaded_script.seek(0)
    with target_path.open("wb") as handle:
        shutil.copyfileobj(uploaded_script, handle)

    _make_executable(target_path)
    return filename


def _make_executable(path: Path) -> None:
    try:
        current_mode = path.stat().st_mode
        path.chmod(current_mode | os.stat(path).st_mode | 0o755)
    except OSError:
        pass


def _normalize_filename(value: object) -> str | None:
    if value is None:
        return None
    name = Path(str(value)).name.strip()
    return name or None
