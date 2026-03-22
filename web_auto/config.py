import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
import re
import requests
from bs4 import BeautifulSoup

# ─── Available Departments (fetched dynamically) ─────────────

def _fetch_departments():
    """Scrape department codes and names from the source website."""
    url = os.getenv("SOURCE_DOMAIN") or "https://cce.edu.in"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"  ⚠ Could not fetch departments from {url}: {e}")
        return {}

    soup = BeautifulSoup(r.text, "html.parser")
    depts = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"/department/([^/]+)/", a["href"])
        if m:
            code = m.group(1)
            name = a.get_text(strip=True)
            # Clean up whitespace from multi-line link text
            name = re.sub(r"\s+", " ", name).strip()
            # Prefer names that start with "Department" or are longer
            if code not in depts or (
                "Department" in name and "Department" not in depts[code]
            ) or (
                "Department" in name and len(name) > len(depts[code])
            ):
                if name:
                    depts[code] = name
    return depts


# Fetched once at import time (cached for the session)
DEPARTMENTS = _fetch_departments()

# ─── CMS Admin Portal ────────────────────────────────────────
CMS_BASE   = os.getenv("CMS_BASE")
LOGIN_URL  = f"{CMS_BASE}/login"

# Per-type CMS URLs
CMS_URLS = {
    "department": {
        "create": f"{CMS_BASE}/departments/create",
        "list":   f"{CMS_BASE}/departments",
    },
    "faculty": {
        "create": f"{CMS_BASE}/faculties/create",
        "list":   f"{CMS_BASE}/faculties",
    },
    "magazine": {
        "create": f"{CMS_BASE}/magazines/create",
        "list":   f"{CMS_BASE}/magazines",
    },
}

# Legacy alias used by department pipeline
CREATE_PAGE = CMS_URLS["department"]["create"]

# ─── Source Website ───────────────────────────────────────────
SOURCE_DOMAIN = os.getenv("SOURCE_DOMAIN") or "https://cce.edu.in"
SOURCE_URL    = ""  # Set at runtime via set_department()

def set_department(dept_code):
    """Set the active department. Call before importing scrapers."""
    global SOURCE_URL
    SOURCE_URL = f"{SOURCE_DOMAIN}/department/{dept_code}"

# ─── Credentials ─────────────────────────────────────────────
EMAIL    = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

# ─── Google Gemini API ────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDtb9glgvwEt62vne0Z8eJWOb90hFWLnu4")
AI_MODEL       = "gemini-2.0-flash"

# ─── Paths ────────────────────────────────────────────────────
BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR        = os.path.join(BASE_DIR, "assets")
FORM_SCHEMA_FILE  = os.path.join(BASE_DIR, "form_schema.json")
SCRAPE_PLAN_FILE  = os.path.join(BASE_DIR, "scrape_plan.json")
SCRAPED_DATA_FILE = os.path.join(BASE_DIR, "scraped_data.json")

# Per-type data files
FACULTY_DATA_FILE  = os.path.join(BASE_DIR, "faculty_data.json")
MAGAZINE_DATA_FILE = os.path.join(BASE_DIR, "magazine_data.json")
