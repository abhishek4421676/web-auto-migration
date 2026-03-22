"""Quick CMS discovery — inspect faculty + magazine create forms."""

import time
import json
import os
from dotenv import load_dotenv
from browser import get_driver, login

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

driver = get_driver()
try:
    login(driver)
    time.sleep(2)
    
    base = os.getenv("CMS_BASE")
    
    # Inspect faculty create page
    for page_type, list_url in [("faculty", f"{base}/faculties"), ("magazine", f"{base}/magazines")]:
        print(f"\n{'='*60}")
        print(f"  {page_type.upper()}")
        print(f"{'='*60}")
        
        # First check the list page for existing entries
        driver.get(list_url)
        time.sleep(3)
        
        # Find the create link
        from selenium.webdriver.common.by import By
        create_links = driver.find_elements(By.XPATH, 
            "//a[contains(@href,'create') or contains(text(),'Add') or contains(text(),'Create')]")
        create_url = None
        for cl in create_links:
            href = cl.get_attribute("href") or ""
            if "create" in href:
                create_url = href
                print(f"  Create URL: {href}")
                break
        
        if not create_url:
            # Try common pattern
            create_url = f"{list_url}/create"
            print(f"  Guessed create URL: {create_url}")
        
        # Check existing entries in the list
        print(f"\n  --- Existing entries ---")
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr, .list-group-item, .card")
        print(f"  Found {len(rows)} entries in list")
        for row in rows[:5]:
            print(f"    {row.text[:100]}")
        
        # Now inspect the create form
        driver.get(create_url)
        time.sleep(3)
        
        # Click all tabs
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        tabs = driver.find_elements(By.CSS_SELECTOR,
            '[role="tab"], .nav-link, .tab-link, [data-toggle="tab"], [data-bs-toggle="tab"]')
        for tab in tabs:
            try:
                tab.click()
                time.sleep(0.3)
            except:
                pass
        
        # Collect all form fields
        print(f"\n  --- Form fields ---")
        fields = []
        
        # text/email/tel/number inputs
        for inp in driver.find_elements(By.CSS_SELECTOR, "input:not([type='hidden']):not([type='file']):not([type='checkbox']):not([type='radio'])"):
            name = inp.get_attribute("name") or ""
            itype = inp.get_attribute("type") or "text"
            if name and not name.startswith("layout"):
                fields.append({"name": name, "type": itype})
                
        # file inputs
        for inp in driver.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            name = inp.get_attribute("name") or ""
            if name:
                fields.append({"name": name, "type": "file"})
        
        # selects
        for sel in driver.find_elements(By.CSS_SELECTOR, "select"):
            name = sel.get_attribute("name") or ""
            if name and not name.startswith("layout"):
                from selenium.webdriver.support.ui import Select
                s = Select(sel)
                options = [{"value": o.get_attribute("value"), "text": o.text} for o in s.options[:10]]
                fields.append({"name": name, "type": "select", "options": options})
        
        # textareas
        for ta in driver.find_elements(By.CSS_SELECTOR, "textarea"):
            name = ta.get_attribute("name") or ""
            tid = ta.get_attribute("id") or ""
            if name:
                # check if tinymce
                has_editor = len(driver.find_elements(By.CSS_SELECTOR, f"#{tid}_ifr")) > 0 if tid else False
                fields.append({"name": name, "type": "rich_text_editor" if has_editor else "textarea", "id": tid})
        
        for f in fields:
            print(f"    {f}")
        
        # Save schema
        schema = {"page_type": page_type, "total_fields": len(fields), "fields": fields}
        out_file = f"{page_type}_schema.json"
        with open(out_file, "w") as fp:
            json.dump(schema, fp, indent=2)
        print(f"\n  ✔ Saved {out_file} ({len(fields)} fields)")

finally:
    input("\nPress ENTER to close...")
    driver.quit()

