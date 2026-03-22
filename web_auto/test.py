import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import shutil

chrome_path = shutil.which("chromium-browser")

options = Options()
options.binary_location = chrome_path
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--remote-debugging-port=9222")

driver = webdriver.Chrome(options=options)

driver.get(os.getenv("TEST_URL") or "https://google.com")

input("Press Enter to close...")
driver.quit()