#!/usr/bin/env python3
"""
GTA Online HQ — Weekly Data Scraper v2
Uses BeautifulSoup HTML structure (h2/h3 tags) instead of plain text regex.
This is far more reliable — headings are unambiguous in HTML.
"""

import json
import re
import sys
import datetime
import requests
from bs4 import BeautifulSoup

# ── Sources ───────────────────────────────────────────────────────────
SOURCES = [
    "https://techwiser.com/gta-online-weekly-update/",
    "https://www.sportskeeda.com/gta/gta-online-weekly-update",
    "https://www.dexerto.com/gta/gta-online-weekly-update-patch-notes-1498644/",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Fetch ─────────────────────────────────────────────────────────────

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  [WARN] fetch failed for {url}: {e}")
        return None

# ── BeautifulSoup section helpers ─────────────────────────────────────

def get_section_text(soup, pattern):
    """
    Find the first h2/h3/h4 matching pattern.
    Return all text content until the next heading at the same or higher level.
    Stops cleanly — no bleeding into other sections.
    """
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        if re.search(pattern, heading.get_text(strip=True), re.IGNORECASE):
            level = int(heading.name[1])
            parts = []
            for sib in heading.find_next_siblings():
                if not sib.name:
                    continue
                if sib.name in ['h1','h2','h3','h4'] and int(sib.name[1]) <= level:
                    break
                parts.append(sib.get_text(separator='\n', strip=True))
            return '\n'.join(parts)
    return ''


def get_section_bullets(soup, pattern):
    """
    Find a section heading matching pattern.
    Return list items from the first ul/ol inside that section only.
    Stops at the next heading — no bleed.
    """
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        if re.search(pattern, heading.get_text(strip=True), re.IGNORECASE):
            level = int(heading.name[1])
            for sib in heading.find_next_siblings():
                if not sib.name:
                    continue
                if sib.name in ['h1','h2','h3','h4'] and int(sib.name[1]) <= level:
                    break
                if sib.name in ['ul', 'ol']:
                    return [li.get_text(strip=True) for li in sib.find_all('li')]
    return []


def clean(s):
    return re.sub(r'\s+', ' ', (s or '').replace('*', '')).strip()


def salvage_tier(name):
    n = name.lower()
    if re.search(r'cargo|ship|duggan|podium', n): return 3
    if re.search(r'mctony', n):                   return 2
    if re.search(r'gangbanger', n):               return 1
    return 2


# ── Main Parser ───────────────────────────────────────────────────────

def parse(html):
    soup = BeautifulSoup(html, 'html.parser')

    # Remove navigation, footer, sidebar, scripts — noise
    for tag in soup.find_all(['nav', 'footer', 'aside', 'script', 'style', 'header']):
        tag.decompose()

    # Remove table of contents (TOC links pollute challenge parsing)
    for el in soup.find_all(class_=re.compile(r'toc|table.of.content|ez-toc|wp-block-table-of-contents', re.I)):
        el.decompose()
    for el in soup.find_all(id=re.compile(r'toc|table.of.content', re.I)):
        el.decompose()

    data = {
        "weekLabel":   "",
        "challenge":   {"desc": "", "reward": ""},
        "bonuses":     [],
        "newVehicles": [],
        "discounts":   [],
        "podium":      "",
        "prizeRide":   "",
        "salvage":     [],
        "gunVan":      [],
        "fibFile":     "",
        "mostWanted":  [],
        "carMeet": {
            "prizeRide": "", "prizeReq": "",
            "premiumTest": "", "premiumTestNote": "",
            "testRides": [], "luxuryAutos": [], "pdm": [],
        },
    }

    full_text = soup.get_text(separator='\n')

    # ── Week label (from H1) ──────────────────────────────────────────
    h1 = soup.find('h1')
    if h1:
        m = re.search(r'\(([A-Z][a-z]+ \d+\s*[–\-]\s*(?:[A-Z][a-z]+ )?\d+,?\s*\d{4})\)', h1.get_text())
        if m:
            label = m.group(1)
            label = re.sub(r',?\s*\d{4}', '', label)
            label = re.sub(r'(\w{3})\w*\s+(\d+)', lambda x: x.group(1).upper() + ' ' + x.group(2), label)
            data['weekLabel'] = label.strip()

    # ── Weekly Challenge ─────────────────────────────────────────────
    # Uses section body ONLY — avoids TOC which has same heading text
    chal_body = get_section_text(soup, r'weekly\s+challenge')
    if chal_body:
        m = re.search(r'((?:Secure|Complete|Win|Earn)\s+[^.\n]{5,100})', chal_body, re.IGNORECASE)
        if m:
            data['challenge']['desc'] = clean(m.group(1))
        m = re.search(r'(?:receive|get|earn)\s+(?:the\s+)?([^\n]{5,120})', chal_body, re.IGNORECASE)
        if m:
            data['challenge']['reward'] = clean(m.group(1))
        # Fallback: first two non-empty lines
        if not data['challenge']['desc']:
            lines = [l.strip() for l in chal_body.split('\n') if len(l.strip()) > 10]
            if lines:
                data['challenge']['desc'] = clean(lines[0])
            if len(lines) > 1 and not data['challenge']['reward']:
                data['challenge']['reward'] = clean(lines[1])

    # ── Bonus Money ──────────────────────────────────────────────────
    # Each tier is extracted from its own section — stops at next heading
    for mult, pattern in [
        (4, r'quadruple.*(?:money|bonus)'),
        (3, r'triple.*(?:money|bonus)'),
        (2, r'double.*(?:money|bonus)'),
    ]:
        bullets = get_section_bullets(soup, pattern)
        if not bullets:
            # Some sites use plain paragraphs instead of lists
            body = get_section_text(soup, pattern)
            bullets = [l.strip().lstrip('-•* ') for l in body.split('\n')
                      if l.strip() and re.match(r'^[-•*]', l.strip())]

        for b in bullets:
            b = b.replace('**', '').strip()
            # Name = text before the first dash/colon separator
            name_m = re.match(r'^([^–\-:]{3,70}?)(?:\s*[-–:]|$)', b)
            if not name_m:
                continue
            name = clean(name_m.group(1))
            note_m = re.search(r'[-–:]\s*(.{5,80})', b)
            note = clean(note_m.group(1))[:60] if note_m else ''
            if 2 < len(name) < 80:
                key = f"{mult}:{name}"
                if not any(f"{x['multiplier']}:{x['name']}" == key for x in data['bonuses']):
                    data['bonuses'].append({'multiplier': mult, 'name': name, 'note': note})

    # ── Salvage Yard ─────────────────────────────────────────────────
    salvage_text = get_section_text(soup, r'special\s+activit|salvage\s+yard')
    for m in re.finditer(
        r'(?:The\s+)?([\w\s]+Robbery)\s*[:–\-]\s*([A-Z][A-Za-z\s]+?)(?:\n|·|,|\.|$)',
        salvage_text, re.MULTILINE
    ):
        robbery = 'The ' + re.sub(r'^The\s+', '', m.group(1).strip())
        car = m.group(2).strip().rstrip('.')
        if 2 < len(car) < 60 and not any(s['robbery'] == robbery for s in data['salvage']):
            data['salvage'].append({'tier': salvage_tier(robbery), 'robbery': robbery, 'car': car})
    data['salvage'].sort(key=lambda x: x['tier'])

    # ── Discounts ────────────────────────────────────────────────────
    # Pull from actual heading tags — unambiguous
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        heading_text = heading.get_text(strip=True)
        pct_m = re.search(r'(\d+)%\s*[Oo]ff', heading_text)
        if not pct_m:
            continue
        pct = int(pct_m.group(1))
        cat = clean(re.sub(r'\d+%\s*[Oo]ff\s*', '', heading_text)) or 'Various Vehicles'
        items = []
        level = int(heading.name[1])
        for sib in heading.find_next_siblings():
            if not sib.name:
                continue
            if sib.name in ['h1','h2','h3','h4'] and int(sib.name[1]) <= level:
                break
            if sib.name in ['ul', 'ol']:
                items = [li.get_text(strip=True) for li in sib.find_all('li')]
                break
        data['discounts'].append({'pct': pct, 'category': cat, 'until': '', 'items': items})

    # Business discount sentence (e.g. "Bail Offices are 40% off")
    biz_m = re.search(
        r'([A-Z][A-Za-z\s]{3,30}(?:Office|Offices|Yard|Hangar|Bunker|Lab)s?)\s+(?:are|is)\s+(\d+)%\s*off',
        full_text, re.IGNORECASE
    )
    if biz_m:
        cat = clean(biz_m.group(1))
        pct = int(biz_m.group(2))
        if not any(d['category'].startswith(cat.split()[0]) for d in data['discounts']):
            data['discounts'].insert(0, {'pct': pct, 'category': cat, 'until': '', 'items': []})

    # ── Gun Van ──────────────────────────────────────────────────────
    # Strict: only bullets from within the Gun Van section
    gun_bullets = get_section_bullets(soup, r'gun\s+van')
    for b in gun_bullets:
        b = b.replace('**', '').strip()
        name = re.sub(r'[-–:].+$', '', b).replace('GTA+', '').strip()
        if not (3 <= len(name) <= 50):
            continue
        free  = bool(re.search(r'\bfree\b', b, re.IGNORECASE))
        pct_m = re.search(r'(\d+)%\s*off', b, re.IGNORECASE)
        plus  = bool(re.search(r'GTA\+', b, re.IGNORECASE))
        deal  = 'FREE' if free else (f"{pct_m.group(1)}% OFF" if pct_m else '')
        if deal:
            data['gunVan'].append({'name': name, 'deal': deal, 'gtaPlus': plus})

    # ── Podium / Lucky Wheel ─────────────────────────────────────────
    m = re.search(r'Lucky\s+Wheel[:\s·]+([A-Z][A-Za-z\s]+?)(?:\n|·|,)', full_text, re.IGNORECASE)
    if m:
        data['podium'] = m.group(1).strip()

    # ── Prize Ride ───────────────────────────────────────────────────
    m = (re.search(r'Prize\s+Ride(?:\s+Challenge)?[:\s]+(?:win\s+)?(?:the\s+)?([A-Z][A-Za-z\s]+?)(?:\n|·|,|to\s+win)', full_text, re.IGNORECASE) or
         re.search(r'to\s+win\s+the\s+([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,|\.)', full_text, re.IGNORECASE))
    if m:
        data['prizeRide'] = m.group(1).strip()

    # ── FIB File ─────────────────────────────────────────────────────
    m = (re.search(r'(?:FIB\s+Priority\s+File|Priority\s+File)[:\s]+(?:the\s+)?([A-Z][A-Za-z\s]+File)', full_text, re.IGNORECASE) or
         re.search(r'The\s+([A-Z][A-Za-z\s]+File)\s*[–\-]', full_text, re.IGNORECASE))
    if m:
        data['fibFile'] = m.group(1).strip()

    # ── Most Wanted ──────────────────────────────────────────────────
    for m in re.finditer(
        r'([A-Z][a-z]+ [A-Z][a-z\']+(?:\s+[A-Z][a-z]+)?)\s*[-–]\s*((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+)\s*[-–]\s*(?:GTA)?\$?([\d,]+)',
        full_text
    ):
        name   = m.group(1).strip()
        date   = m.group(2).strip()
        reward = '$' + m.group(3)
        if not any(t['name'] == name and t['date'] == date for t in data['mostWanted']):
            data['mostWanted'].append({'name': name, 'date': date, 'reward': reward})
    data['mostWanted'].sort(key=lambda x: int(re.sub(r'\D', '', x['date']) or '0'))

    # ── LS Car Meet ──────────────────────────────────────────────────
    cm_body = get_section_text(soup, r'LS\s+Car\s+Meet\s+Activit')
    if not cm_body:
        cm_body = get_section_text(soup, r'LS\s+Car\s+Meet')

    if cm_body:
        if data['prizeRide']:
            data['carMeet']['prizeRide'] = data['prizeRide']

        m = re.search(r'Prize\s+Ride[:\s]+(?:[^\n]*?to\s+win\s+(?:the\s+)?)?([A-Z][A-Za-z\s]+?)(?:\n|·|,)', cm_body, re.IGNORECASE)
        if m:
            data['carMeet']['prizeRide'] = re.sub(r'(?:top|place).+', '', m.group(1), flags=re.IGNORECASE).strip()

        m = re.search(r'((?:top|place)\s+\d+[^\n.]+?(?:\d+\s+days|a\s+row))', cm_body, re.IGNORECASE)
        if m:
            data['carMeet']['prizeReq'] = m.group(1).strip()

        m = re.search(r'Premium\s+Test\s+(?:Ride|Drive)[:\s]+([A-Z][A-Za-z\s]+?)(?:\n|·|,|-)', cm_body, re.IGNORECASE)
        if m:
            data['carMeet']['premiumTest'] = m.group(1).strip()
            note_m = re.search(r'(?:Enhanced|PS5|Series|exclusive)[^\n]*', cm_body, re.IGNORECASE)
            if note_m:
                data['carMeet']['premiumTestNote'] = clean(note_m.group(0))

        m = re.search(r'Test\s+(?:Vehicles?|Track|Rides?)[:\s]+((?:[^\n]+\n?){1,6})', cm_body, re.IGNORECASE)
        if m:
            rides = []
            for line in m.group(1).split('\n'):
                line = re.sub(r'[-–:].+$', '', line.strip().lstrip('-•* ')).strip()
                if 2 < len(line) < 60:
                    rides.append(line)
            data['carMeet']['testRides'] = rides[:5]

        m = re.search(r'Lucky\s+Wheel[:\s·]+([A-Z][A-Za-z\s]+?)(?:\n|·|,)', cm_body, re.IGNORECASE)
        if m and not data['podium']:
            data['podium'] = m.group(1).strip()

    # PDM Showroom
    pdm = get_section_bullets(soup, r'Premium\s+Deluxe\s+Motorsport\s+Showroom')
    if not pdm:
        pdm = get_section_bullets(soup, r'Premium\s+Deluxe\s+Motorsport')
    data['carMeet']['pdm'] = [re.sub(r'\s*\(\d+%.*?\)', '', v).strip() for v in pdm if 2 < len(v) < 60][:8]

    # Luxury Autos
    la = get_section_bullets(soup, r'Luxury\s+Autos\s+Showroom')
    if not la:
        la = get_section_bullets(soup, r'Luxury\s+Autos')
    data['carMeet']['luxuryAutos'] = [v for v in la if 2 < len(v) < 60][:6]

    return data


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print(f"[GTA HQ Scraper] Starting — {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    parsed = None
    for url in SOURCES:
        print(f"  Trying {url} …")
        html = fetch(url)
        if not html or len(html) < 1000:
            print("  [WARN] Response missing or too short, skipping")
            continue

        result = parse(html)
        has_data = result.get('weekLabel') or result.get('bonuses') or result.get('salvage') or result.get('discounts')

        if has_data:
            parsed = result
            print(f"  ✓ Parsed successfully from {url}")
            print(f"    weekLabel  = '{parsed['weekLabel']}'")
            print(f"    challenge  = '{parsed['challenge']['desc'][:60]}'")
            print(f"    bonuses    = {len(parsed['bonuses'])}")
            print(f"    salvage    = {len(parsed['salvage'])}")
            print(f"    discounts  = {len(parsed['discounts'])}")
            print(f"    gunVan     = {len(parsed['gunVan'])}")
            print(f"    mostWanted = {len(parsed['mostWanted'])}")
            print(f"    podium     = '{parsed['podium']}'")
            print(f"    prizeRide  = '{parsed['prizeRide']}'")
            break
        else:
            print("  [WARN] No useful data found, trying next source")

    if not parsed:
        print("[ERROR] All sources failed. weekly-data.json unchanged.")
        sys.exit(1)

    try:
        with open("weekly-data.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = {}

    def pick(scraped, existing_val):
        if isinstance(scraped, list):   return scraped if scraped else existing_val
        if isinstance(scraped, dict):   return scraped if any(scraped.values()) else existing_val
        return scraped if scraped else existing_val

    output = {
        "_updated":    datetime.datetime.utcnow().strftime("%Y-%m-%d"),
        "weekLabel":   pick(parsed["weekLabel"],   existing.get("weekLabel",   "")),
        "challenge":   pick(parsed["challenge"],   existing.get("challenge",   {})),
        "bonuses":     pick(parsed["bonuses"],     existing.get("bonuses",     [])),
        "newVehicles": pick(parsed["newVehicles"], existing.get("newVehicles", [])),
        "discounts":   pick(parsed["discounts"],   existing.get("discounts",   [])),
        "podium":      pick(parsed["podium"],      existing.get("podium",      "")),
        "prizeRide":   pick(parsed["prizeRide"],   existing.get("prizeRide",   "")),
        "salvage":     pick(parsed["salvage"],     existing.get("salvage",     [])),
        "gunVan":      pick(parsed["gunVan"],      existing.get("gunVan",      [])),
        "fibFile":     pick(parsed["fibFile"],     existing.get("fibFile",     "")),
        "mostWanted":  pick(parsed["mostWanted"],  existing.get("mostWanted",  [])),
        "carMeet": {
            "prizeRide":       pick(parsed["carMeet"]["prizeRide"],       existing.get("carMeet",{}).get("prizeRide",       "")),
            "prizeReq":        pick(parsed["carMeet"]["prizeReq"],        existing.get("carMeet",{}).get("prizeReq",        "")),
            "premiumTest":     pick(parsed["carMeet"]["premiumTest"],     existing.get("carMeet",{}).get("premiumTest",     "")),
            "premiumTestNote": pick(parsed["carMeet"]["premiumTestNote"], existing.get("carMeet",{}).get("premiumTestNote", "")),
            "testRides":       pick(parsed["carMeet"]["testRides"],       existing.get("carMeet",{}).get("testRides",       [])),
            "luxuryAutos":     pick(parsed["carMeet"]["luxuryAutos"],     existing.get("carMeet",{}).get("luxuryAutos",     [])),
            "pdm":             pick(parsed["carMeet"]["pdm"],             existing.get("carMeet",{}).get("pdm",             [])),
        },
    }

    with open("weekly-data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Done! Week: {output['weekLabel']} | "
          f"Bonuses: {len(output['bonuses'])} | "
          f"Salvage: {len(output['salvage'])} | "
          f"GunVan: {len(output['gunVan'])}")


if __name__ == "__main__":
    main()
