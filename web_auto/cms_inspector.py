"""
Stage 1 – CMS Form Inspector
Opens a CMS page, clicks every tab, and records every visible form
field (text, file, select, textarea, rich‑text editors, checkboxes,
radios).  Returns the schema dict and optionally saves it to disk.

Can use a shared Selenium driver or create its own.
"""

import json
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from browser import get_driver, login
from config import CREATE_PAGE, FORM_SCHEMA_FILE


# ─── helpers ──────────────────────────────────────────────────

def _label_for(driver, element):
    """Best‑effort label resolution for a form element."""
    # 1. <label for="id">
    eid = element.get_attribute("id")
    if eid:
        try:
            lbl = driver.find_element(By.CSS_SELECTOR, f'label[for="{eid}"]')
            text = lbl.text.strip()
            if text:
                return text
        except Exception:
            pass

    # 2. label inside same parent wrapper
    try:
        parent = element.find_element(By.XPATH,
            "./ancestor::*[contains(@class,'form-group') "
            "or contains(@class,'field') "
            "or contains(@class,'mb-3')][1]"
        )
        for lbl in parent.find_elements(By.TAG_NAME, "label"):
            text = lbl.text.strip()
            if text:
                return text
    except Exception:
        pass

    # 3. preceding label anywhere
    try:
        lbl = element.find_element(By.XPATH, "preceding::label[1]")
        text = lbl.text.strip()
        if text:
            return text
    except Exception:
        pass

    # 4. placeholder
    ph = element.get_attribute("placeholder")
    return ph.strip() if ph else ""


# ─── main inspection logic ───────────────────────────────────

def inspect_form(driver, url=None):
    """Inspect every form field on *url* (defaults to CREATE_PAGE).

    Returns a schema dict with keys: page_url, tabs, fields, total_fields.
    """
    target = url or CREATE_PAGE
    wait = WebDriverWait(driver, 15)

    driver.get(target)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(3)

    # ── discover & click all tabs ────────────────────────────
    tab_sel = (
        '[role="tab"], .nav-link, .tab-link, '
        '[data-toggle="tab"], [data-bs-toggle="tab"]'
    )
    tabs = driver.find_elements(By.CSS_SELECTOR, tab_sel)
    tab_names = []
    for tab in tabs:
        t = tab.text.strip()
        if t:
            tab_names.append(t)
            try:
                tab.click()
                time.sleep(1)
            except Exception:
                pass
    # go back to first tab
    if tabs:
        try:
            tabs[0].click()
            time.sleep(1)
        except Exception:
            pass

    fields = []

    # ── regular inputs (text, email, number, date, file …) ──
    # exclude hidden / submit / button / checkbox / radio (handled later)
    inputs = driver.find_elements(
        By.CSS_SELECTOR,
        "input:not([type='hidden'])"
        ":not([type='submit'])"
        ":not([type='button'])"
        ":not([type='checkbox'])"
        ":not([type='radio'])"
    )
    for inp in inputs:
        itype = inp.get_attribute("type") or "text"
        name  = inp.get_attribute("name") or ""
        fid   = inp.get_attribute("id") or ""
        label = _label_for(driver, inp)
        req   = inp.get_attribute("required") is not None
        ph    = inp.get_attribute("placeholder") or ""

        info = {
            "name": name, "id": fid, "type": itype,
            "label": label, "required": req, "placeholder": ph,
        }
        if itype == "file":
            info["accept"]   = inp.get_attribute("accept") or ""
            info["multiple"] = inp.get_attribute("multiple") is not None
        fields.append(info)

    # ── textareas ────────────────────────────────────────────
    for ta in driver.find_elements(By.TAG_NAME, "textarea"):
        fields.append({
            "name":     ta.get_attribute("name") or "",
            "id":       ta.get_attribute("id") or "",
            "type":     "textarea",
            "label":    _label_for(driver, ta),
            "required": ta.get_attribute("required") is not None,
        })

    # ── select dropdowns ────────────────────────────────────
    for sel in driver.find_elements(By.TAG_NAME, "select"):
        opts = []
        for opt in sel.find_elements(By.TAG_NAME, "option"):
            opts.append({
                "value": opt.get_attribute("value") or "",
                "text":  opt.text.strip(),
            })
        fields.append({
            "name":     sel.get_attribute("name") or "",
            "id":       sel.get_attribute("id") or "",
            "type":     "select",
            "label":    _label_for(driver, sel),
            "required": sel.get_attribute("required") is not None,
            "options":  opts,
        })

    # ── rich‑text editors (TinyMCE / CKEditor iframes) ──────
    editor_iframes = driver.find_elements(
        By.CSS_SELECTOR,
        "iframe.tox-edit-area__iframe, "
        "iframe.cke_wysiwyg_frame"
    )
    # fallback: if no specific editor classes, try any iframe
    if not editor_iframes:
        editor_iframes = driver.find_elements(By.TAG_NAME, "iframe")

    for idx, iframe in enumerate(editor_iframes):
        label = ""
        textarea_name = ""
        try:
            wrapper = iframe.find_element(
                By.XPATH,
                "./ancestor::*[contains(@class,'form-group') "
                "or contains(@class,'field') "
                "or contains(@class,'mb-3')][1]"
            )
            lbls = wrapper.find_elements(By.TAG_NAME, "label")
            if lbls:
                label = lbls[0].text.strip()
            tas = wrapper.find_elements(By.TAG_NAME, "textarea")
            if tas:
                textarea_name = tas[0].get_attribute("name") or ""
        except Exception:
            pass

        fields.append({
            "name":        textarea_name or f"editor_{idx}",
            "id":          iframe.get_attribute("id") or "",
            "type":        "rich_text_editor",
            "label":       label,
            "required":    False,
            "editor_type": "iframe",
        })

    # ── checkboxes & radios ──────────────────────────────────
    for chk in driver.find_elements(
        By.CSS_SELECTOR, "input[type='checkbox'], input[type='radio']"
    ):
        fields.append({
            "name":     chk.get_attribute("name") or "",
            "id":       chk.get_attribute("id") or "",
            "type":     chk.get_attribute("type"),
            "label":    _label_for(driver, chk),
            "value":    chk.get_attribute("value") or "",
            "required": chk.get_attribute("required") is not None,
        })

    schema = {
        "page_url":     target,
        "tabs":         tab_names,
        "fields":       fields,
        "total_fields": len(fields),
    }
    return schema


# ─── entry point ─────────────────────────────────────────────

def run(driver=None, url=None, save_path=None):
    """Inspect the CMS form and return the schema dict.

    Parameters
    ----------
    driver : WebDriver, optional
        Shared browser session (must already be logged in).
        If None a fresh driver is created and quit when done.
    url : str, optional
        CMS page to inspect.  Defaults to CREATE_PAGE.
    save_path : str, optional
        File path to save the JSON schema.  Defaults to FORM_SCHEMA_FILE.
        Pass False to skip saving entirely.
    """
    own_driver = driver is None
    if own_driver:
        driver = get_driver()
        login(driver)

    try:
        schema = inspect_form(driver, url=url)

        # persist unless caller opts out
        out = save_path if save_path is not None else FORM_SCHEMA_FILE
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2, ensure_ascii=False)
            print(f"\n✔ Form schema saved  →  {out}")

        print(f"  Total fields : {schema['total_fields']}")
        print(f"  Tabs         : {schema['tabs']}")
        for fld in schema["fields"]:
            print(f"  - {fld['type']:20s} | name={fld['name']:20s} | label={fld['label']}")

        return schema
    finally:
        if own_driver:
            driver.quit()


if __name__ == "__main__":
    run()
