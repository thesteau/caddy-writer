from __future__ import annotations

import asyncio
import io
import shutil
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import custom_script, main, saved_source, scheduler, translator, workflows
from app.settings import Settings


@pytest.fixture
def settings() -> Settings:
    root = Path(".test-tmp") / uuid.uuid4().hex
    config = Settings(
        _env_file=None,
        output_dir=root / "output",
        script_dir=root / "scripts-data",
        temp_dir=root / "tmp",
        caddy_output_dir=root / "deploy-target",
    )
    config.ensure_directories()
    config.caddy_output_dir.mkdir(parents=True)
    try:
        yield config
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize("record", [
    None, "broken json", "{}", "null", '{"url": null}',
    '{"url": ""}', '{"url": "file:///tmp/source.csv"}',
    '{"url": "https://bad host/source.csv"}',
])
def test_sync_without_valid_saved_source_has_no_side_effects(settings, monkeypatch, record):
    if record is not None:
        (settings.output_dir / "saved-source.json").write_text(record)
    live = settings.caddy_output_dir / "Caddyfile"
    live.write_text("existing config")

    def forbidden(*args, **kwargs):
        pytest.fail("Sync must not fetch or run a command without a valid saved URL")

    monkeypatch.setattr(translator, "parse_csv_url", forbidden)
    monkeypatch.setattr(custom_script, "run_saved_command", forbidden)
    scheduler.sync_saved_source(settings)

    assert live.read_text() == "existing config"
    assert not (settings.output_dir / "Caddyfile.generated").exists()
    assert not (settings.script_dir / "Caddyfile.generated").exists()


def test_sync_respects_disabled_url_fetch(settings, monkeypatch):
    saved_source.save_url("https://example.com/saved.csv", settings)
    settings.allow_url_fetch = False

    def forbidden(*args, **kwargs):
        pytest.fail("Disabled URL fetching must prevent the update")

    monkeypatch.setattr(workflows, "update_saved", forbidden)
    scheduler.sync_saved_source(settings)


@pytest.mark.parametrize("has_command", [False, True])
def test_sync_fetches_current_saved_source_deploys_and_runs_configured_command(
    settings, monkeypatch, has_command,
):
    if has_command:
        command = (
            f'"{sys.executable}" -c "from pathlib import Path; '
            "Path('ran.txt').write_text(Path('Caddyfile.generated').read_text())\""
        )
        custom_script.save_config(command, settings=settings)
    fetched = []

    def fetch(url):
        fetched.append(url)
        return translator.parse_csv_upload(io.StringIO(
            f"host,upstream\nversion{len(fetched)}.home,http://localhost:8080\n",
        ))

    monkeypatch.setattr(translator, "parse_csv_url", fetch)
    for version, url in enumerate([
        "https://docs.google.com/spreadsheets/d/example/edit#gid=123",
        "https://example.com/replacement.csv",
    ], start=1):
        saved_source.save_url(url, settings)
        scheduler.sync_saved_source(settings)
        assert fetched[-1] == url
        generated = (settings.output_dir / "Caddyfile.generated").read_text()
        assert f"version{version}.home" in generated
        assert (settings.caddy_output_dir / "Caddyfile").read_text() == generated
        assert (settings.script_dir / "Caddyfile.generated").read_text() == generated
        marker = settings.script_dir / "ran.txt"
        assert marker.exists() == has_command
        if has_command:
            assert marker.read_text() == generated


def test_periodic_sync_waits_12_hours_and_recovers_from_failure(settings, monkeypatch, caplog):
    saved_source.save_url("https://example.com/saved.csv", settings)
    live = settings.caddy_output_dir / "Caddyfile"
    live.write_text("existing config")
    attempts = []
    waits = []

    def fetch(url):
        attempts.append(url)
        if len(attempts) == 1:
            raise translator.CSVError("Temporary fetch failure")
        return translator.parse_csv_upload(io.StringIO(
            "host,upstream\nrecovered.home,http://localhost:8080\n",
        ))

    async def advance_timer(waiter, timeout):
        waiter.close()
        waits.append(timeout)
        # No immediate update; the failed first tick leaves the live file alone.
        if len(waits) <= 2:
            assert live.read_text() == "existing config"
            assert len(attempts) == len(waits) - 1
            raise TimeoutError

    monkeypatch.setattr(translator, "parse_csv_url", fetch)
    monkeypatch.setattr(scheduler.asyncio, "wait_for", advance_timer)
    asyncio.run(scheduler.run_periodic_sync(settings, asyncio.Event()))

    assert waits == [43200, 43200, 43200]
    assert len(attempts) == 2
    assert "recovered.home" in live.read_text()
    assert "Temporary fetch failure" in caplog.text


def test_lifespan_starts_timer_and_stops_it_on_shutdown(settings, monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    saved_source.save_url("https://example.com/saved.csv", settings)

    def forbidden(*args, **kwargs):
        pytest.fail("Startup or shutdown must not trigger an immediate sync")

    monkeypatch.setattr(scheduler, "sync_saved_source", forbidden)
    tasks = []

    async def inspect():
        tasks.extend(task for task in asyncio.all_tasks()
                     if task.get_coro().__name__ == "run_periodic_sync")

    with TestClient(main.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        client.portal.call(inspect)
        assert len(tasks) == 1
        assert not tasks[0].done()
    assert tasks[0].done()
    assert not tasks[0].cancelled()


def test_overlapping_updates_keep_each_commands_generated_file(settings, monkeypatch):
    command_started = threading.Event()
    release_command = threading.Event()
    second_started = threading.Event()
    script_inputs = []

    def run_command(**kwargs):
        if not script_inputs:
            command_started.set()
            assert release_command.wait(timeout=5)
        script_inputs.append((settings.script_dir / "Caddyfile.generated").read_text())
        return custom_script.ScriptExecutionResult(attempted=True, succeeded=True)

    def translate(host):
        if host == "second.home":
            second_started.set()
        return workflows.translate_dataframe(
            translator.parse_csv_upload(io.StringIO(f"host,upstream\n{host},http://localhost:8080\n")),
            "upload", host, False, True, settings,
        )

    monkeypatch.setattr(custom_script, "run_saved_command", run_command)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(translate, "first.home")
        try:
            assert command_started.wait(timeout=5)
            second = pool.submit(translate, "second.home")
            assert second_started.wait(timeout=5)
            assert not second.done()
        finally:
            release_command.set()
        assert first.result(timeout=5).copied_to_caddy_dir
        assert second.result(timeout=5).copied_to_caddy_dir

    assert len(script_inputs) == 2
    assert "first.home" in script_inputs[0]
    assert "second.home" in script_inputs[1]
