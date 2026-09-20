from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import custom_script, deploy, saved_source, translator, workflows
from app.models import UrlTranslateRequest
from app.presentation import render_error, render_success, templates, wants_json
from app.settings import Settings, get_settings


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

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
            "saved_url": saved_source.load_url(settings),
            "source_saved": request.query_params.get("source_saved") == "1",
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
        result = workflows.translate_dataframe(
            dataframe=dataframe,
            source_type="upload",
            source_name=csv_file.filename or "upload.csv",
            preview_only=preview_only,
            run_custom_script=run_custom_script,
            settings=settings,
        )
        return render_success(request, result)
    except Exception as exc:
        return render_error(request, exc)


@app.post("/translate/url")
async def translate_url(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        payload = await _parse_url_payload(request)
        result = workflows.translate_url(payload, settings)
        return render_success(request, result)
    except Exception as exc:
        return render_error(request, exc)


@app.post("/source/save")
async def save_source(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    try:
        payload = await _parse_url_payload(request)
        url = saved_source.save_url(payload.url, settings)
        if wants_json(request):
            return JSONResponse(content={"status": "ok", "url": url})
        return RedirectResponse(url="/?source_saved=1", status_code=303)
    except Exception as exc:
        return render_error(request, exc)


@app.post("/update")
async def update_saved(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    try:
        return render_success(request, workflows.update_saved(settings))
    except Exception as exc:
        return render_error(request, exc)


@app.post("/translate/saved")
async def translate_saved(
    request: Request,
    preview_only: bool = Form(False),
    run_custom_script: bool = Form(False),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        result = workflows.translate_saved(
            settings, preview_only=preview_only, run_custom_script=run_custom_script,
        )
        return render_success(request, result)
    except Exception as exc:
        return render_error(request, exc)


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


def _read_sample_csv() -> str:
    sample_path = PROJECT_ROOT / "sample" / "sample.csv"
    if not sample_path.exists():
        return ""
    return sample_path.read_text(encoding="utf-8")


def _read_latest_preview(settings: Settings) -> str | None:
    try:
        _, text = deploy.read_generated_file(settings=settings)
        return text
    except FileNotFoundError:
        return None
