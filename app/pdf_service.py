from __future__ import annotations

import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


BASE_DIR = Path(__file__).resolve().parent.parent
PDF_OUTPUT_DIR = BASE_DIR / "Session_4_pdf"

ALLOWED_FORMATS = {"A4", "Letter", "Legal"}
ALLOWED_MARGINS = {"0mm", "5mm", "10mm", "12mm", "20mm"}
DEFAULT_TIMEOUT_MS = 90_000


def slugify_filename(name: str, max_length: int = 100) -> str:
    """
    Return a Windows-safe filename stem.
    """

    cleaned = (name or "").strip().lower()

    # Remove invalid Windows filename characters and control characters.
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", cleaned)

    # Normalize whitespace and symbols.
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"[^a-z0-9._-]", "-", cleaned)
    cleaned = re.sub(r"[-_.]{2,}", "-", cleaned)
    cleaned = cleaned.strip(".-_ ")

    # Reserved Windows device names.
    reserved_names = {
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com2",
        "com3",
        "com4",
        "com5",
        "com6",
        "com7",
        "com8",
        "com9",
        "lpt1",
        "lpt2",
        "lpt3",
        "lpt4",
        "lpt5",
        "lpt6",
        "lpt7",
        "lpt8",
        "lpt9",
    }

    if not cleaned or cleaned in reserved_names:
        cleaned = "webpage"

    return cleaned[:max_length].rstrip(".-_ ") or "webpage"


def validate_url(url: str) -> str:
    """
    Validate and normalize URL.

    Examples:
    - example.com -> https://example.com
    - https://example.com -> https://example.com
    """

    value = (url or "").strip()

    if not value:
        raise HTTPException(status_code=400, detail="URL is required.")

    if "://" not in value:
        value = f"https://{value}"

    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=400,
            detail="Only http:// and https:// URLs are allowed.",
        )

    if not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid URL with a domain name.",
        )

    return value


def filename_from_url(url: str) -> str:
    """
    Build a fallback filename from the URL.
    """

    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "", 1)
    path = parsed.path.strip("/").replace("/", "-")

    base = f"{host}-{path}" if path else host

    return slugify_filename(base)


async def get_page_title(page) -> str | None:
    """
    Safely read page title.
    """

    try:
        title = (await page.title()).strip()
    except PlaywrightError:
        return None

    return title or None


async def auto_scroll_page(page) -> None:
    """
    Scroll through the page so lazy-loaded images/content render before PDF export.
    """

    try:
        await page.evaluate(
            """
            async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 700;

                    const timer = setInterval(() => {
                        const scrollHeight =
                            document.body.scrollHeight ||
                            document.documentElement.scrollHeight;

                        window.scrollBy(0, distance);
                        totalHeight += distance;

                        if (totalHeight >= scrollHeight) {
                            clearInterval(timer);
                            window.scrollTo(0, 0);
                            resolve();
                        }
                    }, 150);
                });
            }
            """
        )
    except PlaywrightError:
        # Scrolling is helpful but not mandatory.
        pass


async def clean_page_for_pdf(page) -> None:
    """
    Remove common popups, cookie banners, sticky bars, newsletter boxes, and obvious ads.
    """

    try:
        await page.evaluate(
            """
            () => {
                const selectors = [
                    '[id*="cookie" i]',
                    '[class*="cookie" i]',
                    '[aria-label*="cookie" i]',

                    '[id*="consent" i]',
                    '[class*="consent" i]',

                    '[id*="popup" i]',
                    '[class*="popup" i]',
                    '[class*="modal" i]',
                    '[role="dialog"]',

                    '[id*="newsletter" i]',
                    '[class*="newsletter" i]',

                    '[id*="subscribe" i]',
                    '[class*="subscribe" i]',

                    '[id*="advert" i]',
                    '[class*="advert" i]',
                    '[class*=" ad-" i]',
                    '[class^="ad-" i]',

                    'iframe[src*="ads" i]',
                    'iframe[src*="doubleclick" i]',

                    'header[style*="position: fixed" i]',
                    'header[style*="position: sticky" i]',
                    '[style*="position: fixed" i]',
                    '[style*="position: sticky" i]'
                ];

                for (const selector of selectors) {
                    document.querySelectorAll(selector).forEach((element) => {
                        try {
                            element.remove();
                        } catch (error) {}
                    });
                }

                document.body.style.overflow = 'visible';
                document.documentElement.style.overflow = 'visible';
            }
            """
        )
    except PlaywrightError:
        # Cleaning is optional.
        pass


async def load_page_safely(page, url: str) -> None:
    """
    Load page reliably.

    First try networkidle.
    If the website keeps background requests open forever,
    fallback to domcontentloaded.
    """

    try:
        await page.goto(
            url,
            wait_until="networkidle",
            timeout=DEFAULT_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=DEFAULT_TIMEOUT_MS,
        )


async def convert_url_to_pdf(
    url: str,
    custom_name: str | None = None,
    clean: bool = True,
    margin: str = "12mm",
    page_format: str = "A4",
    landscape: bool = False,
) -> Path:
    """
    Convert a webpage URL into a PDF and return the generated file path.
    """

    PDF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    normalized_url = validate_url(url)

    selected_format = page_format if page_format in ALLOWED_FORMATS else "A4"
    selected_margin = margin if margin in ALLOWED_MARGINS else "12mm"

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )

            page = await browser.new_page(
                viewport={
                    "width": 1440,
                    "height": 1200,
                }
            )

            try:
                await load_page_safely(page, normalized_url)

                await page.emulate_media(media="screen")

                await auto_scroll_page(page)

                await page.wait_for_timeout(1000)

                title = await get_page_title(page)

                if clean:
                    await clean_page_for_pdf(page)
                    await page.wait_for_timeout(500)

                if custom_name and custom_name.strip():
                    base_name = custom_name.strip()
                elif title:
                    base_name = title
                else:
                    base_name = filename_from_url(normalized_url)

                safe_name = slugify_filename(base_name)

                filename = f"{safe_name}-{uuid.uuid4().hex[:8]}.pdf"
                output_path = PDF_OUTPUT_DIR / filename

                await page.pdf(
                    path=str(output_path),
                    format=selected_format,
                    landscape=landscape,
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={
                        "top": selected_margin,
                        "right": selected_margin,
                        "bottom": selected_margin,
                        "left": selected_margin,
                    },
                )

                return output_path

            finally:
                await page.close()
                await browser.close()

    except HTTPException:
        raise

    except PlaywrightTimeoutError as exc:
        raise HTTPException(
            status_code=500,
            detail="Page timed out while loading. Try again or use a simpler URL.",
        ) from exc

    except PlaywrightError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"PDF conversion failed: {exc}",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected PDF conversion error: {exc}",
        ) from exc