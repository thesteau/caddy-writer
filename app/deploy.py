from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.models import CommandResult, DeployResult
from app.settings import Settings, get_settings


def write_generated_file(text: str, settings: Settings | None = None) -> str:
    app_settings = settings or get_settings()
    app_settings.ensure_directories()
    output_path = app_settings.output_dir / "Caddyfile.generated"
    output_path.write_text(text, encoding="utf-8")
    return str(output_path)


def copy_to_target(src: str | Path, dst: str | Path) -> CommandResult:
    source_path = Path(src)
    target_path = Path(dst)

    if not source_path.exists():
        return CommandResult(
            command=f"copy {source_path} {target_path}",
            attempted=True,
            succeeded=False,
            stderr="Generated source file does not exist.",
        )

    if not target_path.exists():
        return CommandResult(
            command=f"copy {source_path} {target_path}",
            attempted=True,
            succeeded=False,
            stderr="Deploy target missing.",
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_path = target_path.with_name(f"{target_path.name}.{timestamp}.bak")
    shutil.copy2(target_path, backup_path)
    shutil.copy2(source_path, target_path)

    return CommandResult(
        command=f"copy {source_path} {target_path}",
        attempted=True,
        succeeded=True,
        stdout=f"Backed up {target_path} to {backup_path} and copied {source_path} into place.",
        details={"backup_path": str(backup_path)},
    )


def validate_caddy(settings: Settings | None = None) -> CommandResult:
    app_settings = settings or get_settings()
    return _run_command(
        [
            "docker",
            "exec",
            app_settings.caddy_container_name,
            "caddy",
            "validate",
            "--config",
            app_settings.caddy_container_config_path,
            "--adapter",
            "caddyfile",
        ]
    )


def reload_caddy(settings: Settings | None = None) -> CommandResult:
    app_settings = settings or get_settings()
    return _run_command(
        [
            "docker",
            "exec",
            app_settings.caddy_container_name,
            "caddy",
            "reload",
            "--config",
            app_settings.caddy_container_config_path,
            "--adapter",
            "caddyfile",
        ]
    )


def ensure_caddy_container_running(settings: Settings | None = None) -> CommandResult:
    app_settings = settings or get_settings()

    if not app_settings.docker_socket_enabled:
        return CommandResult(
            command="docker inspect",
            attempted=False,
            succeeded=False,
            skipped_reason="Docker socket support is disabled.",
            stderr="Docker socket unavailable.",
        )

    inspect_result = _run_command(
        [
            "docker",
            "inspect",
            app_settings.caddy_container_name,
            "--format",
            "{{.State.Running}}",
        ]
    )
    if not inspect_result.succeeded:
        inspect_result.stderr = inspect_result.stderr or "Caddy container not running."
        return inspect_result

    if inspect_result.stdout.strip().lower() != "true":
        inspect_result.succeeded = False
        inspect_result.stderr = inspect_result.stderr or "Caddy container not running."

    return inspect_result


def deploy_generated_file(path: str | Path, settings: Settings | None = None) -> DeployResult:
    app_settings = settings or get_settings()
    source_path = Path(path)
    target_path = app_settings.caddy_target_file
    result = DeployResult(
        attempted=True,
        source_path=str(source_path),
        target_path=str(target_path),
        message="Deployment attempted.",
    )

    if not source_path.exists():
        result.message = "Latest generated file does not exist."
        result.copy_result = CommandResult(
            command="copy",
            attempted=False,
            succeeded=False,
            stderr=result.message,
        )
        return result

    if not target_path.exists():
        result.message = "Configured target path does not exist."
        result.copy_result = CommandResult(
            command=f"copy {source_path} {target_path}",
            attempted=False,
            succeeded=False,
            stderr="Deploy target missing.",
        )
        return result

    availability_result = ensure_caddy_container_running(app_settings)
    result.availability_result = availability_result
    if not availability_result.succeeded:
        result.message = "Caddy container is not available."
        return result

    copy_result = copy_to_target(source_path, target_path)
    result.copy_result = copy_result
    result.copied = copy_result.succeeded
    if copy_result.succeeded:
        result.backup_path = copy_result.details.get("backup_path")
    else:
        result.message = "Copy to target failed."
        return result

    if not app_settings.caddy_validate_and_reload:
        result.succeeded = True
        result.message = "Copied generated file to the target path. Validation and reload are disabled."
        return result

    validate_result = validate_caddy(app_settings)
    result.validate_result = validate_result
    if not validate_result.succeeded:
        result.message = "Caddy validation failed; reload was skipped."
        return result

    reload_result = reload_caddy(app_settings)
    result.reload_result = reload_result
    if not reload_result.succeeded:
        result.message = "Caddy reload failed."
        return result

    result.succeeded = True
    result.message = "Deployment, validation, and reload succeeded."
    return result


def _run_command(command: list[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            command=" ".join(command),
            attempted=True,
            succeeded=False,
            stderr=f"Command unavailable: {exc}",
        )
    except Exception as exc:
        return CommandResult(
            command=" ".join(command),
            attempted=True,
            succeeded=False,
            stderr=str(exc),
        )

    return CommandResult(
        command=" ".join(command),
        attempted=True,
        succeeded=completed.returncode == 0,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        returncode=completed.returncode,
    )
