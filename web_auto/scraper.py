import asyncio
import aiohttp
import json
import json
import time
import shutil
import os
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

def require_env(var):
    value = os.getenv(var)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {var}")
    return value

LOGIN_URL = require_env("LOGIN_URL")
CREATE_PAGE = require_env("CREATE_PAGE")
DEPT_HOME = require_env("DEPT_HOME")
EMAIL = require_env("EMAIL")
PASSWORD = require_env("PASSWORD")


# -----------------------------
# CHROME SETUP
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

email = wait.until(
    EC.presence_of_element_located((By.NAME, "email"))
)

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
# DETECT TABS (CMS REQUIREMENTS)
# -----------------------------

tabs = driver.find_elements(By.XPATH, "//*[text()='About' or text()='People' or text()='DAB' or text()='PAC']")

required_sections = set()

for tab in tabs:
    required_sections.add(tab.text.lower())

print("\n✔ CMS requires sections:")
print(required_sections)


# -----------------------------
# DISCOVER WEBSITE LINKS
# -----------------------------

HEADERS = {"User-Agent": "Mozilla/5.0"}

async def fetch(session, url):
    try:
        async with session.get(url, headers=HEADERS, timeout=20) as r:
            return await r.text()
    except:
        return None


async def discover_links():

    async with aiohttp.ClientSession() as session:

        html = await fetch(session, DEPT_HOME)

        soup = BeautifulSoup(html, "html.parser")

        links = set()

        for a in soup.find_all("a", href=True):

            href = a["href"]

            if "/department/EEE" in href:
                links.add(urljoin(DEPT_HOME, href))

        return links


# -----------------------------
# SCRAPE PAGE CONTENT
# -----------------------------

def extract_sections(html):

    soup = BeautifulSoup(html, "html.parser")

    sections = {}
    current = "introduction"

    sections[current] = []

    for tag in soup.find_all(["h1","h2","h3","h4","p","li"]):

        if tag.name in ["h1","h2","h3","h4"]:

            heading = tag.get_text(strip=True)

            if heading:
                current = heading
                sections[current] = []

        else:

            text = tag.get_text(strip=True)

            if text:
                sections[current].append(text)

    for k in sections:
        sections[k] = "\n".join(sections[k])

    return sections


async def scrape_page(session, url):

    html = await fetch(session, url)

    if not html:
        return None

    return extract_sections(html)


# -----------------------------
# MAIN SCRAPER
# -----------------------------

async def scrape_required_sections():

    discovered_links = await discover_links()

    print("\n✔ Discovered department pages:")
    print(discovered_links)

    section_map = {}

    for link in discovered_links:

        lower = link.lower()

        if "about" in lower and "about" in required_sections:
            section_map["about"] = link

        if "faculty" in lower and "people" in required_sections:
            section_map["people"] = link

        if "dab" in lower and "dab" in required_sections:
            section_map["dab"] = link

        if "pac" in lower and "pac" in required_sections:
            section_map["pac"] = link

    print("\n✔ Matched sections to URLs:")
    print(section_map)

    results = {}

    async with aiohttp.ClientSession() as session:

        for section, url in section_map.items():

            print("Scraping:", url)

            content = await scrape_page(session, url)

            results[section] = content

    return results


# -----------------------------
# RUN SCRAPER
# -----------------------------

data = asyncio.run(scrape_required_sections())

with open("department_structured.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print("\n✔ Data saved to department_structured.json")

input("\nPress ENTER to close browser...")