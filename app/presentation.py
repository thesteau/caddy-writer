"""HTML and JSON representations of workflow results."""
from json import JSONDecodeError
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError as PydanticValidationError

from app import translator
from app.models import TranslationResponse


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def render_success(request: Request, result: TranslationResponse) -> Response:
    if wants_json(request):
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


def render_error(request: Request, exc: Exception) -> Response:
    status_code = 400
    details: list[str] = []

    if isinstance(exc, translator.CSVValidationException):
        details = [_format_validation_error(error) for error in exc.errors]
        message = "CSV validation failed."
    elif isinstance(exc, translator.CSVError):
        message = str(exc)
    elif isinstance(exc, JSONDecodeError):
        message = "Invalid JSON request body."
    elif isinstance(exc, PydanticValidationError):
        message = "Invalid request payload."
        details = [item["msg"] for item in exc.errors()]
    else:
        message = f"Unexpected error: {exc}"
        status_code = 500

    if wants_json(request):
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


def wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")
    return "application/json" in accept or "application/json" in content_type


def _format_validation_error(error: translator.ValidationError) -> str:
    if error.row_number is None:
        return f"{error.column}: {error.message}"
    return f"Row {error.row_number} [{error.column}]: {error.message}"
