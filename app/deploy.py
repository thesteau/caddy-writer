from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, DockerException, NotFound

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


def cleanup_backup(path: str | Path) -> CommandResult:
    backup_path = Path(path)
    if not backup_path.exists():
        return CommandResult(
            command=f"remove {backup_path}",
            attempted=True,
            succeeded=False,
            stderr="Backup file does not exist.",
        )

    backup_path.unlink()
    return CommandResult(
        command=f"remove {backup_path}",
        attempted=True,
        succeeded=True,
        stdout=f"Removed backup file {backup_path}.",
    )


def validate_caddy(settings: Settings | None = None) -> CommandResult:
    app_settings = settings or get_settings()
    return _run_caddy_exec(
        app_settings,
        [
            "caddy",
            "validate",
            "--config",
            app_settings.caddy_container_config_path,
            "--adapter",
            "caddyfile",
        ],
    )


def reload_caddy(settings: Settings | None = None) -> CommandResult:
    app_settings = settings or get_settings()
    return _run_caddy_exec(
        app_settings,
        [
            "caddy",
            "reload",
            "--config",
            app_settings.caddy_container_config_path,
            "--adapter",
            "caddyfile",
        ],
    )


def ensure_caddy_container_running(settings: Settings | None = None) -> CommandResult:
    app_settings = settings or get_settings()

    if not app_settings.docker_socket_enabled:
        return CommandResult(
            command=f"docker inspect {app_settings.caddy_container_name}",
            attempted=False,
            succeeded=False,
            skipped_reason="Docker socket support is disabled.",
            stderr="Docker socket unavailable.",
        )

    command = f"docker inspect {app_settings.caddy_container_name}"
    try:
        client = docker.from_env()
        container = client.containers.get(app_settings.caddy_container_name)
        container.reload()
        running = bool(container.attrs.get("State", {}).get("Running"))
        status = str(container.attrs.get("State", {}).get("Status", "unknown"))
        stdout = f"running={str(running).lower()} status={status}"
        return CommandResult(
            command=command,
            attempted=True,
            succeeded=running,
            stdout=stdout,
            stderr="" if running else "Caddy container not running.",
        )
    except NotFound:
        return CommandResult(
            command=command,
            attempted=True,
            succeeded=False,
            stderr="Caddy container not found.",
        )
    except DockerException as exc:
        return CommandResult(
            command=command,
            attempted=True,
            succeeded=False,
            stderr=f"Docker SDK error: {exc}",
        )
    finally:
        _close_client(locals().get("client"))


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

    if result.backup_path:
        cleanup_result = cleanup_backup(result.backup_path)
        if cleanup_result.succeeded:
            result.backup_path = None
            if reload_result.stdout:
                reload_result.stdout = f"{reload_result.stdout}\n{cleanup_result.stdout}"
            else:
                reload_result.stdout = cleanup_result.stdout
        else:
            if reload_result.stderr:
                reload_result.stderr = f"{reload_result.stderr}\n{cleanup_result.stderr}"
            else:
                reload_result.stderr = cleanup_result.stderr

    result.succeeded = True
    result.message = "Deployment, validation, and reload succeeded."
    return result

def _run_caddy_exec(settings: Settings, command: list[str]) -> CommandResult:
    if not settings.docker_socket_enabled:
        return CommandResult(
            command=_format_exec_command(settings.caddy_container_name, command),
            attempted=False,
            succeeded=False,
            skipped_reason="Docker socket support is disabled.",
            stderr="Docker socket unavailable.",
        )

    full_command = _format_exec_command(settings.caddy_container_name, command)
    try:
        client = docker.from_env()
        container = client.containers.get(settings.caddy_container_name)
        exec_result = container.exec_run(command, demux=True)
        stdout_bytes, stderr_bytes = _normalize_exec_output(exec_result.output)
        stdout = _decode_bytes(stdout_bytes)
        stderr = _decode_bytes(stderr_bytes)
        return CommandResult(
            command=full_command,
            attempted=True,
            succeeded=exec_result.exit_code == 0,
            stdout=stdout,
            stderr=stderr,
            returncode=exec_result.exit_code,
        )
    except NotFound:
        return CommandResult(
            command=full_command,
            attempted=True,
            succeeded=False,
            stderr="Caddy container not found.",
        )
    except (APIError, DockerException) as exc:
        return CommandResult(
            command=full_command,
            attempted=True,
            succeeded=False,
            stderr=f"Docker SDK error: {exc}",
        )
    finally:
        _close_client(locals().get("client"))


def _format_exec_command(container_name: str, command: list[str]) -> str:
    return f"docker exec {container_name} {' '.join(command)}"


def _normalize_exec_output(output: Any) -> tuple[bytes | None, bytes | None]:
    if isinstance(output, tuple):
        stdout_bytes, stderr_bytes = output
        return stdout_bytes, stderr_bytes
    return output, None


def _decode_bytes(value: bytes | None) -> str:
    if not value:
        return ""
    return value.decode("utf-8", errors="replace").strip()


def _close_client(client: Any) -> None:
    if client is None:
        return
    try:
        client.close()
    except Exception:
        return
