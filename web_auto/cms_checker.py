"""
CMS Checker – Scrapes the CMS list page to discover already-entered records.
Returns a set of existing names/titles so scrapers can skip duplicates.

Supports pagination: clicks "Next" until all pages are traversed.
"""

import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from browser import get_driver, login
from config import CMS_URLS


def get_existing_entries(page_type, driver=None):
    """
    Navigate to the CMS list page for the given page_type
    and return a set of existing entry names/titles (lowercased for matching).

    Parameters
    ----------
    page_type : str
        "faculty" or "magazine"
    driver : WebDriver, optional
        If provided, reuses this driver (must already be logged in).
        If None, creates a new driver + logs in, then quits when done.

    Returns
    -------
    set[str]
        Lowercased names/titles already present in CMS.
    """
    own_driver = driver is None
    if own_driver:
        driver = get_driver()
        login(driver)

    try:
        list_url = CMS_URLS[page_type]["list"]
        wait = WebDriverWait(driver, 15)
        existing = set()
        page_num = 0

        driver.get(list_url)
        time.sleep(3)

        while True:
            page_num += 1
            # Wait for table to load
            try:
                wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "table tbody tr")
                ))
            except Exception:
                print(f"  ⚠ No table rows found on page {page_num}")
                break

            rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if not cells:
                    continue
                # The name/title is typically in the first or second column
                # (first column is often SL.No or checkbox)
                name = ""
                for cell in cells[:3]:  # check first 3 columns
                    text = cell.text.strip()
                    # Skip numeric-only cells (SL.No, sort_order)
                    if text and not text.isdigit():
                        name = text
                        break

                if name:
                    existing.add(name.lower())

            count_before = len(existing)
            print(f"  Page {page_num}: found {len(rows)} rows, "
                  f"total unique names: {len(existing)}")

            # Try clicking "Next" for pagination
            try:
                next_btn = driver.find_element(
                    By.CSS_SELECTOR,
                    "a.page-link[rel='next'], "
                    "li.page-item:not(.disabled) a:contains('Next'), "
                    ".pagination a[aria-label='Next »'], "
                    ".pagination li:last-child a"
                )
                # Check if it's actually a "next" link
                href = next_btn.get_attribute("href")
                text = next_btn.text.strip().lower()
                aria = next_btn.get_attribute("aria-label") or ""

                if (href and ("page=" in href or "next" in text.lower()
                              or "next" in aria.lower() or "»" in text)):
                    next_btn.click()
                    time.sleep(2)
                    continue
            except Exception:
                pass

            # Alternative: look for numbered pagination links
            try:
                next_page = page_num + 1
                page_links = driver.find_elements(
                    By.CSS_SELECTOR,
                    f".pagination a[href*='page={next_page}']"
                )
                if page_links:
                    page_links[0].click()
                    time.sleep(2)
                    continue
            except Exception:
                pass

            # No more pages
            break

        print(f"\n✔ CMS {page_type} checker: {len(existing)} existing entries found")
        return existing

    finally:
        if own_driver:
            driver.quit()


if __name__ == "__main__":
    import sys
    pt = sys.argv[1] if len(sys.argv) > 1 else "faculty"
    entries = get_existing_entries(pt)
    print("\nExisting entries:")
    for name in sorted(entries):
        print(f"  • {name}")
