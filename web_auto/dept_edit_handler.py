"""
Post-Submit Edit Flow
─────────────────────
After a department is created and submitted, this module:

1. Finds the newly-created department in the CMS list and clicks Edit.
2. Discovers all form sections/tabs on the edit page.
3. For each section: inspect fields → print AI prompt → read scrape plan
   from stdin → scrape → fill → pause for manual submit.

Everything is terminal-only — no file editing required by the user.
"""

import time
import re
import json

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import config
from config import CMS_URLS


# ─── helpers ──────────────────────────────────────────────────

def _find_edit_url(driver):
    """Navigate to the departments list and find the Edit button for
    the most recently created department (first row).

    Returns the edit page URL, or None if not found.
    """
    list_url = CMS_URLS["department"]["list"]
    wait = WebDriverWait(driver, 15)

    driver.get(list_url)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(3)

    # Look for the first Edit link/button in the table
    edit_links = driver.find_elements(
        By.XPATH,
        "//a[contains(@href, '/departments/') and "
        "(contains(@href, '/edit') or contains(text(), 'Edit'))]"
    )

    if edit_links:
        # The most recent entry is typically at the top
        href = edit_links[0].get_attribute("href")
        print(f"  ✔ Found edit URL: {href}")
        return href

    # Fallback: look for action buttons with edit icon
    edit_btns = driver.find_elements(
        By.CSS_SELECTOR,
        "a.btn i.fa-edit, a.btn i.fa-pencil, a.btn i.mdi-pencil"
    )
    if edit_btns:
        parent = edit_btns[0].find_element(By.XPATH, "..")
        href = parent.get_attribute("href")
        if href:
            print(f"  ✔ Found edit URL (icon): {href}")
            return href

    print("  ✘ Could not find an Edit link on the departments list page.")
    return None


def _discover_sections(driver):
    """Discover all tabs / sections on the edit page.

    Returns a list of dicts:
        [{"name": "About", "tab_element_index": 0}, ...]
    """
    tab_sel = (
        '[role="tab"], .nav-link, .tab-link, '
        '[data-toggle="tab"], [data-bs-toggle="tab"]'
    )
    tabs = driver.find_elements(By.CSS_SELECTOR, tab_sel)

    sections = []
    for idx, tab in enumerate(tabs):
        text = tab.text.strip()
        if text:
            sections.append({"name": text, "tab_index": idx})

    return sections


def _click_tab(driver, tab_index):
    """Click the tab at the given index to reveal its fields."""
    tab_sel = (
        '[role="tab"], .nav-link, .tab-link, '
        '[data-toggle="tab"], [data-bs-toggle="tab"]'
    )
    tabs = driver.find_elements(By.CSS_SELECTOR, tab_sel)
    if tab_index < len(tabs):
        try:
            tabs[tab_index].click()
            time.sleep(1)
            return True
        except Exception:
            pass
    return False


# ─── main edit flow ──────────────────────────────────────────

def run_edit_flow(driver, edit_url=None):
    """Orchestrate the post-submit edit page flow.

    Parameters
    ----------
    driver : WebDriver
        Shared browser session (must already be logged in).
    edit_url : str, optional
        Direct URL to the edit page.  If None, auto-discovers from
        the department list (picks the most recent entry).
    """
    wait = WebDriverWait(driver, 15)

    # ── 1. Navigate to the edit page ──────────────────────────
    if not edit_url:
        print("\n" + "=" * 60)
        print("  EDIT FLOW — LOCATING DEPARTMENT")
        print("=" * 60)
        edit_url = _find_edit_url(driver)

    if not edit_url:
        # Let user paste the URL manually
        print("\n  Could not auto-detect the edit page URL.")
        edit_url = input("  Paste the edit page URL (or press ENTER to skip): ").strip()
        if not edit_url:
            print("  ⏹ Skipping edit flow.")
            return

    driver.get(edit_url)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(3)

    print(f"\n  ✔ On edit page: {edit_url}")

    # ── 2. Discover sections / tabs ───────────────────────────
    sections = _discover_sections(driver)
    if not sections:
        print("  ⚠ No tabs/sections found on the edit page.")
        print("  Running full-page inspect → plan → scrape → fill instead.\n")
        sections = [{"name": "Full Page", "tab_index": -1}]

    print(f"\n  Discovered {len(sections)} section(s):")
    for i, sec in enumerate(sections, 1):
        print(f"    {i}. {sec['name']}")

    # ── 3. For each section: inspect → prompt → scrape → fill ─
    from cms_inspector import inspect_form
    from ai_planner import build_prompt, fetch_source_pages, read_plan_from_stdin
    from smart_scraper import run as scrape_run
    from form_filler import run as fill_run

    # Fetch source content once (shared across all sections)
    import asyncio
    print("\n⏳ Fetching source website (shared across all sections) …")
    source_content = asyncio.run(fetch_source_pages())
    print(f"✔ Fetched {len(source_content)} pages from source site\n")

    for i, section in enumerate(sections, 1):
        sec_name = section["name"]
        tab_idx = section["tab_index"]

        print("\n" + "=" * 60)
        print(f"  SECTION {i}/{len(sections)}: {sec_name}")
        print("=" * 60)

        # Ask user if they want to handle this section
        ans = input(f"\n  Process this section? (ENTER=yes / skip / quit): ").strip().lower()
        if ans == "quit":
            print("  ⏹ Stopping edit flow.")
            break
        if ans == "skip":
            print(f"  ⏭ Skipping {sec_name}")
            continue

        # Click the tab to reveal its fields
        if tab_idx >= 0:
            driver.get(edit_url)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(2)
            _click_tab(driver, tab_idx)
            time.sleep(1)

        # ── 3a. Inspect fields on this section ────────────────
        print(f"\n  ⏳ Inspecting fields for: {sec_name}")

        # Inspect the current page (fields visible after clicking tab)
        schema = inspect_form(driver, url=None)
        # Don't navigate — inspect_form with url=None should
        # inspect the current page state.  But our inspect_form
        # calls driver.get(url) which would reload.
        # So instead, re-navigate + click tab + inspect with
        # the edit URL.
        schema = inspect_form(driver, url=edit_url)

        # After inspection, click tab again (inspect navigated away via driver.get)
        if tab_idx >= 0:
            _click_tab(driver, tab_idx)
            time.sleep(1)

        if not schema["fields"]:
            print(f"  ⚠ No fields found for section '{sec_name}'. Skipping.")
            continue

        print(f"  ✔ Found {schema['total_fields']} fields")

        # ── 3b. Build & print AI prompt ───────────────────────
        print(f"\n  ⏳ Building AI prompt for: {sec_name}")
        prompt = build_prompt(schema, source_content)

        print("\n" + "=" * 60)
        print(f"  PROMPT (Section: {sec_name})")
        print("=" * 60)
        print(prompt)
        print("=" * 60)
        print(f"  END OF PROMPT for section: {sec_name}")
        print("=" * 60)

        # Copy to clipboard
        from ai_planner import _copy_to_clipboard
        if _copy_to_clipboard(prompt):
            print(f"\n  ✔ Prompt copied to clipboard!")
        else:
            print(f"\n  ⚠ Could not copy to clipboard — copy manually from above.")

        # ── 3c. Read scrape plan from stdin ───────────────────
        plan = read_plan_from_stdin()
        print(f"  ✔ Got scrape plan with {len(plan.get('fields', []))} fields")

        # ── 3d. Scrape ────────────────────────────────────────
        print(f"\n  ⏳ Scraping for section: {sec_name}")
        scraped = scrape_run(plan=plan, schema=schema, save_path=False)
        print(f"  ✔ Scraped {len(scraped)} fields")

        # ── 3e. Fill the form ─────────────────────────────────
        print(f"\n  ⏳ Filling fields for section: {sec_name}")

        # Navigate to edit page and click the right tab
        fill_run(
            driver=driver,
            url=edit_url,
            schema=schema,
            data=scraped,
        )

        # fill_run pauses for manual submit — after user submits
        # and presses ENTER, we continue to the next section.

        print(f"\n  ✔ Section '{sec_name}' complete.")

    print("\n" + "=" * 60)
    print("  ✔ EDIT FLOW COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    # standalone test
    from browser import get_driver, login

    driver = get_driver()
    try:
        login(driver)
        run_edit_flow(driver)
    finally:
        driver.quit()
