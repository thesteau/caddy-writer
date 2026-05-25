from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError as PydanticValidationError

from app import custom_script, deploy, translator
from app.models import ScriptExecutionResponse, TranslationResponse, UrlTranslateRequest
from app.settings import Settings, get_settings


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="caddy-writer")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, settings: Settings = Depends(get_settings)) -> HTMLResponse:
    saved_script = custom_script.load_config(settings=settings)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "sample_csv": _read_sample_csv(),
            "latest_preview": _read_latest_preview(settings),
            "saved_script": saved_script,
            "settings": settings,
            "script_saved": request.query_params.get("script_saved") == "1",
        },
    )


@app.post("/translate/upload")
async def translate_upload(
    request: Request,
    csv_file: UploadFile = File(...),
    preview_only: bool = Form(False),
    run_custom_script: bool = Form(False),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        dataframe = translator.parse_csv_upload(csv_file.file)
        result = _build_translation_response(
            dataframe=dataframe,
            source_type="upload",
            source_name=csv_file.filename or "upload.csv",
            preview_only=preview_only,
            run_custom_script=run_custom_script,
            settings=settings,
        )
        return _render_success(request, result)
    except Exception as exc:
        return _render_error_response(request, exc)


@app.post("/translate/url")
async def translate_url(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        payload = await _parse_url_payload(request)
        if not settings.allow_url_fetch:
            raise translator.CSVError("URL fetching is disabled by configuration.")

        dataframe = translator.parse_csv_url(payload.url)
        result = _build_translation_response(
            dataframe=dataframe,
            source_type="url",
            source_name=payload.url,
            preview_only=payload.preview_only,
            run_custom_script=payload.run_custom_script,
            settings=settings,
        )
        return _render_success(request, result)
    except Exception as exc:
        return _render_error_response(request, exc)


@app.post("/custom-script/save")
async def save_custom_script(
    command: str = Form(""),
    script_file: UploadFile | None = File(None),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    file_obj = script_file.file if script_file is not None else None
    file_name = script_file.filename if script_file is not None else None
    custom_script.save_config(
        command=command,
        uploaded_script=file_obj,
        uploaded_script_name=file_name,
        settings=settings,
    )
    return RedirectResponse(url="/?script_saved=1", status_code=303)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/preview/latest")
async def preview_latest(settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    preview = _read_latest_preview(settings)
    if preview is None:
        return PlainTextResponse("No generated Caddyfile is available yet.", status_code=404)
    return PlainTextResponse(preview)


@app.post("/deploy/latest")
async def deploy_latest(settings: Settings = Depends(get_settings)) -> JSONResponse:
    try:
        generated_file_path, generated_text = deploy.read_generated_file(settings=settings)
        caddy_generated_file_path = deploy.copy_generated_file_to_caddy_dir(
            generated_file_path,
            settings=settings,
        )
    except FileNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "error": str(exc)},
        )
    except NotADirectoryError as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc)},
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "generated_file_path": generated_file_path,
            "generated_text": generated_text,
            "caddy_generated_file_path": caddy_generated_file_path,
            "message": "Copied the latest generated file into the mounted Caddy directory.",
        },
    )


def _build_translation_response(
    dataframe,
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


async def _parse_url_payload(request: Request) -> UrlTranslateRequest:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        return UrlTranslateRequest.model_validate(body)

    form = await request.form()
    payload = {
        "url": form.get("url", ""),
        "preview_only": form.get("preview_only", False),
        "run_custom_script": form.get("run_custom_script", False),
    }
    return UrlTranslateRequest.model_validate(payload)


def _render_success(request: Request, result: TranslationResponse) -> Response:
    if _wants_json(request):
        return JSONResponse(content=result.model_dump(mode="json"))

    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "result": result,
            "error_message": None,
            "error_details": [],
        },
    )


def _render_error_response(request: Request, exc: Exception) -> Response:
    status_code = 400
    details: list[str] = []

    if isinstance(exc, translator.CSVValidationException):
        details = [_format_validation_error(error) for error in exc.errors]
        message = "CSV validation failed."
    elif isinstance(exc, translator.CSVError):
        message = str(exc)
    elif isinstance(exc, PydanticValidationError):
        message = "Invalid request payload."
        details = [item["msg"] for item in exc.errors()]
    else:
        message = f"Unexpected error: {exc}"
        status_code = 500

    if _wants_json(request):
        return JSONResponse(
            status_code=status_code,
            content={"status": "error", "error": message, "details": details},
        )

    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "result": None,
            "error_message": message,
            "error_details": details,
        },
        status_code=status_code,
    )


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")
    return "application/json" in accept or "application/json" in content_type


def _format_validation_error(error: translator.ValidationError) -> str:
    if error.row_number is None:
        return f"{error.column}: {error.message}"
    return f"Row {error.row_number} [{error.column}]: {error.message}"


def _read_sample_csv() -> str:
    sample_path = PROJECT_ROOT / "sample" / "sample.csv"
    if not sample_path.exists():
        return ""
    return sample_path.read_text(encoding="utf-8")


def _read_latest_preview(settings: Settings) -> str | None:
    latest_path = settings.output_dir / "Caddyfile.generated"
    if not latest_path.exists():
        return None
    return latest_path.read_text(encoding="utf-8")
