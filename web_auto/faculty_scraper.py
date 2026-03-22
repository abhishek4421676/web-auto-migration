"""
Faculty Scraper – Scrapes faculty data from the source website.

Source page: {SOURCE_URL}/faculty
Extracts per faculty member: name, designation, photo URL.
Downloads photos to assets/.
Skips faculty already in CMS (via cms_checker).

CMS fields (from discovery):
  - name (text)
  - designation (text)
  - sort_order (number)
  - image_alt_text (text)
  - image (file)
  - status (select)
"""

import asyncio
import json
import os
import re

import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin

import config
from config import ASSETS_DIR, FACULTY_DATA_FILE
from compress import compress_image

HEADERS = {"User-Agent": "Mozilla/5.0"}
os.makedirs(ASSETS_DIR, exist_ok=True)


def _safe_filename(name, ext=".jpg"):
    """Sanitise a string into a filename-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)[:80]
    return f"faculty_{slug}{ext}"


async def _download_photo(session, url, name):
    """Download a faculty photo to assets/. Returns local path or None."""
    if not url:
        return None
    try:
        async with session.get(
            url, headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            if r.status != 200:
                print(f"    ✘ HTTP {r.status} for photo: {url}")
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
            print(f"    ✔ Photo downloaded → assets/{filename} ({len(data)} bytes)")
            filepath = compress_image(filepath)
            return filepath
    except Exception as e:
        print(f"    ✘ Photo download failed: {e}")
        return None


async def scrape_faculty(skip_names=None):
    """
    Scrape faculty listing from {SOURCE_URL}/faculty.
    
    Parameters
    ----------
    skip_names : set[str], optional
        Lowercased names already in CMS to skip.
    
    Returns
    -------
    list[dict]
        Each dict has keys matching CMS fields:
        name, designation, sort_order, image_alt_text, image, status
    """
    skip_names = skip_names or set()
    faculty_url = config.SOURCE_URL.rstrip("/") + "/faculty"
    records = []

    async with aiohttp.ClientSession() as session:
        print(f"\n⏳ Fetching faculty page: {faculty_url}")
        try:
            async with session.get(
                faculty_url, headers=HEADERS,
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

        # Find the "Faculty & Staff" section
        # The faculty cards are inside <section id="content"> or similar
        content = soup.find("section", id="content")
        if not content:
            content = soup

        # Faculty cards are typically <a> tags linking to profile PDFs,
        # each containing an <img> (photo) and text (name, designation).
        # They may also be in <div> cards. Let's find all patterns.
        
        faculty_cards = []
        
        # Pattern 1: Look for structured card containers
        # Each faculty member has: image, name, designation
        # Try finding cards by common class patterns
        cards = content.find_all("div", recursive=True)
        
        # Better approach: find all <img> tags that are faculty photos
        # and walk up to their parent card containers
        images = content.find_all("img")
        
        # Also look for <a> tags with PDF profile links
        profile_links = content.find_all("a", href=re.compile(
            r"faculty_profile|faculty-profile|profile", re.IGNORECASE
        ))
        
        if profile_links:
            # Each profile link wraps a faculty card
            print(f"  Found {len(profile_links)} faculty profile links")
            for link in profile_links:
                card_data = _extract_from_link_card(link)
                if card_data:
                    faculty_cards.append(card_data)
        
        if not faculty_cards:
            # Pattern 2: Look for repeating card structures
            # Find all images that look like faculty photos (not logos/icons)
            for img in images:
                src = img.get("src", "")
                alt = img.get("alt", "")
                # Skip tiny icons, logos, gallery images
                if any(skip in src.lower() for skip in 
                       ["logo", "icon", "gallery", "happenings", "banner",
                        "hero", "slide", "static/images"]):
                    continue
                # Look for faculty-related images
                if "faculty" in src.lower() or "staff" in src.lower() or (
                    "media" in src.lower() and "profile" not in src.lower()
                ):
                    # Walk up to find the card container
                    parent = img.find_parent(["div", "a", "li", "article"])
                    if parent:
                        card_data = _extract_from_container(parent, img)
                        if card_data:
                            faculty_cards.append(card_data)

        if not faculty_cards:
            # Pattern 3: Parse text-based listing
            # Look for heading patterns like name + designation
            print("  ⚠ No card structure found, trying text-based extraction")
            faculty_cards = _extract_from_text_listing(content)

        # Deduplicate by name
        seen = set()
        unique_cards = []
        for card in faculty_cards:
            name_key = card["name"].lower().strip()
            if name_key not in seen:
                seen.add(name_key)
                unique_cards.append(card)
        faculty_cards = unique_cards

        print(f"  Found {len(faculty_cards)} unique faculty members")

        # Filter out already-existing entries
        new_cards = []
        for card in faculty_cards:
            name_lower = card["name"].lower().strip()
            if name_lower in skip_names:
                print(f"  ⏭ Skipping (already in CMS): {card['name']}")
            else:
                new_cards.append(card)

        print(f"  {len(new_cards)} new faculty to add "
              f"({len(faculty_cards) - len(new_cards)} skipped)")

        # Download photos and build final records
        for i, card in enumerate(new_cards, 1):
            print(f"\n  [{i}/{len(new_cards)}] {card['name']}")
            photo_path = await _download_photo(
                session, card.get("photo_url", ""), card["name"]
            )
            records.append({
                "name": card["name"],
                "designation": card.get("designation", ""),
                "sort_order": str(i),
                "image_alt_text": card["name"],
                "image": photo_path or "",
                "status": "1",  # Enabled
            })

    return records


def _extract_from_link_card(link_tag):
    """Extract faculty data from an <a> tag that wraps a faculty card."""
    img = link_tag.find("img")
    photo_url = ""
    if img:
        photo_url = img.get("src", "") or img.get("data-src", "")
        if photo_url and not photo_url.startswith("http"):
            photo_url = urljoin(config.SOURCE_URL, photo_url)

    # Get all text from the link
    text_parts = []
    for child in link_tag.descendants:
        if isinstance(child, str):
            t = child.strip()
            if t:
                text_parts.append(t)
        elif hasattr(child, 'get_text'):
            pass  # we'll get text from NavigableString children

    # Alternative: get structured text
    all_text = link_tag.get_text(separator="\n", strip=True)
    lines = [l.strip() for l in all_text.split("\n") if l.strip()]

    # Filter out non-name/designation lines
    lines = [l for l in lines if l.lower() not in (
        "hero_img", "download", "expand_more"
    ) and not l.startswith("AICTE")]

    name = ""
    designation = ""
    
    known_designations = [
        "hod", "professor", "associate professor", "assistant professor",
        "head of department", "lecturer", "senior lecturer",
        "lab instructor", "instructor", "office superintendent",
        "tradesman", "attender", "peon", "librarian",
    ]

    for line in lines:
        line_lower = line.lower().strip()
        # Check if this line is a designation
        is_designation = any(d in line_lower for d in known_designations)
        if is_designation and not designation:
            designation = line
        elif not name and not is_designation and len(line) > 2:
            # Skip hero_img alt text artifacts
            if line.lower() != "hero_img":
                name = line

    if not name:
        return None

    return {
        "name": name,
        "designation": designation,
        "photo_url": photo_url,
    }


def _extract_from_container(container, img_tag):
    """Extract faculty data from a card container div."""
    photo_url = img_tag.get("src", "") or img_tag.get("data-src", "")
    if photo_url and not photo_url.startswith("http"):
        photo_url = urljoin(config.SOURCE_URL, photo_url)

    all_text = container.get_text(separator="\n", strip=True)
    lines = [l.strip() for l in all_text.split("\n") if l.strip()]
    lines = [l for l in lines if l.lower() not in ("hero_img",)]

    name = ""
    designation = ""
    known_designations = [
        "hod", "professor", "associate professor", "assistant professor",
        "head of department", "lecturer",
    ]

    for line in lines:
        line_lower = line.lower().strip()
        is_designation = any(d in line_lower for d in known_designations)
        if is_designation and not designation:
            designation = line
        elif not name and not is_designation and len(line) > 2:
            name = line

    if not name:
        return None

    return {
        "name": name,
        "designation": designation,
        "photo_url": photo_url,
    }


def _extract_from_text_listing(soup):
    """Fallback: extract faculty from heading/paragraph patterns."""
    cards = []
    # Look for h3/h4/h5 elements that might be faculty names
    headings = soup.find_all(["h3", "h4", "h5"])
    
    known_designations = [
        "hod", "professor", "associate professor", "assistant professor",
        "head of department", "lecturer",
    ]
    
    i = 0
    while i < len(headings):
        text = headings[i].get_text(strip=True)
        # Skip non-name headings
        if text.lower() in ("faculty & staff", "gallery", "contact us",
                            "quick links", "results", "other", "departments"):
            i += 1
            continue
        
        # Check if next heading is a designation
        designation = ""
        if i + 1 < len(headings):
            next_text = headings[i + 1].get_text(strip=True)
            if any(d in next_text.lower() for d in known_designations):
                designation = next_text
                i += 1
        
        if len(text) > 2 and not any(d in text.lower() for d in known_designations):
            cards.append({
                "name": text,
                "designation": designation,
                "photo_url": "",
            })
        i += 1
    
    return cards


def run(skip_names=None):
    """Entry point: scrape faculty and save to JSON."""
    records = asyncio.run(scrape_faculty(skip_names))

    with open(FACULTY_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"\n✔ Faculty data saved → {FACULTY_DATA_FILE}")
    print(f"  Total new records: {len(records)}")

    for i, rec in enumerate(records, 1):
        print(f"  {i}. {rec['name']} — {rec['designation']}")

    return records


if __name__ == "__main__":
    run()
