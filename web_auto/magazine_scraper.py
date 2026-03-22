"""
Magazine Scraper – Scrapes newsletter/magazine data from the source website.

Source page: {SOURCE_URL}/newsletters
Extracts per magazine: title, PDF URL, cover image URL.
Downloads cover images to assets/.
Skips magazines already in CMS (via cms_checker).

CMS fields (from discovery):
  - title (text)
  - url (url)           — link to the PDF
  - sort_order (number)
  - image_alt_text (text)
  - image (file)        — cover image
  - status (select)
  - description (textarea)
"""

import asyncio
import json
import os
import re

import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin

import config
from config import ASSETS_DIR, MAGAZINE_DATA_FILE
from compress import compress_image

HEADERS = {"User-Agent": "Mozilla/5.0"}
os.makedirs(ASSETS_DIR, exist_ok=True)


def _safe_filename(name, ext=".jpg"):
    """Sanitise a string into a filename-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)[:80]
    return f"magazine_{slug}{ext}"


async def _download_image(session, url, name):
    """Download a magazine cover image to assets/. Returns local path or None."""
    if not url:
        return None
    try:
        async with session.get(
            url, headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            if r.status != 200:
                print(f"    ✘ HTTP {r.status} for image: {url}")
                return None
            ct = r.content_type or ""
            if "png" in ct:
                ext = ".png"
            elif "webp" in ct:
                ext = ".webp"
            elif "gif" in ct:
                ext = ".gif"
            else:
                ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
            filename = _safe_filename(name, ext)
            filepath = os.path.join(ASSETS_DIR, filename)
            data = await r.read()
            with open(filepath, "wb") as f:
                f.write(data)
            print(f"    ✔ Image downloaded → assets/{filename} ({len(data)} bytes)")
            filepath = compress_image(filepath)
            return filepath
    except Exception as e:
        print(f"    ✘ Image download failed: {e}")
        return None


async def scrape_magazines(skip_titles=None):
    """
    Scrape magazine/newsletter listing from {SOURCE_URL}/newsletters.

    Parameters
    ----------
    skip_titles : set[str], optional
        Lowercased titles already in CMS to skip.

    Returns
    -------
    list[dict]
        Each dict has keys matching CMS fields:
        title, url, sort_order, image_alt_text, image, status, description
    """
    skip_titles = skip_titles or set()
    newsletters_url = config.SOURCE_URL.rstrip("/") + "/newsletters"
    records = []

    async with aiohttp.ClientSession() as session:
        print(f"\n⏳ Fetching newsletters page: {newsletters_url}")
        try:
            async with session.get(
                newsletters_url, headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status != 200:
                    print(f"  ✘ HTTP {r.status}")
                    return []
                html = await r.text()
        except Exception as e:
            print(f"  ✘ Failed to fetch: {e}")
            return []

        soup = BeautifulSoup(html, "html.parser")

        # Find the content section
        content = soup.find("section", id="content")
        if not content:
            content = soup

        magazines = []

        # Pattern: Each magazine is an <a> linking to a PDF, containing
        # an <img> (cover image) and a heading (title).
        # From the source page structure:
        #   <a href="cloudfront...pdf">
        #     <img src="NewsLettersImages/..." />
        #     <h5>Volume 2 Issue 2</h5>  (or similar heading)
        #   </a>

        # Find all links to PDFs
        pdf_links = content.find_all("a", href=re.compile(
            r"\.(pdf|PDF)", re.IGNORECASE
        ))

        if pdf_links:
            print(f"  Found {len(pdf_links)} PDF links")
            for link in pdf_links:
                mag_data = _extract_from_pdf_link(link)
                if mag_data:
                    magazines.append(mag_data)

        if not magazines:
            # Fallback: look for card-like structures with images + headings
            print("  ⚠ No PDF links found, trying card-based extraction")
            magazines = _extract_from_cards(content)

        if not magazines:
            # Last resort: look for heading + link patterns
            print("  ⚠ No cards found, trying heading-based extraction")
            magazines = _extract_from_headings(content)

        # Deduplicate by title
        seen = set()
        unique = []
        for mag in magazines:
            title_key = mag["title"].lower().strip()
            if title_key not in seen:
                seen.add(title_key)
                unique.append(mag)
        magazines = unique

        print(f"  Found {len(magazines)} unique magazines/newsletters")

        # Filter out already-existing entries
        new_mags = []
        for mag in magazines:
            title_lower = mag["title"].lower().strip()
            if title_lower in skip_titles:
                print(f"  ⏭ Skipping (already in CMS): {mag['title']}")
            else:
                new_mags.append(mag)

        print(f"  {len(new_mags)} new magazines to add "
              f"({len(magazines) - len(new_mags)} skipped)")

        # Download cover images and build final records
        for i, mag in enumerate(new_mags, 1):
            print(f"\n  [{i}/{len(new_mags)}] {mag['title']}")
            image_path = await _download_image(
                session, mag.get("image_url", ""), mag["title"]
            )
            records.append({
                "title": mag["title"],
                "url": mag.get("pdf_url", ""),
                "sort_order": str(i),
                "image_alt_text": mag["title"],
                "image": image_path or "",
                "status": "1",  # Enabled
                "description": mag.get("description", ""),
            })

    return records


def _extract_from_pdf_link(link_tag):
    """Extract magazine data from an <a> tag linking to a PDF."""
    pdf_url = link_tag.get("href", "")
    if not pdf_url.startswith("http"):
        pdf_url = urljoin(config.SOURCE_URL, pdf_url)

    # Find cover image inside the link
    img = link_tag.find("img")
    image_url = ""
    if img:
        image_url = img.get("src", "") or img.get("data-src", "")
        if image_url and not image_url.startswith("http"):
            image_url = urljoin(config.SOURCE_URL, image_url)

    # Find the title — usually in a heading tag near the link
    title = ""
    
    # Check inside the link for headings
    heading = link_tag.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    if heading:
        title = heading.get_text(strip=True)
    
    if not title:
        # Check the next sibling for a heading
        next_sib = link_tag.find_next_sibling(["h1", "h2", "h3", "h4", "h5", "h6"])
        if next_sib:
            title = next_sib.get_text(strip=True)

    if not title:
        # Try text content of the link itself (excluding img alt text)
        all_text = link_tag.get_text(strip=True)
        # Remove common non-title text
        all_text = re.sub(r'hero_img', '', all_text).strip()
        if all_text and len(all_text) > 2:
            title = all_text

    if not title:
        # Derive title from PDF filename
        fname = os.path.basename(pdf_url).rsplit(".", 1)[0]
        title = fname.replace("_", " ").replace("-", " ").title()

    # Skip non-magazine links (e.g. faculty list PDFs)
    skip_patterns = ["faculty_list", "faculty-list", "mandatory",
                     "approval", "regulation"]
    if any(p in pdf_url.lower() for p in skip_patterns):
        return None

    return {
        "title": title,
        "pdf_url": pdf_url,
        "image_url": image_url,
        "description": "",
    }


def _extract_from_cards(soup):
    """Extract magazines from card-like div structures."""
    magazines = []
    # Look for divs containing both an image and a heading
    for div in soup.find_all("div"):
        img = div.find("img")
        heading = div.find(["h3", "h4", "h5", "h6"])
        if img and heading:
            image_url = img.get("src", "") or img.get("data-src", "")
            if image_url and not image_url.startswith("http"):
                image_url = urljoin(config.SOURCE_URL, image_url)
            
            title = heading.get_text(strip=True)
            pdf_link = div.find("a", href=re.compile(r"\.pdf", re.IGNORECASE))
            pdf_url = ""
            if pdf_link:
                pdf_url = pdf_link.get("href", "")
                if not pdf_url.startswith("http"):
                    pdf_url = urljoin(config.SOURCE_URL, pdf_url)

            if title and len(title) > 2:
                magazines.append({
                    "title": title,
                    "pdf_url": pdf_url,
                    "image_url": image_url,
                    "description": "",
                })
    return magazines


def _extract_from_headings(soup):
    """Fallback: extract from heading tags that look like magazine titles."""
    magazines = []
    headings = soup.find_all(["h3", "h4", "h5"])
    
    skip_headings = {
        "newsletters & magazines", "departments", "gallery",
        "contact us", "quick links", "results", "other",
        "faculty & staff",
    }

    for h in headings:
        title = h.get_text(strip=True)
        if title.lower() in skip_headings or len(title) < 3:
            continue
        
        # Look for a nearby PDF link
        parent = h.find_parent(["a", "div", "li"])
        pdf_url = ""
        image_url = ""
        if parent:
            pdf_link = parent.find("a", href=re.compile(r"\.pdf", re.IGNORECASE))
            if pdf_link:
                pdf_url = pdf_link.get("href", "")
                if not pdf_url.startswith("http"):
                    pdf_url = urljoin(config.SOURCE_URL, pdf_url)
            img = parent.find("img")
            if img:
                image_url = img.get("src", "") or img.get("data-src", "")
                if image_url and not image_url.startswith("http"):
                    image_url = urljoin(config.SOURCE_URL, image_url)

        magazines.append({
            "title": title,
            "pdf_url": pdf_url,
            "image_url": image_url,
            "description": "",
        })
    return magazines


def run(skip_titles=None):
    """Entry point: scrape magazines and save to JSON."""
    records = asyncio.run(scrape_magazines(skip_titles))

    with open(MAGAZINE_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"\n✔ Magazine data saved → {MAGAZINE_DATA_FILE}")
    print(f"  Total new records: {len(records)}")

    for i, rec in enumerate(records, 1):
        print(f"  {i}. {rec['title']} — {rec['url'][:60]}")

    return records


if __name__ == "__main__":
    run()
