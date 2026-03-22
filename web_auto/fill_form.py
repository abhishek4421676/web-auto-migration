import json
import time
import shutil


from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv
import os

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))



LOGIN_URL = os.getenv("LOGIN_URL") or "https://christ-college.dev6.intersmarthosting.in/admin-portal/login"
CREATE_PAGE = os.getenv("CREATE_PAGE") or "https://christ-college.dev6.intersmarthosting.in/admin-portal/departments/create"
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

JSON_FILE = "department_structured.json"


# -----------------------------
# LOAD DATA
# -----------------------------

with open(JSON_FILE) as f:
    sections = json.load(f)


# -----------------------------
# BROWSER SETUP
# -----------------------------

chrome_path = shutil.which("chromium-browser") or shutil.which("chromium") or shutil.which("google-chrome")

options = Options()
options.binary_location = chrome_path

options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--remote-debugging-port=9222")

driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, 30)


# -----------------------------
# LOGIN
# -----------------------------

driver.get(LOGIN_URL)

email = wait.until(EC.presence_of_element_located((By.NAME, "email")))
password = driver.find_element(By.NAME, "password")

email.send_keys(EMAIL)
password.send_keys(PASSWORD)

driver.find_element(By.XPATH, "//button[contains(.,'Login')]").click()

wait.until(
    EC.presence_of_element_located((By.XPATH, "//a[contains(.,'Dashboard')]"))
)

print("✔ Logged in")


# -----------------------------
# OPEN CREATE PAGE
# -----------------------------

driver.get(CREATE_PAGE)

wait.until(
    EC.presence_of_element_located((By.TAG_NAME, "body"))
)

print("✔ Create page opened")

time.sleep(3)


# -----------------------------
# FILL NAME + SLUG
# -----------------------------

try:
    name = driver.find_element(By.NAME, "name")
    name.send_keys("Electrical and Electronics Engineering")
    print("✔ Name filled")
except:
    print("Name field not found")


try:
    slug = driver.find_element(By.NAME, "slug")
    slug.send_keys("eee")
    print("✔ Slug filled")
except:
    print("Slug field not found")


# -----------------------------
# BUILD ABOUT TEXT
# -----------------------------

about_text = ""

if "about" in sections:

    for k, v in sections["about"].items():
        about_text += k + "\n" + v + "\n\n"


# -----------------------------
# FILL EDITOR
# -----------------------------

try:

    iframe = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "iframe"))
    )

    driver.switch_to.frame(iframe)

    editor = driver.find_element(By.TAG_NAME, "body")

    editor.clear()
    editor.send_keys(about_text)

    driver.switch_to.default_content()

    print("✔ About section filled")

except:
    print("Editor not found")


print("\n✔ Form filled")
print("✔ Script did NOT submit")

input("\nPress ENTER to close browser...")