"""
Stage 4 – Form Filler
Reads scraped_data.json + form_schema.json, then fills every CMS
field — including uploading downloaded images/files via
<input type="file">.send_keys(absolute_path).
Does NOT submit the form; waits for manual review.

Also supports batch mode for faculty/magazine: loops through a list of
records, filling the CMS form for each, pausing for manual submit.
"""

import json
import os
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

from browser import get_driver, login
from config import (
    CREATE_PAGE, FORM_SCHEMA_FILE, SCRAPED_DATA_FILE, ASSETS_DIR,
    CMS_URLS,
)


# ─── per‑type fill helpers ───────────────────────────────────

def _fill_text(driver, name, value):
    try:
        el = driver.find_element(By.NAME, name)
        el.clear()
        el.send_keys(str(value))
        print(f"  ✔ text        {name}")
        return True
    except Exception as e:
        print(f"  ✘ text        {name}  →  {e}")
        return False


def _fill_textarea(driver, name, value):
    try:
        el = driver.find_element(By.NAME, name)
        el.clear()
        el.send_keys(str(value))
        print(f"  ✔ textarea    {name}")
        return True
    except Exception as e:
        print(f"  ✘ textarea    {name}  →  {e}")
        return False


def _fill_select(driver, name, value):
    try:
        el = driver.find_element(By.NAME, name)
        sel = Select(el)
        try:
            sel.select_by_value(str(value))
        except Exception:
            try:
                sel.select_by_visible_text(str(value))
            except Exception:
                # For multi-selects like assigned_magazines[], try selecting
                # each comma-separated value, or select first non-empty option
                options = [o for o in sel.options if o.get_attribute("value")]
                if options:
                    options[0].click()
                    print(f"  ✔ select      {name} = {options[0].text} (first option)")
                    return True
                raise
        print(f"  ✔ select      {name} = {value}")
        return True
    except Exception as e:
        print(f"  ✘ select      {name}  →  {e}")
        return False


def _fill_file(driver, name, filepath):
    """Upload a file to <input type='file'>. Works even if the input is hidden."""
    if not filepath or not os.path.isfile(filepath):
        print(f"  ✘ file        {name}  →  file not found: {filepath}")
        return False

    abs_path = os.path.abspath(filepath)

    try:
        # try by name first
        file_inputs = driver.find_elements(
            By.CSS_SELECTOR, f'input[type="file"][name="{name}"]'
        )
        if not file_inputs:
            # broaden: any visible file input
            file_inputs = driver.find_elements(
                By.CSS_SELECTOR, 'input[type="file"]'
            )
        if not file_inputs:
            print(f"  ✘ file        {name}  →  no <input type=file> found")
            return False

        el = file_inputs[0]

        # make the input interactable (many CMS hide it behind a button)
        driver.execute_script(
            """
            var el = arguments[0];
            el.style.display    = 'block';
            el.style.visibility = 'visible';
            el.style.opacity    = '1';
            el.style.height     = 'auto';
            el.style.width      = 'auto';
            el.style.position   = 'relative';
            el.removeAttribute('hidden');
            """,
            el,
        )
        time.sleep(0.3)

        el.send_keys(abs_path)
        time.sleep(2)  # wait for crop modal to appear

        # Pause for manual cropping — user handles the crop dialog
        _wait_for_manual_crop(driver)

        print(f"  ✔ file        {name}  →  {os.path.basename(abs_path)}")
        return True
    except Exception as e:
        print(f"  ✘ file        {name}  →  {e}")
        return False


def _wait_for_manual_crop(driver):
    """Check if a crop dialog appeared and wait for user to handle it."""
    try:
        # Detect if a crop modal is visible
        buttons = driver.find_elements(By.XPATH,
            "//button[contains(text(),'Crop')]"
        )
        modal_visible = any(btn.is_displayed() for btn in buttons)
        if modal_visible:
            print(f"    ⚠ CROP DIALOG detected — please crop the image manually in the browser.")
            input(f"    Press ENTER after cropping … ")
            time.sleep(1)
        else:
            # No crop dialog, continue
            pass
    except Exception:
        pass


def _fill_rich_text(driver, name, html_content, field_info):
    """Inject HTML into TinyMCE using the TinyMCE API.
    For tables, uses mceInsertTable + cell filling so the table
    tool registers the table properly."""
    fid = field_info.get("id", "")

    try:
        # ---- approach 1: TinyMCE JS API (preferred) ----
        # Try using tinymce.get(id).setContent() which properly
        # processes tables through TinyMCE's pipeline.
        if fid:
            has_table = "<table" in html_content.lower()

            if has_table:
                # Separate text content from table content, insert
                # text first via setContent, then insert table via
                # the TinyMCE table plugin command + fill cells.
                ok = driver.execute_script("""
                    var editorId = arguments[0];
                    var fullHtml = arguments[1];

                    // parse the HTML to separate text and tables
                    var parser = new DOMParser();
                    var doc = parser.parseFromString(fullHtml, 'text/html');
                    var tables = doc.querySelectorAll('table');
                    var textParts = [];

                    // collect non-table elements
                    for (var child of doc.body.children) {
                        if (child.tagName !== 'TABLE') {
                            textParts.push(child.outerHTML);
                        }
                    }

                    var editor = tinymce.get(editorId);
                    if (!editor) {
                        // try without _ifr suffix if id has it
                        var altId = editorId.replace(/_ifr$/, '');
                        editor = tinymce.get(altId);
                    }
                    if (!editor) return false;

                    // set text content first
                    editor.setContent(textParts.join(''));

                    // for each table, use mceInsertTable to create it properly
                    for (var t = 0; t < tables.length; t++) {
                        var table = tables[t];
                        var rows = table.querySelectorAll('tr');
                        var numRows = rows.length;
                        if (numRows === 0) continue;

                        var numCols = 0;
                        // count columns from first row (th or td)
                        var firstRowCells = rows[0].querySelectorAll('th, td');
                        numCols = firstRowCells.length;
                        if (numCols === 0) continue;

                        // move cursor to end
                        editor.selection.select(editor.getBody(), true);
                        editor.selection.collapse(false);

                        // insert table via the table plugin
                        editor.execCommand('mceInsertTable', false, {
                            rows: numRows,
                            columns: numCols
                        });

                        // now fill the cells of the just-inserted table
                        var insertedTables = editor.getBody().querySelectorAll('table');
                        var lastTable = insertedTables[insertedTables.length - 1];
                        if (lastTable) {
                            var newRows = lastTable.querySelectorAll('tr');
                            for (var r = 0; r < rows.length && r < newRows.length; r++) {
                                var srcCells = rows[r].querySelectorAll('th, td');
                                var dstCells = newRows[r].querySelectorAll('th, td');
                                for (var c = 0; c < srcCells.length && c < dstCells.length; c++) {
                                    dstCells[c].innerHTML = srcCells[c].innerHTML;
                                }
                            }
                        }
                    }

                    return true;
                """, fid, html_content)

                if ok:
                    print(f"  ✔ rich_text   {name} (table via TinyMCE table tool)")
                    return True
            else:
                # no table — just use setContent directly
                ok = driver.execute_script("""
                    var editor = tinymce.get(arguments[0]);
                    if (!editor) {
                        var altId = arguments[0].replace(/_ifr$/, '');
                        editor = tinymce.get(altId);
                    }
                    if (!editor) return false;
                    editor.setContent(arguments[1]);
                    return true;
                """, fid, html_content)

                if ok:
                    print(f"  ✔ rich_text   {name}")
                    return True

        # ---- approach 2: iframe innerHTML fallback ----
        iframes = []
        if fid:
            for suffix in ["_ifr", ""]:
                try:
                    iframe = driver.find_element(By.ID, fid + suffix)
                    iframes = [iframe]
                    break
                except Exception:
                    pass

        if not iframes:
            iframes = driver.find_elements(
                By.CSS_SELECTOR,
                "iframe.tox-edit-area__iframe, iframe.cke_wysiwyg_frame",
            )

        for iframe in iframes:
            try:
                driver.switch_to.frame(iframe)
                driver.execute_script(
                    "document.body.innerHTML = arguments[0];", html_content
                )
                driver.switch_to.default_content()
                print(f"  ✔ rich_text   {name} (iframe fallback)")
                return True
            except Exception:
                driver.switch_to.default_content()

        print(f"  ✘ rich_text   {name}  →  no editor found")
        return False
    except Exception as e:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        print(f"  ✘ rich_text   {name}  →  {e}")
        return False


def _fill_check_radio(driver, name, value):
    try:
        if value:
            el = driver.find_element(
                By.CSS_SELECTOR,
                f'input[name="{name}"][value="{value}"]',
            )
        else:
            el = driver.find_element(By.NAME, name)
        if not el.is_selected():
            el.click()
        print(f"  ✔ check/radio {name}")
        return True
    except Exception as e:
        print(f"  ✘ check/radio {name}  →  {e}")
        return False


def _clean_value(value):
    """Strip markdown link formatting like [text](url) → text."""
    if isinstance(value, str):
        value = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', value)
    return value


# ─── main fill routine ───────────────────────────────────────

def run(driver=None, url=None, schema=None, data=None):
    """Fill the CMS department form.

    Parameters
    ----------
    driver : WebDriver, optional
        Shared browser (must be logged in).  Creates its own if None.
    url : str, optional
        CMS page to fill.  Defaults to CREATE_PAGE.
    schema : dict, optional
        Form schema dict.  If None, loads from FORM_SCHEMA_FILE.
    data : dict, optional
        Scraped data dict.  If None, loads from SCRAPED_DATA_FILE.
    """
    if schema is None:
        with open(FORM_SCHEMA_FILE) as f:
            schema = json.load(f)
    if data is None:
        with open(SCRAPED_DATA_FILE) as f:
            data = json.load(f)

    target = url or CREATE_PAGE
    field_lookup = {fld["name"]: fld for fld in schema["fields"]}
    print(
        f"✔ Loaded schema ({schema['total_fields']} fields) "
        f"+ scraped data ({len(data)} values)"
    )

    own_driver = driver is None
    if own_driver:
        driver = get_driver()
        login(driver)
    try:
        wait = WebDriverWait(driver, 15)

        driver.get(target)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(3)

        # discover tabs so we can click them to reveal fields
        tab_sel = (
            '[role="tab"], .nav-link, .tab-link, '
            '[data-toggle="tab"], [data-bs-toggle="tab"]'
        )
        tabs = driver.find_elements(By.CSS_SELECTOR, tab_sel)

        filled = 0
        failed = 0

        print("\n⏳ Filling form …\n")

        for field_name, value in data.items():
            if not value:
                continue

            value = _clean_value(value)

            info  = field_lookup.get(field_name, {})
            ftype = info.get("type", "text")

            # try clicking tabs until the field is accessible
            for tab in tabs:
                try:
                    tab.click()
                    time.sleep(0.4)
                    driver.find_element(By.NAME, field_name)
                    break
                except Exception:
                    continue

            # dispatch by field type
            if ftype == "file":
                ok = _fill_file(driver, field_name, value)
            elif ftype == "rich_text_editor":
                ok = _fill_rich_text(driver, field_name, value, info)
            elif ftype == "select":
                ok = _fill_select(driver, field_name, value)
            elif ftype in ("checkbox", "radio"):
                ok = _fill_check_radio(driver, field_name, value)
            elif ftype == "textarea":
                ok = _fill_textarea(driver, field_name, value)
            else:
                ok = _fill_text(driver, field_name, value)

            if ok:
                filled += 1
            else:
                failed += 1

        print(f"\n✔ Form filling complete — {filled} filled, {failed} failed")
        print("✔ Script did NOT submit the form")

        print("\n  ⚠ REVIEW the form, crop images, then SUBMIT manually.")
        input("\n  Press ENTER after submitting … ")
    finally:
        if own_driver:
            driver.quit()


# ─── batch fill for faculty / magazine ────────────────────────

# CMS field type mappings for faculty and magazine forms
FIELD_TYPES = {
    "faculty": {
        "name": "text",
        "designation": "text",
        "sort_order": "text",
        "image_alt_text": "text",
        "image": "file",
        "status": "select",
    },
    "magazine": {
        "title": "text",
        "url": "text",
        "sort_order": "text",
        "image_alt_text": "text",
        "image": "file",
        "status": "select",
        "description": "textarea",
    },
}


def _fill_one_record(driver, record, field_types):
    """Fill a single CMS form with one record's data. Returns (filled, failed)."""
    filled = 0
    failed = 0

    for field_name, value in record.items():
        if not value:
            continue

        value = _clean_value(value)
        ftype = field_types.get(field_name, "text")

        if ftype == "file":
            ok = _fill_file(driver, field_name, value)
        elif ftype == "rich_text_editor":
            ok = _fill_rich_text(driver, field_name, value,
                                 {"id": field_name})
        elif ftype == "select":
            ok = _fill_select(driver, field_name, value)
        elif ftype in ("checkbox", "radio"):
            ok = _fill_check_radio(driver, field_name, value)
        elif ftype == "textarea":
            ok = _fill_textarea(driver, field_name, value)
        else:
            ok = _fill_text(driver, field_name, value)

        if ok:
            filled += 1
        else:
            failed += 1

    return filled, failed


def run_batch(page_type, records, driver=None):
    """
    Fill the CMS form for each record in the list, pausing for
    manual review + submit between each one.

    Parameters
    ----------
    page_type : str
        "faculty" or "magazine"
    records : list[dict]
        List of scraped records to fill into CMS.
    driver : WebDriver, optional
        If provided, reuses this driver (must already be logged in).
        If None, creates a new driver + logs in, then quits when done.
    """
    if not records:
        print("✔ No records to fill.")
        return

    create_url = CMS_URLS[page_type]["create"]
    field_types = FIELD_TYPES.get(page_type, {})
    total = len(records)

    print(f"\n{'='*60}")
    print(f"  BATCH FILL — {total} {page_type} records")
    print(f"  CMS create page: {create_url}")
    print(f"{'='*60}")

    own_driver = driver is None
    if own_driver:
        driver = get_driver()
        login(driver)
    try:
        wait = WebDriverWait(driver, 15)

        for i, record in enumerate(records, 1):
            print(f"\n{'─'*60}")
            # Display record summary
            display_name = (record.get("name") or record.get("title")
                           or f"Record {i}")
            print(f"  [{i}/{total}] {display_name}")
            print(f"{'─'*60}")

            # Show all fields that will be filled
            for k, v in record.items():
                display_v = str(v)
                if len(display_v) > 80:
                    display_v = display_v[:77] + "..."
                print(f"    {k}: {display_v}")

            # Navigate to create page
            driver.get(create_url)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(3)

            # Fill the form
            print(f"\n  ⏳ Filling form …\n")
            filled, failed = _fill_one_record(driver, record, field_types)

            print(f"\n  ✔ Form filled — {filled} fields ok, {failed} failed")
            print(f"\n  ⚠ REVIEW the form in the browser, then SUBMIT manually.")

            if i < total:
                ans = input(f"\n  Press ENTER after submitting to continue "
                           f"to record {i+1}/{total} "
                           f"(or type 'skip' / 'quit'): ").strip().lower()
                if ans == "quit":
                    print(f"\n  ⏹ Stopped after {i-1} records.")
                    break
                elif ans == "skip":
                    print(f"  ⏭ Skipping …")
                    continue
                # Wait a moment for submit to process
                time.sleep(2)
            else:
                input(f"\n  Last record. Press ENTER after submitting "
                      f"to close browser: ")

        print(f"\n✔ Batch fill complete for {page_type}")
    finally:
        if own_driver:
            driver.quit()


if __name__ == "__main__":
    run()
