#!/usr/bin/env python3
"""
GTA Online HQ — Weekly Data Scraper v5
- Checks freshness before saving (no stale data)
- Tries rolling URLs first, then RSS lookup, then PCQuest search
- Runs 3x on Thursday so late-publishing sites get caught
"""

import json, re, sys, datetime, urllib.parse
import requests
from bs4 import BeautifulSoup

# ── Sources ───────────────────────────────────────────────────────────

ROLLING_SOURCES = [
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
        print(f"  [WARN] fetch {url}: {e}")
        return None

def find_fresh_url_via_site_search(domain):
    """
    Find latest GTA weekly article by searching the site directly.
    Avoids Google rate limits entirely.
    """
    search_urls = {
        "techwiser.com":  "https://techwiser.com/?s=gta+online+weekly+update",
        "fandomwire.com": "https://fandomwire.com/?s=gta+online+weekly+update",
        "sportskeeda.com": "https://www.sportskeeda.com/gta/gta-online-weekly-update",
    }
    search_url = search_urls.get(domain)
    if not search_url:
        return None
    try:
        html = fetch(search_url)
        if not html:
            return None
        # Find article links containing "weekly" and "gta"
        escaped = re.escape(domain)
        links = re.findall(
            r'href="(https://(?:www\.)?'  + escaped + r'/[^"]*(?:weekly|gta-online)[^"]*)"'
            , html
        )
        for link in links:
            if 'weekly' in link.lower() and ('gta' in link.lower() or 'update' in link.lower()):
                print(f"  Found via site search: {link}")
                return link
    except Exception as e:
        print(f"  [WARN] Site search {domain}: {e}")
    return None

# Keep old name as alias so Strategy 2 calls still work
find_fresh_url_via_rss = find_fresh_url_via_site_search

# ── Freshness check ───────────────────────────────────────────────────

def is_fresh(scraped_label, stored_label):
    """True if scraped data is a different week than what's stored."""
    if not stored_label:
        return True
    if not scraped_label:
        return False
    norm = lambda s: re.sub(r'[^A-Z0-9]', '', s.upper())
    return norm(scraped_label) != norm(stored_label)

# ── Helpers ───────────────────────────────────────────────────────────

def clean(s):
    return re.sub(r'\s+', ' ', (s or '').replace('*', '')).strip()

def salvage_tier(name):
    n = name.lower()
    if re.search(r'cargo|ship|duggan|podium', n): return 3
    if re.search(r'mctony', n):                   return 2
    if re.search(r'gangbanger', n):               return 1
    return 2

def get_section_items(soup, pattern, include_paragraphs=True):
    """Find heading matching pattern, collect li/p items until next heading."""
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        if not re.search(pattern, heading.get_text(strip=True), re.IGNORECASE):
            continue
        level = int(heading.name[1])
        items = []
        seen = set()
        for el in heading.find_all_next():
            if el.name in ['h1', 'h2', 'h3', 'h4']:
                if int(el.name[1]) <= level:
                    break
            if el.name == 'li':
                text = el.get_text(separator=' ', strip=True)
                if text and text not in seen and 2 < len(text) < 250:
                    items.append(text)
                    seen.add(text)
            elif include_paragraphs and el.name == 'p':
                text = el.get_text(strip=True)
                if text and ' - ' in text and text not in seen and 2 < len(text) < 250:
                    items.append(text)
                    seen.add(text)
        return items
    return []

def get_section_text(soup, pattern):
    """Get full text of a section (heading to next heading)."""
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        if not re.search(pattern, heading.get_text(strip=True), re.IGNORECASE):
            continue
        level = int(heading.name[1])
        parts = []
        seen = set()
        for el in heading.find_all_next():
            if el.name in ['h1', 'h2', 'h3', 'h4']:
                if int(el.name[1]) <= level:
                    break
            if el.name in ['p', 'li', 'div']:
                text = el.get_text(separator=' ', strip=True)
                if text and text not in seen and len(text) > 2:
                    parts.append(text)
                    seen.add(text)
        return '\n'.join(parts)
    return ''

# ── Parser ────────────────────────────────────────────────────────────

def parse(html):
    soup = BeautifulSoup(html, 'html.parser')

    # Remove noise
    for tag in soup.find_all(['nav', 'footer', 'aside', 'script', 'style', 'header']):
        tag.decompose()
    for el in soup.find_all(class_=re.compile(r'toc|table.of.content|ez-toc', re.I)):
        el.decompose()
    for el in soup.find_all(id=re.compile(r'toc|table.of.content', re.I)):
        el.decompose()
    for ul in soup.find_all('ul'):
        links = ul.find_all('a')
        lis = ul.find_all('li', recursive=False)
        if links and len(links) == len(lis) and all(a.get('href', '').startswith('#') for a in links):
            ul.decompose()

    full_text = soup.get_text(separator='\n')
    full_text = re.sub(r'\r\n', '\n', full_text)
    full_text = re.sub(r'\n{3,}', '\n\n', full_text).strip()

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

    # Week label — handles multiple formats:
    # TechWiser/Sportskeeda: "(April 9 – 15, 2026)"
    # PCQuest/others:        "April 16 to 22, 2026"  or  "April 16-22, 2026"
    h1 = soup.find('h1')
    if h1:
        h1_text = h1.get_text()
        m = (
            re.search(r'\(([A-Z][a-z]+ \d+\s*[–\-]\s*(?:[A-Z][a-z]+ )?\d+,?\s*\d{4})\)', h1_text) or
            re.search(r'([A-Z][a-z]+ \d+\s*(?:to|–|-)\s*(?:[A-Z][a-z]+ )?\d+,?\s*\d{4})', h1_text)
        )
        if m:
            label = m.group(1)
            label = re.sub(r',?\s*\d{4}', '', label)
            label = re.sub(r'\s+to\s+', ' – ', label, flags=re.IGNORECASE)
            label = re.sub(r'(\w{3})\w*\s+(\d+)', lambda x: x.group(1).upper()+' '+x.group(2), label)
            data['weekLabel'] = label.strip()

    # Challenge
    chal_text = get_section_text(soup, r'weekly\s+challenge')
    if chal_text:
        m = re.search(r'((?:Secure|Complete|Win|Earn)\s+[^.\n]{5,100})', chal_text, re.IGNORECASE)
        if m:
            data['challenge']['desc'] = clean(m.group(1))
        m = re.search(r'(?:receive|get|earn)\s+(?:the\s+)?([^\n]{5,120})', chal_text, re.IGNORECASE)
        if m:
            data['challenge']['reward'] = clean(m.group(1))
        if not data['challenge']['desc']:
            lines = [l.strip() for l in chal_text.split('\n') if len(l.strip()) > 10]
            if lines:
                data['challenge']['desc'] = clean(lines[0])
            if len(lines) > 1 and not data['challenge']['reward']:
                data['challenge']['reward'] = clean(lines[1])

    # Bonus Money
    for mult, pattern in [
        (4, r'quadruple.*(?:money|bonus|reward)'),
        (3, r'triple.*(?:money|bonus|reward)'),
        (2, r'double.*(?:money|bonus|reward)'),
    ]:
        items = get_section_items(soup, pattern, include_paragraphs=True)
        for b in items:
            b = b.replace('**', '').strip()
            name_m = re.match(r'^([^–\-:]{3,70}?)(?:\s*[-–:]|$)', b)
            if not name_m:
                continue
            name = clean(name_m.group(1))
            note_m = re.search(r'[-–:]\s*(.{5,80})', b)
            note = clean(note_m.group(1))[:60] if note_m else ''
            note = re.sub(r'GTA\+\s+members?\s+get\s+(?:four|three|two|\d+)\s+times.*',
                          'GTA+ bonus', note, flags=re.IGNORECASE)
            if 2 < len(name) < 80:
                key = f"{mult}:{name}"
                if not any(f"{x['multiplier']}:{x['name']}" == key for x in data['bonuses']):
                    data['bonuses'].append({'multiplier': mult, 'name': name, 'note': note})

    # Salvage
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

    # Discounts
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        heading_text = heading.get_text(strip=True)
        pct_m = re.search(r'(\d+)%\s*[Oo]ff', heading_text)
        if not pct_m:
            continue
        pct = int(pct_m.group(1))
        cat_raw = re.sub(r'\d+%\s*[Oo]ff\s*', '', heading_text).strip()
        cat_raw = re.sub(r'^(?:Business\s+Discounts?|Vehicle\s+Discounts?|Discounts?)\s*',
                         '', cat_raw, flags=re.IGNORECASE).strip()
        cat = clean(cat_raw) or 'Various Vehicles'
        level = int(heading.name[1])
        items = []
        seen = set()
        for el in heading.find_all_next():
            if el.name in ['h1', 'h2', 'h3', 'h4'] and int(el.name[1]) <= level:
                break
            if el.name == 'li':
                text = el.get_text(separator=' ', strip=True)
                if text and text not in seen and 2 < len(text) < 100:
                    items.append(text)
                    seen.add(text)
        existing = next((d for d in data['discounts'] if d['pct'] == pct), None)
        if existing:
            if len(items) > len(existing['items']):
                existing['items'] = items
                existing['category'] = cat
        else:
            data['discounts'].append({'pct': pct, 'category': cat, 'until': '', 'items': items})

    # Business discount sentence
    biz_m = (
        re.search(r'All\s+([A-Z][A-Za-z\s]{3,30}(?:Office|Offices|Yard|Hangar|Bunker|Lab|Farm)s?)[^.]*?(\d+)%\s*off', full_text, re.IGNORECASE) or
        re.search(r'([A-Z][A-Za-z\s]{3,30}(?:Office|Offices|Yard|Hangar|Bunker|Lab|Farm)s?)\s+(?:are|is)\s+(\d+)%\s*off', full_text, re.IGNORECASE)
    )
    if biz_m:
        raw = biz_m.group(0)
        cat = ('All ' if not raw.lower().startswith('all') else '') + clean(biz_m.group(1))
        pct = int(biz_m.group(2))
        if not any(d['pct'] == pct and d['category'].lower().startswith(cat.split()[0].lower()) for d in data['discounts']):
            data['discounts'].insert(0, {'pct': pct, 'category': cat, 'until': '', 'items': []})

    # Gun Van
    gv_items = get_section_items(soup, r'gun\s+van', include_paragraphs=False)
    for b in gv_items:
        b = b.replace('**', '').strip()
        name = re.sub(r'[-–:].+$', '', b).replace('GTA+', '').strip()
        if not (3 <= len(name) <= 50):
            continue
        free = bool(re.search(r'\bfree\b', b, re.IGNORECASE))
        pct_m = re.search(r'(\d+)%\s*off', b, re.IGNORECASE)
        plus = bool(re.search(r'GTA\+', b, re.IGNORECASE))
        deal = 'FREE' if free else (f"{pct_m.group(1)}% OFF" if pct_m else '')
        if deal:
            data['gunVan'].append({'name': name, 'deal': deal, 'gtaPlus': plus})

    # Podium
    m = re.search(r'Lucky\s+Wheel[:\s·]+([A-Z][A-Za-z\s]+?)(?:\n|·|,)', full_text, re.IGNORECASE)
    if m:
        data['podium'] = m.group(1).strip()

    # Prize Ride
    m = (
        re.search(r'to\s+win\s+the\s+([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,|\.)', full_text, re.IGNORECASE) or
        re.search(r'Prize\s+Ride[:\s]+(?!Challenge\b)([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,)', full_text, re.IGNORECASE)
    )
    if m:
        candidate = m.group(1).strip()
        if candidate.lower() not in ('challenge', 'the', 'a', 'an', ''):
            data['prizeRide'] = candidate

    # FIB File
    m = (
        re.search(r'(?:FIB\s+Priority\s+File|Priority\s+File)[:\s]+(?:the\s+)?([A-Z][A-Za-z\s]+File)', full_text, re.IGNORECASE) or
        re.search(r'The\s+([A-Z][A-Za-z\s]+File)\s*[–\-]', full_text, re.IGNORECASE)
    )
    if m:
        data['fibFile'] = m.group(1).strip()

    # Most Wanted
    for m in re.finditer(
        r'([A-Z][a-z]+ [A-Z][a-z\']+(?:\s+[A-Z][a-z]+)?)\s*[-–]\s*'
        r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+)\s*[-–]\s*(?:GTA)?\$?([\d,]+)',
        full_text
    ):
        name, date, reward = m.group(1).strip(), m.group(2).strip(), '$'+m.group(3)
        if not any(t['name'] == name and t['date'] == date for t in data['mostWanted']):
            data['mostWanted'].append({'name': name, 'date': date, 'reward': reward})
    data['mostWanted'].sort(key=lambda x: int(re.sub(r'\D', '', x['date']) or '0'))

    # LS Car Meet
    cm_text = get_section_text(soup, r'LS\s+Car\s+Meet\s+Activit')
    if not cm_text:
        cm_text = get_section_text(soup, r'LS\s+Car\s+Meet')
    if cm_text:
        if data['prizeRide']:
            data['carMeet']['prizeRide'] = data['prizeRide']
        m = re.search(r'to\s+win\s+the\s+([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,|\.)', cm_text, re.IGNORECASE)
        if m:
            c = m.group(1).strip()
            if c.lower() not in ('challenge', ''):
                data['carMeet']['prizeRide'] = c
        m = re.search(r'((?:top|place)\s+\d+[^\n.]+?(?:\d+\s+days|a\s+row))', cm_text, re.IGNORECASE)
        if m:
            data['carMeet']['prizeReq'] = m.group(1).strip()
        m = re.search(r'Premium\s+Test\s+(?:Ride|Drive)[:\s]+([A-Z][A-Za-z\s]+?)(?:\n|·|,|-)', cm_text, re.IGNORECASE)
        if m:
            data['carMeet']['premiumTest'] = m.group(1).strip()
            note_m = re.search(r'(?:Enhanced|PS5|Series|exclusive|HSW)[^\n]*', cm_text, re.IGNORECASE)
            if note_m:
                data['carMeet']['premiumTestNote'] = clean(note_m.group(0))
        m = re.search(r'Test\s+(?:Vehicles?|Track|Rides?)[:\s]+((?:[^\n]+\n?){1,6})', cm_text, re.IGNORECASE)
        if m:
            rides = []
            for line in m.group(1).split('\n'):
                line = re.sub(r'[-–:].+$', '', line.strip().lstrip('-•* ')).strip()
                if 2 < len(line) < 60:
                    rides.append(line)
            data['carMeet']['testRides'] = rides[:5]
        m = re.search(r'Lucky\s+Wheel[:\s·]+([A-Z][A-Za-z\s]+?)(?:\n|·|,)', cm_text, re.IGNORECASE)
        if m and not data['podium']:
            data['podium'] = m.group(1).strip()

    # PDM
    pdm_items = get_section_items(soup, r'Premium\s+Deluxe\s+Motorsport', include_paragraphs=False)
    data['carMeet']['pdm'] = [re.sub(r'\s*\(\d+%.*?\)', '', v).strip() for v in pdm_items if 2 < len(v) < 60][:8]

    # Luxury Autos
    la_items = get_section_items(soup, r'Luxury\s+Autos', include_paragraphs=False)
    data['carMeet']['luxuryAutos'] = [v for v in la_items if 2 < len(v) < 60][:6]

    return data

# ── Main ──────────────────────────────────────────────────────────────

def main():
    now = datetime.datetime.utcnow()
    print(f"[GTA HQ Scraper] Starting — {now.strftime('%Y-%m-%d %H:%M UTC')}")

    # Load existing data for freshness check
    try:
        with open("weekly-data.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing_label = existing.get("weekLabel", "")
    except Exception:
        existing = {}
        existing_label = ""
    print(f"  Stored week: '{existing_label}'")

    parsed = None

    # ── Strategy 1: Rolling URLs ──────────────────────────────────────
    print("\n── Strategy 1: Rolling URLs ──")
    for url in ROLLING_SOURCES:
        print(f"  Trying {url} …")
        html = fetch(url)
        if not html or len(html) < 1000:
            print("  [SKIP] Too short or failed")
            continue
        result = parse(html)
        has_data = result.get('weekLabel') or result.get('bonuses') or result.get('discounts')
        if not has_data:
            print("  [SKIP] No useful data parsed")
            continue
        if not is_fresh(result.get('weekLabel', ''), existing_label):
            print(f"  [SKIP] Stale — still '{result.get('weekLabel')}', site not updated yet")
            continue
        parsed = result
        print(f"  ✓ Fresh! Week: '{result.get('weekLabel')}'")
        break

    # ── Strategy 2: Site search for fresh article URLs ───────────────
    if not parsed:
        print("\n── Strategy 2: Site search ──")
        for domain in ["techwiser.com", "fandomwire.com", "sportskeeda.com"]:
            url = find_fresh_url_via_rss(domain)
            if not url:
                print(f"  [SKIP] No RSS URL found for {domain}")
                continue
            print(f"  RSS URL: {url}")
            html = fetch(url)
            if not html or len(html) < 1000:
                continue
            result = parse(html)
            has_data = result.get('weekLabel') or result.get('bonuses') or result.get('discounts')
            if has_data and is_fresh(result.get('weekLabel', ''), existing_label):
                parsed = result
                print(f"  ✓ Fresh via RSS! Week: '{result.get('weekLabel')}'")
                break
            elif has_data:
                print(f"  [SKIP] RSS article also stale: '{result.get('weekLabel')}'")

    # ── Strategy 3: PCQuest search ───────────────────────────────────
    if not parsed:
        print("\n── Strategy 3: PCQuest search ──")
        month = now.strftime("%B").lower()
        year = now.year
        search_url = f"https://www.pcquest.com/gaming/?s=gta+online+weekly+update+{month}+{year}"
        html = fetch(search_url)
        if html:
            urls = re.findall(r'href="(https://www.pcquest.com/gaming/gta-online-weekly-update[^"]+)"', html)
            for url in urls[:3]:
                print(f"  Trying PCQuest: {url}")
                html2 = fetch(url)
                if not html2 or len(html2) < 1000:
                    continue
                result = parse(html2)
                has_data = result.get('bonuses') or result.get('discounts') or result.get('weekLabel')
                if not has_data:
                    continue
                scraped_label = result.get('weekLabel', '')
                # If weekLabel is empty, check URL for current month/year as proxy
                if not scraped_label:
                    now_month = now.strftime("%B").lower()[:3]
                    url_fresh = now_month in url.lower() and str(now.year) in url
                    if url_fresh:
                        # Synthesize label from URL date pattern e.g. "april-16-to-22"
                        url_dates = re.search(r'(\w+-\d+-to-\d+|\w+-\d+[-–]\d+)', url)
                        if url_dates:
                            raw = url_dates.group(1).replace('-', ' ').replace('to', '–')
                            result['weekLabel'] = re.sub(r'(\w{3})\w*\s+(\d+)',
                                lambda x: x.group(1).upper()+' '+x.group(2), raw).strip()
                            scraped_label = result['weekLabel']
                            print(f"  Synthesized weekLabel from URL: '{scraped_label}'")
                if is_fresh(scraped_label, existing_label) or (has_data and not existing_label):
                    parsed = result
                    print(f"  ✓ Fresh via PCQuest! Week: '{scraped_label}'")
                    break
                else:
                    print(f"  [SKIP] PCQuest stale: '{scraped_label}'")

    # ── No fresh data found ───────────────────────────────────────────
    if not parsed:
        print("\n[INFO] No fresh data found from any source.")
        print("       Sites may not have published the new week yet.")
        print("       The cron will retry at 13:00 and 16:00 UTC today.")
        sys.exit(0)  # Clean exit — don't fail the action, just retry later

    # ── Print debug summary ───────────────────────────────────────────
    b_list = [str(b['multiplier'])+'x '+b['name'][:25] for b in parsed['bonuses']]
    s_list = [s['car'] for s in parsed['salvage']]
    d_list = [str(d['pct'])+'% '+d['category'][:20] for d in parsed['discounts']]
    g_list = [g['name'] for g in parsed['gunVan']]
    print(f"\n  weekLabel  = '{parsed['weekLabel']}'")
    print(f"  challenge  = '{parsed['challenge']['desc'][:60]}'")
    print(f"  bonuses    = {len(parsed['bonuses'])} {b_list}")
    print(f"  salvage    = {len(parsed['salvage'])} {s_list}")
    print(f"  discounts  = {len(parsed['discounts'])} {d_list}")
    print(f"  gunVan     = {len(parsed['gunVan'])} {g_list}")
    print(f"  mostWanted = {len(parsed['mostWanted'])}")
    print(f"  podium     = '{parsed['podium']}'")
    print(f"  prizeRide  = '{parsed['prizeRide']}'")

    # ── Merge with existing and save ──────────────────────────────────
    def pick(s, e):
        if isinstance(s, list): return s if s else e
        if isinstance(s, dict): return s if any(s.values()) else e
        return s if s else e

    output = {
        "_updated":    now.strftime("%Y-%m-%d"),
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

    print(f"\n✅ Done! Week:{output['weekLabel']} | "
          f"Bonuses:{len(output['bonuses'])} | "
          f"Salvage:{len(output['salvage'])} | "
          f"GunVan:{len(output['gunVan'])} | "
          f"Discounts:{len(output['discounts'])}")


if __name__ == "__main__":
    main()
