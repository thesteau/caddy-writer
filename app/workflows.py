"""Generation, deployment, and script workflows, independent of HTTP routes."""
from __future__ import annotations

import pandas as pd

from app import custom_script, deploy, saved_source, translator
from app.models import ScriptExecutionResponse, TranslationResponse, UrlTranslateRequest
from app.settings import Settings


def translate_saved(
    settings: Settings, *, preview_only: bool = False, run_custom_script: bool = False,
) -> TranslationResponse:
    url = saved_source.load_url(settings)
    if not url:
        raise translator.CSVError("No saved URL is available. Save a URL first.")
    return translate_url(UrlTranslateRequest(
        url=url, preview_only=preview_only, run_custom_script=run_custom_script,
    ), settings)


def update_saved(settings: Settings) -> TranslationResponse:
    return translate_saved(
        settings, run_custom_script=custom_script.load_config(settings=settings).has_command,
    )


def translate_url(payload: UrlTranslateRequest, settings: Settings) -> TranslationResponse:
    if not settings.allow_url_fetch:
        raise translator.CSVError("URL fetching is disabled by configuration.")
    return translate_dataframe(
        dataframe=translator.parse_csv_url(payload.url), source_type="url",
        source_name=payload.url, preview_only=payload.preview_only,
        run_custom_script=payload.run_custom_script, settings=settings,
    )


def translate_dataframe(
    dataframe: pd.DataFrame,
    source_type: str,
    source_name: str,
    preview_only: bool,
    run_custom_script: bool,
    settings: Settings,
) -> TranslationResponse:
    prepared = translator.prepare_dataframe(dataframe)
    generated_text = translator.render_caddyfile(prepared.active_df)
    generated_file_path = deploy.write_generated_file(generated_text, settings=settings)
    custom_script.sync_generated_file_into_workspace(generated_file_path, settings=settings)

    warnings = list(prepared.warnings)
    copied_to_caddy_dir = False
    caddy_generated_file_path = None
    caddy_copy_message = "Skipped copying into the mounted Caddy directory."
    if preview_only:
        warnings.append("Preview-only mode is enabled. The generated file was not copied into the Caddy directory.")
    else:
        try:
            caddy_generated_file_path = deploy.copy_generated_file_to_caddy_dir(
                generated_file_path,
                settings=settings,
            )
            copied_to_caddy_dir = True
            caddy_copy_message = "Copied the generated file into the mounted Caddy directory."
        except (FileNotFoundError, NotADirectoryError) as exc:
            warnings.append(str(exc))
            caddy_copy_message = "Could not copy the generated file into the mounted Caddy directory."

    script_execution: ScriptExecutionResponse | None = None
    if run_custom_script:
        execution = custom_script.run_saved_command(settings=settings)
        script_execution = ScriptExecutionResponse(
            attempted=execution.attempted,
            command=execution.command,
            working_directory=execution.working_directory,
            succeeded=execution.succeeded,
            exit_code=execution.exit_code,
            stdout=execution.stdout,
            stderr=execution.stderr,
            error_message=execution.error_message,
        )
        if execution.error_message:
            warnings.append(execution.error_message)
        elif execution.attempted and not execution.succeeded:
            warnings.append("Custom script command failed after translation.")

    return TranslationResponse(
        source_type=source_type,
        source_name=source_name,
        parsed_row_count=len(prepared.normalized_df.index),
        generated_row_count=len(prepared.active_df.index),
        skipped_row_count=prepared.skipped_row_count,
        warnings=warnings,
        generated_file_path=generated_file_path,
        generated_text=generated_text,
        preview_only=preview_only,
        copied_to_caddy_dir=copied_to_caddy_dir,
        caddy_generated_file_path=caddy_generated_file_path,
        caddy_copy_message=caddy_copy_message,
        script_execution=script_execution,
    )
