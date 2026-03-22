"""
Stage 3 – Smart Scraper
Follows scrape_plan.json:
  • Extracts text/HTML via CSS selectors
  • Downloads images and PDFs to ./assets/
  • Falls back to the LLM when selectors miss
Outputs scraped_data.json  +  assets/ folder.
"""

import json
import os
import asyncio
import re

import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin

import config
from config import (
    ASSETS_DIR,
    FORM_SCHEMA_FILE, SCRAPE_PLAN_FILE, SCRAPED_DATA_FILE,
)
from compress import compress_image

HEADERS = {"User-Agent": "Mozilla/5.0"}
os.makedirs(ASSETS_DIR, exist_ok=True)

# Known sub-page URL patterns for auto-discovery
# Only pages that actually exist on cce.edu.in are mapped.
SUB_PAGE_MAP = {
    "about":            "about",
    "dab":              "DAB",
    "pac":              "PAC",
    "achievements":     "achievements",
    "people":           "faculty",
    "research":         "research",
    "placements":       "placements",
    "association":      "associations",
    "professional_bodies": "professionalBodies",
    "e_content":        "e-content",
    "clubs":            "clubs",
    "laboratories":     "about",       # labs info is on the about page
    "activity_points":  "about",       # activity points info is on about page
}


# ─── URL cleaning ────────────────────────────────────────────

def _clean_url(url):
    """Strip markdown link formatting:  [text](actual_url) → actual_url"""
    if not url:
        return url
    m = re.search(r'\[.*?\]\((https?://[^)]+)\)', url)
    if m:
        return m.group(1)
    return url.strip()


# ─── download helpers ────────────────────────────────────────

def _safe_filename(name, ext):
    """Sanitise a string into a filename‑safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)[:80]
    return f"{slug}{ext}"


async def download_file(session, url, field_name):
    """Download a binary file into assets/. Returns local path or None."""
    try:
        url = _clean_url(url)
        resolved = urljoin(config.SOURCE_URL, url)
        async with session.get(
            resolved, headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            if r.status != 200:
                print(f"  ✘ HTTP {r.status} for {resolved}")
                return None

            # determine extension from Content‑Type or URL
            ct = r.content_type or ""
            if "png" in ct:
                ext = ".png"
            elif "gif" in ct:
                ext = ".gif"
            elif "webp" in ct:
                ext = ".webp"
            elif "pdf" in ct:
                ext = ".pdf"
            elif "svg" in ct:
                ext = ".svg"
            else:
                # fall back to URL extension
                ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"

            filename = _safe_filename(field_name, ext)
            filepath = os.path.join(ASSETS_DIR, filename)
            data = await r.read()
            with open(filepath, "wb") as f:
                f.write(data)
            print(f"  ✔ Downloaded  →  assets/{filename}  ({len(data)} bytes)")
            filepath = compress_image(filepath)
            return filepath
    except Exception as e:
        print(f"  ✘ Download failed ({url}): {e}")
        return None


# ─── extraction helpers ──────────────────────────────────────

async def _fetch(session, url):
    try:
        async with session.get(
            url, headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                print(f"  ✘ HTTP {r.status} for {url}")
                return None
            return await r.text()
    except Exception:
        return None


async def extract_by_selector(session, url, selector, transform="none"):
    """Fetch a page and apply a CSS selector."""
    html = await _fetch(session, url)
    if not html:
        return None

    # detect error / 404 pages
    lower = html.lower()
    for phrase in ["page you are looking for", "page not found", "404",
                   "relocated or deleted", "we can't find", "does not exist"]:
        if phrase in lower:
            print(f"  ✘ error page detected ('{phrase}')")
            return None

    soup = BeautifulSoup(html, "html.parser")
    elements = soup.select(selector)
    if not elements:
        return None

    if transform == "keep_html":
        return "".join(str(el) for el in elements)
    elif transform == "join_newlines":
        return "\n".join(el.get_text(strip=True) for el in elements)
    else:                         # strip / none
        return elements[0].get_text(strip=True)


def ai_extract(html_chunk, instruction):
    """Fallback when selector-based extraction fails (no API)."""
    print("  ⚠ AI fallback skipped (no API configured)")
    return ""


# ─── main scraper loop ───────────────────────────────────────

async def run_scraper(plan):
    scraped = {}

    async with aiohttp.ClientSession() as session:
        for field in plan.get("fields", []):
            name   = field["cms_field_name"]
            method = field["extraction_method"]
            url    = _clean_url(field.get("source_url", "")) or config.SOURCE_URL

            print(f"\n⏳ {name}  ({method})")

            # ---- static value ------------------------------------------------
            if method == "static_value":
                ftype = field.get("cms_field_type", "")
                is_desc = (ftype in ("rich_text_editor", "textarea")
                           and "description" in name)

                # For description / rich-text fields, don't trust the
                # static value — actually scrape the page instead.
                if is_desc and url:
                    print(f"  ⚠ description field — scraping real content from {url}")
                    content = await _scrape_page_content(session, url)
                    if content:
                        scraped[name] = content
                        print(f"  ✔ scraped ({len(content)} chars)")
                    else:
                        # fall back to static value if scrape fails
                        scraped[name] = field.get("value", "")
                        print(f"  ⚠ scrape failed — using static fallback")
                else:
                    scraped[name] = field.get("value", "")
                    print(f"  ✔ value = {str(scraped[name])[:80]}")

            # ---- css selector ------------------------------------------------
            elif method == "css_selector":
                selector  = field.get("selector_or_value", "")
                transform = field.get("transform", "none")
                result    = await extract_by_selector(
                    session, url, selector, transform
                )
                if result:
                    scraped[name] = result
                    print(f"  ✔ extracted ({len(result)} chars)")
                else:
                    # AI fallback
                    print("  ⚠ selector missed – trying AI fallback")
                    html = await _fetch(session, url)
                    if html:
                        result = ai_extract(
                            html,
                            f"Extract the content for the CMS field "
                            f"'{name}' (label/context: "
                            f"{field.get('selector_or_value','')})",
                        )
                        scraped[name] = result
                        print(f"  ✔ AI fallback ({len(result)} chars)")
                    else:
                        scraped[name] = ""

            # ---- image / pdf / file download ---------------------------------
            elif method in (
                "image_download", "pdf_download", "file_download"
            ):
                dl_url = _clean_url(
                    field.get("download_url")
                    or field.get("selector_or_value", "")
                )
                if dl_url:
                    path = await download_file(session, dl_url, name)
                    scraped[name] = path or ""
                else:
                    scraped[name] = ""
                    print("  ✘ no download URL provided")

            # ---- text_match (AI‑assisted) ------------------------------------
            elif method == "text_match":
                # text_match relies on AI extraction which has no API.
                # Use the static value from the plan if available.
                fallback_value = field.get("value", "")
                is_desc = ("description" in name)

                if is_desc and url:
                    # For description fields, scrape the real page
                    print(f"  ⚠ description field — scraping real content from {url}")
                    content = await _scrape_page_content(session, url)
                    if content:
                        scraped[name] = content
                        print(f"  ✔ scraped ({len(content)} chars)")
                    elif fallback_value:
                        scraped[name] = fallback_value
                        print(f"  ✔ using plan value ({len(fallback_value)} chars)")
                    else:
                        scraped[name] = ""
                        print("  ✘ no content found")
                elif fallback_value:
                    scraped[name] = fallback_value
                    print(f"  ✔ value = {str(fallback_value)[:80]}")
                else:
                    scraped[name] = ""
                    print("  ✘ no value and no AI available")

            # ---- unknown method ----------------------------------------------
            else:
                scraped[name] = field.get("value", "")
                print(f"  ⚠ unknown method — used value fallback")

    return scraped


# ─── auto-fill missing fields ────────────────────────────────

def _guess_source_url(field_name):
    """Try to build a source sub-page URL based on field name."""
    for prefix, sub_path in SUB_PAGE_MAP.items():
        if field_name.startswith(prefix):
            base = config.SOURCE_URL.rstrip("/")
            return f"{base}/{sub_path}"
    return None


def _strip_classes(soup):
    """Remove all class/style attributes to produce clean HTML."""
    for tag in soup.find_all(True):
        tag.attrs = {k: v for k, v in tag.attrs.items()
                     if k not in ("class", "style")}
    return soup


def _dedup_elements(parts):
    """Remove duplicate HTML fragments (desktop + mobile responsive copies)."""
    seen_texts = set()
    unique = []
    for html_str in parts:
        # normalise whitespace for dedup comparison
        text = re.sub(r'\s+', ' ', BeautifulSoup(html_str, 'html.parser').get_text()).strip()
        if text and text not in seen_texts:
            seen_texts.add(text)
            unique.append(html_str)
    return unique


async def _scrape_page_content(session, url):
    """Fetch a page and extract main content as clean HTML."""
    html = await _fetch(session, url)
    if not html:
        return ""

    # detect error / 404 pages
    lower = html.lower()
    error_phrases = [
        "page you are looking for",
        "page not found",
        "relocated or deleted",
        "we can't find",
        "does not exist",
    ]
    for phrase in error_phrases:
        if phrase in lower:
            print(f"    ✘ error page detected ('{phrase}')")
            return ""

    soup = BeautifulSoup(html, "html.parser")

    # remove non-content elements
    for tag in soup.find_all(
        ["script", "style", "nav", "footer", "header", "noscript", "aside"]
    ):
        tag.decompose()

    # Target <section id="content"> which holds the actual page content
    content = soup.find("section", id="content")
    if not content:
        # fallback: try the desktop content div
        content = soup.find("div", class_=lambda c: c and "px-20" in c and "md:block" in c)
    if not content:
        content = soup.find("main")
    if not content:
        content = soup.find("body")
    if not content:
        return ""

    # Remove gallery and contact sections (they appear on every page)
    for sec in content.find_all("section"):
        sec_id = sec.get("id", "")
        sec_cls = " ".join(sec.get("class", []))
        if "gallery" in sec_id or "contact" in sec_id or "gallery" in sec_cls:
            sec.decompose()
    # Also remove elements that are clearly "Gallery" / "Contact us" headings
    for heading in content.find_all(["h1", "h2"]):
        txt = heading.get_text(strip=True).lower()
        if txt in ("gallery", "contact us", "faculty"):
            heading.decompose()

    # Strip CSS classes and styles
    _strip_classes(content)

    # Extract only plain descriptions and tables — no headings.
    # Headings (Vision, Mission, PEOs, etc.) belong in separate CMS
    # fields that are only available after the initial page create.
    parts = []
    for el in content.find_all(["p", "table"]):
        # skip breadcrumb / nav-like paragraphs
        text = el.get_text(strip=True)
        if not text or len(text) < 10:
            continue
        # skip breadcrumb lines like "Home / Academics / ..."
        if text.startswith("Home /") or text.startswith("Home/"):
            continue
        # skip hero-overlay text (very short decorative text)
        if el.find_parent("section", class_=lambda c: c and "home_section" in c):
            continue
        parts.append(str(el))

    # Deduplicate (responsive copies)
    parts = _dedup_elements(parts)

    return "\n".join(parts) if parts else ""


async def fill_missing_fields(scraped, schema_fields):
    """Cross-reference form_schema and scrape missing fields."""
    skip_prefixes = (
        "layout", "topbar-color", "sidebar-size",
        "sidebar-color", "layout-direction", "layout-mode",
        "layout-width", "layout-position",
    )

    missing = []
    for fld in schema_fields:
        name = fld.get("name", "")
        if not name or name.startswith(skip_prefixes):
            continue
        if name not in scraped:
            missing.append(fld)

    if not missing:
        print("\n✔ No missing fields")
        return scraped

    print(f"\n⚠ {len(missing)} fields missing from scrape plan — auto-filling...")

    async with aiohttp.ClientSession() as session:
        for fld in missing:
            name  = fld["name"]
            ftype = fld["type"]
            print(f"\n  ⏳ auto-fill: {name} ({ftype})")

            if ftype == "rich_text_editor" or (
                ftype == "textarea" and "description" in name
            ):
                url = _guess_source_url(name)
                if url:
                    print(f"    trying {url}")
                    content = await _scrape_page_content(session, url)
                    if content:
                        scraped[name] = content
                        print(f"    ✔ scraped ({len(content)} chars)")
                    else:
                        scraped[name] = ""
                        print(f"    ✘ page empty or not found")
                else:
                    scraped[name] = ""
                    print(f"    ✘ no matching source URL")

            elif ftype == "select":
                scraped[name] = "1"
                print(f"    ✔ default = 1 (Enabled)")

            elif "tab_title" in name:
                label = name.replace("_tab_title", "").replace("_", " ").title()
                scraped[name] = label
                print(f"    ✔ default = {label}")

            elif "_title" in name:
                label = name.replace("_title", "").replace("_", " ").title()
                scraped[name] = label
                print(f"    ✔ default = {label}")

            else:
                scraped[name] = ""
                print(f"    ✔ default = (empty)")

    return scraped


# ─── entry point ─────────────────────────────────────────────

def run(plan=None, schema=None, save_path=None):
    """Execute the scrape plan and return scraped data dict.

    Parameters
    ----------
    plan : dict, optional
        Scrape plan from ChatGPT.  If None, loads from SCRAPE_PLAN_FILE.
    schema : dict, optional
        Form schema.  If None, loads from FORM_SCHEMA_FILE.
    save_path : str | False, optional
        Where to save scraped data JSON.  Defaults to SCRAPED_DATA_FILE.
        Pass False to skip saving entirely.

    Returns
    -------
    dict
        Scraped data keyed by CMS field name.
    """
    if plan is None:
        with open(SCRAPE_PLAN_FILE) as f:
            plan = json.load(f)
    print(f"✔ Loaded scrape plan — {len(plan.get('fields',[]))} fields")

    if schema is None:
        with open(FORM_SCHEMA_FILE) as f:
            schema = json.load(f)
    print(f"✔ Loaded form schema — {schema['total_fields']} fields")

    scraped = asyncio.run(run_scraper(plan))

    # auto-fill any fields missing from the plan
    scraped = asyncio.run(fill_missing_fields(scraped, schema["fields"]))

    out = save_path if save_path is not None else SCRAPED_DATA_FILE
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(scraped, f, indent=2, ensure_ascii=False)
        print(f"\n✔ Scraped data saved  →  {out}")

    print(f"  Total fields: {len(scraped)}")

    # summarise downloaded files
    files = [
        v for v in scraped.values()
        if isinstance(v, str) and ASSETS_DIR in v
    ]
    if files:
        print(f"  Downloaded files ({len(files)}):")
        for fp in files:
            print(f"    • {os.path.basename(fp)}")

    return scraped


if __name__ == "__main__":
    run()
