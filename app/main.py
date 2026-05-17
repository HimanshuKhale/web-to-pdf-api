from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.pdf_service import PDF_OUTPUT_DIR, convert_url_to_pdf, slugify_filename


if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


app = FastAPI(
    title="URL to PDF Converter API",
    description="Internal API for converting webpages into downloadable PDFs.",
    version="1.0.0",
)

templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


def pdf_file_response(path: Path) -> FileResponse:
    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/pdf",
    )


@app.post("/convert")
async def convert(
    url: str = Form(...),
    custom_name: str | None = Form(None),
    page_format: str = Form("A4"),
    margin: str = Form("12mm"),
    clean: str | None = Form(None),
    landscape: str | None = Form(None),
):
    """
    Convert URL and directly return downloadable PDF.
    """

    pdf_path = await convert_url_to_pdf(
        url=url,
        custom_name=custom_name,
        clean=clean == "true",
        margin=margin,
        page_format=page_format,
        landscape=landscape == "true",
    )

    return pdf_file_response(pdf_path)


@app.post("/api/convert")
async def api_convert(
    url: str = Form(...),
    custom_name: str | None = Form(None),
    page_format: str = Form("A4"),
    margin: str = Form("12mm"),
    clean: str | None = Form(None),
    landscape: str | None = Form(None),
):
    """
    Convert URL and return JSON with download URL.
    """

    pdf_path = await convert_url_to_pdf(
        url=url,
        custom_name=custom_name,
        clean=clean == "true",
        margin=margin,
        page_format=page_format,
        landscape=landscape == "true",
    )

    return JSONResponse(
        {
            "success": True,
            "filename": pdf_path.name,
            "download_url": f"/download/{pdf_path.name}",
        }
    )


@app.get("/download/{filename}")
async def download(filename: str):
    """
    Download a previously generated PDF.
    """

    safe_name = slugify_filename(filename.removesuffix(".pdf")) + ".pdf"

    if safe_name != filename or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    pdf_path = PDF_OUTPUT_DIR / filename

    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="PDF not found.")

    return pdf_file_response(pdf_path)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "webpage-to-pdf",
        "output_dir": str(PDF_OUTPUT_DIR),
    }