"""
Stage 2 – AI Prompt Generator
Fetches the source website, combines it with the CMS form schema,
and prints a ready-to-paste ChatGPT prompt in the terminal.
Then reads ChatGPT's JSON response from stdin.

Everything happens in the terminal — no file editing required.
"""

import json
import asyncio
import os
import sys
import subprocess

import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin

import config
from config import (
    SOURCE_DOMAIN,
    FORM_SCHEMA_FILE, SCRAPE_PLAN_FILE, BASE_DIR,
)

HEADERS = {"User-Agent": "Mozilla/5.0"}


# ─── source‑page helpers ─────────────────────────────────────

def _simplify(html, max_text=12000):
    """Strip scripts/styles and return text + image/link inventories."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(
        ["script", "style", "nav", "footer", "header", "noscript"]
    ):
        tag.decompose()

    images = []
    for img in soup.find_all("img", src=True):
        images.append({
            "src": urljoin(config.SOURCE_URL, img["src"]),
            "alt": img.get("alt", ""),
        })

    dept_links = []
    for a in soup.find_all("a", href=True):
        if "/department/" in a["href"].lower():
            dept_links.append({
                "href": urljoin(config.SOURCE_URL, a["href"]),
                "text": a.get_text(strip=True),
            })

    return {
        "text": soup.get_text("\n", strip=True)[:max_text],
        "images": images[:60],
        "links":  dept_links[:40],
    }


async def _fetch(session, url):
    try:
        async with session.get(
            url, headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            return await r.text()
    except Exception:
        return None


async def fetch_source_pages():
    """Return a dict of  page‑label → simplified‑content."""
    async with aiohttp.ClientSession() as session:
        main_html = await _fetch(session, config.SOURCE_URL)
        if not main_html:
            raise RuntimeError(f"Could not fetch {config.SOURCE_URL}")

        soup = BeautifulSoup(main_html, "html.parser")

        # Extract department code from SOURCE_URL dynamically
        # e.g. "https://cce.edu.in/department/CE" → "/department/CE"
        dept_path = "/department/" + config.SOURCE_URL.rstrip("/").split("/")[-1]

        sub_urls = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if dept_path in href:
                full = urljoin(config.SOURCE_URL, href)
                label = a.get_text(strip=True) or full
                if full != config.SOURCE_URL:
                    sub_urls[full] = label

        pages = {"main": _simplify(main_html)}

        for url, label in list(sub_urls.items())[:12]:
            html = await _fetch(session, url)
            if html:
                pages[label] = _simplify(html)

        return pages


# ─── build prompt ────────────────────────────────────────────

def build_prompt(form_schema, source_content):
    # Filter out layout/theme radio buttons (not real form fields)
    skip_prefixes = (
        "layout", "topbar-color", "sidebar-size",
        "sidebar-color", "layout-direction", "layout-mode",
        "layout-width", "layout-position",
    )
    fields = [
        f for f in form_schema["fields"]
        if not f.get("name", "").startswith(skip_prefixes)
    ]

    prompt = f"""You are a web-scraping planner. I'm migrating content from a college department website into a CMS admin form.

## SOURCE WEBSITE: {config.SOURCE_URL}

Below is the scraped content from all discovered pages (text, images, links):

{json.dumps(source_content, indent=2)}

## CMS FORM FIELDS (that need to be filled)

{json.dumps(fields, indent=2)}

## Tabs detected in the CMS
{json.dumps(form_schema.get("tabs", []))}

## YOUR TASK

For EVERY field listed above, produce a JSON scraping plan.
Return ONLY a valid JSON object (no markdown code fences, no explanation) with this exact structure:

{{
  "fields": [
    {{
      "cms_field_name":     "<name attribute of CMS field>",
      "cms_field_type":     "text | file | select | textarea | rich_text_editor | checkbox | radio",
      "source_url":        "<full URL to fetch, or null>",
      "extraction_method": "css_selector | text_match | image_download | pdf_download | static_value",
      "selector_or_value": "<CSS selector, regex hint, or static value>",
      "download_url":      "<direct URL for files/images, or null>",
      "transform":         "none | strip | join_newlines | keep_html",
      "value":             "<if static_value, the literal string to use, else null>"
    }}
  ]
}}

## RULES
1. For **file upload** fields (banner, banner_mobile, activity_points_pdf), find the best matching image or document from the source site and provide its full download URL.
2. For **rich text editors** (about_description, dab_description, pac_description, laboratories_description, activity_points_description), use keep_html transform and put the full HTML content in the "value" field. Scrape the actual content from the matching source page (e.g. DAB page for dab_description, PAC page for pac_description). If the source page has table data, convert it to an HTML table.
3. For all **select dropdowns** (status fields), use static_value with value "1" (Enabled).
4. For **tab_title** fields, use static_value with sensible tab names: "About", "People", "Curriculum", "DAB", "PAC", etc.
5. For **title** fields within each section, use the actual heading text from the source website if available.
6. For **contact info** (hod_name, hod_phone, hod_email, staff_name, etc.), extract the actual faculty contact details from the source website.
7. If no source content exists for a field, still include it with extraction_method = "static_value" and a reasonable default or empty string — but **STILL INCLUDE IT in the output**.
8. For image downloads, provide the FULL absolute URL.
9. Return ONLY the JSON — no markdown fences, no extra text.

## MANDATORY — DO NOT SKIP ANY FIELD
10. **You MUST produce exactly one entry in the "fields" array for EVERY field listed above.** Count the CMS fields — your output must have the same count. If you skip even one field, the migration will fail. Even if a field has no matching source content, include it with extraction_method = "static_value" and value = "".

## CRITICAL FORMATTING RULES — READ CAREFULLY
10. **NEVER use markdown formatting in any value.** All values must be PLAIN TEXT.
    - WRONG: `[email@example.com](mailto:email@example.com)` 
    - RIGHT: `email@example.com`
    - WRONG: `[Click here](https://example.com)`
    - RIGHT: `https://example.com`
11. **Email fields** must contain ONLY the raw email address (e.g. `name@college.edu.in`). No markdown links, no mailto: prefix, no brackets.
12. **Phone fields** must contain ONLY the phone number (e.g. `+91 9497317677`). No markdown, no call: prefix.
13. **Text fields** must contain ONLY plain text. No markdown bold (`**`), italic (`_`), links (`[]()`), or any other formatting.
14. **HTML is ONLY allowed** in fields with `"transform": "keep_html"` (rich text editors). Even there, use proper HTML tags (`<p>`, `<h2>`, `<ul>`) — NOT markdown.
15. The output must be **parseable by `json.loads()`** with no modifications needed.
"""
    return prompt


def _copy_to_clipboard(text):
    """Copy text to the system clipboard. Works on Linux (xclip/xsel), macOS, WSL."""
    for cmd in (["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
                ["pbcopy"],
                ["clip.exe"]):
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            if p.returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False


# ─── terminal I/O helpers ─────────────────────────────────────

def read_plan_from_stdin():
    """Read a multi-line JSON response from stdin.

    The user pastes ChatGPT's JSON output, then types END on its own
    line to finish.  Returns the parsed dict.
    """
    print("\n" + "=" * 60)
    print("  PASTE ChatGPT's JSON response below.")
    print("  When done, type  END  on a new line and press ENTER.")
    print("=" * 60 + "\n")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)

    raw = "\n".join(lines).strip()

    # strip markdown code fences if present
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    raw = raw.strip()

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\n  ✘ Invalid JSON: {e}")
        print("  Saving raw input to scrape_plan_raw.txt for debugging.")
        with open(os.path.join(BASE_DIR, "scrape_plan_raw.txt"), "w") as f:
            f.write(raw)
        sys.exit(1)

    return plan


# ─── entry point ─────────────────────────────────────────────

def run(form_schema=None):
    """Generate prompt, print it, read plan from stdin, return plan dict.

    Parameters
    ----------
    form_schema : dict, optional
        Pre-loaded form schema.  If None, loads from FORM_SCHEMA_FILE.

    Returns
    -------
    dict
        The scrape plan (parsed JSON from ChatGPT).
    """
    if form_schema is None:
        with open(FORM_SCHEMA_FILE) as f:
            form_schema = json.load(f)
    print(f"✔ Loaded form schema ({form_schema['total_fields']} fields)")

    print("⏳ Fetching source website …")
    source_content = asyncio.run(fetch_source_pages())
    print(f"✔ Fetched {len(source_content)} pages from source site")

    print("⏳ Building prompt …\n")
    prompt = build_prompt(form_schema, source_content)

    # ── Copy prompt to clipboard automatically ──
    if _copy_to_clipboard(prompt):
        print("\n  ✔ Prompt copied to clipboard! Paste it into ChatGPT.")
    else:
        print("\n  ⚠ Could not copy to clipboard (install xclip: sudo apt install xclip)")
        print("    Select and copy the prompt above manually.")

    # ── Print the prompt in the terminal as fallback ──
    print("=" * 60)
    print("  PROMPT (also copied to clipboard)")
    print("=" * 60)
    print(prompt)
    print("=" * 60)
    print("  END OF PROMPT — paste into ChatGPT now")
    print("=" * 60)

    # ── Read the plan back from terminal ──
    plan = read_plan_from_stdin()

    # Save plan to file as backup
    with open(SCRAPE_PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    print(f"\n✔ Scrape plan saved  →  {SCRAPE_PLAN_FILE}")
    print(f"  Fields in plan: {len(plan.get('fields', []))}")

    return plan


if __name__ == "__main__":
    run()
