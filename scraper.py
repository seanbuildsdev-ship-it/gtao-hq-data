#!/usr/bin/env python3
"""
GTA Online HQ — Weekly Data Scraper v3
Hybrid approach:
  - Remove TOC from soup first (fixes challenge parsing)
  - Use HTML headings for discounts (most reliable)
  - Use section-scoped text regex for everything else (bonuses, salvage, gun van)
"""

import json, re, sys, datetime
import requests
from bs4 import BeautifulSoup

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
        print(f"  [WARN] {url}: {e}")
        return None

# ── Helpers ───────────────────────────────────────────────────────────

def clean(s):
    return re.sub(r'\s+', ' ', (s or '').replace('*', '')).strip()

def salvage_tier(name):
    n = name.lower()
    if re.search(r'cargo|ship|duggan|podium', n): return 3
    if re.search(r'mctony', n):                   return 2
    if re.search(r'gangbanger', n):               return 1
    return 2

def extract_bullets_from_text(text):
    """Pull bullet lines from a plain text block."""
    bullets = []
    for line in text.split('\n'):
        line = line.strip()
        if re.match(r'^[-•*+]', line) or re.match(r'^\d+\.', line):
            line = re.sub(r'^[-•*+\d.]\s*', '', line).replace('**', '').strip()
            if line:
                bullets.append(line)
    return bullets

def get_section_text(text, pattern):
    """
    From plain text, find the first heading matching pattern
    and return content until the next heading.
    A heading is a short line starting with capital letter or ## prefix.
    """
    lines = text.split('\n')
    active = False
    body = []
    for line in lines:
        t = line.strip()
        if not t:
            if active:
                body.append('')
            continue
        # Detect heading: ## prefix or short title-cased line
        is_heading = bool(re.match(r'^#{1,4}\s', t)) or (
            len(t) < 100 and t[0].isupper() and
            not re.search(r'[.!?,;]$', t) and
            len(t.split()) <= 12
        )
        heading_text = re.sub(r'^#+\s*', '', t)
        if is_heading:
            if re.search(pattern, heading_text, re.IGNORECASE):
                active = True
                continue
            elif active:
                break  # Stop at next heading
        if active:
            body.append(line)
    return '\n'.join(body).strip()

def get_html_section_bullets(soup, pattern):
    """
    HTML-based: find heading matching pattern, return li items
    from the first ul/ol — searching inside nested elements too.
    """
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        if re.search(pattern, heading.get_text(strip=True), re.IGNORECASE):
            level = int(heading.name[1])
            for sib in heading.find_next_siblings():
                if not sib.name:
                    continue
                if sib.name in ['h1','h2','h3','h4'] and int(sib.name[1]) <= level:
                    break
                # Direct list
                if sib.name in ['ul', 'ol']:
                    return [li.get_text(strip=True) for li in sib.find_all('li')]
                # List nested inside a div/p
                ul = sib.find(['ul', 'ol'])
                if ul:
                    return [li.get_text(strip=True) for li in ul.find_all('li')]
    return []

# ── Main Parser ───────────────────────────────────────────────────────

def parse(html):
    soup = BeautifulSoup(html, 'html.parser')

    # 1. Remove noise elements
    for tag in soup.find_all(['nav','footer','aside','script','style','header']):
        tag.decompose()

    # 2. Remove Table of Contents — critical so challenge regex doesn't hit TOC links
    for el in soup.find_all(class_=re.compile(r'toc|table.of.content|ez-toc|wp-block-table', re.I)):
        el.decompose()
    for el in soup.find_all(id=re.compile(r'toc|table.of.content', re.I)):
        el.decompose()
    # Also remove any <ul> that only contains anchor links (classic TOC pattern)
    for ul in soup.find_all('ul'):
        links = ul.find_all('a')
        lis   = ul.find_all('li')
        if links and len(links) == len(lis) and all(a.get('href','').startswith('#') for a in links):
            ul.decompose()

    # 3. Get clean plain text from the scrubbed soup
    text = soup.get_text(separator='\n')
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

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

    # ── Week label (H1) ──────────────────────────────────────────────
    h1 = soup.find('h1')
    if h1:
        m = re.search(r'\(([A-Z][a-z]+ \d+\s*[–\-]\s*(?:[A-Z][a-z]+ )?\d+,?\s*\d{4})\)', h1.get_text())
        if m:
            label = re.sub(r',?\s*\d{4}', '', m.group(1))
            label = re.sub(r'(\w{3})\w*\s+(\d+)', lambda x: x.group(1).upper()+' '+x.group(2), label)
            data['weekLabel'] = label.strip()

    # ── Weekly Challenge (section-scoped text) ───────────────────────
    chal_body = get_section_text(text, r'weekly\s+challenge')
    if chal_body:
        m = re.search(r'((?:Secure|Complete|Win|Earn)\s+[^.\n]{5,100})', chal_body, re.IGNORECASE)
        if m:
            data['challenge']['desc'] = clean(m.group(1))
        m = re.search(r'(?:receive|get|earn)\s+(?:the\s+)?([^\n]{5,120})', chal_body, re.IGNORECASE)
        if m:
            data['challenge']['reward'] = clean(m.group(1))
        if not data['challenge']['desc']:
            lines = [l.strip() for l in chal_body.split('\n') if len(l.strip()) > 10]
            if lines: data['challenge']['desc'] = clean(lines[0])
            if len(lines) > 1 and not data['challenge']['reward']:
                data['challenge']['reward'] = clean(lines[1])

    # ── Bonus Money (section-scoped text — stops at next heading) ────
    for mult, pattern in [
        (4, r'quadruple.*(?:money|bonus|reward)'),
        (3, r'triple.*(?:money|bonus|reward)'),
        (2, r'double.*(?:money|bonus|reward)'),
    ]:
        section = get_section_text(text, pattern)
        bullets = extract_bullets_from_text(section)
        # Fallback: any line with a dash separator
        if not bullets:
            bullets = [l.strip() for l in section.split('\n')
                      if l.strip() and ' - ' in l and len(l.strip()) < 120]

        for b in bullets:
            b = b.replace('**', '').strip()
            name_m = re.match(r'^([^–\-:]{3,70}?)(?:\s*[-–:]|$)', b)
            if not name_m:
                continue
            name = clean(name_m.group(1))
            note_m = re.search(r'[-–:]\s*(.{5,80})', b)
            note = clean(note_m.group(1))[:60] if note_m else ''
            # Never override section multiplier based on note content
            note = re.sub(r'GTA\+\s+members?\s+get\s+(?:four|three|two|\d+)\s+times.*',
                          'GTA+ bonus', note, flags=re.IGNORECASE)
            if 2 < len(name) < 80:
                key = f"{mult}:{name}"
                if not any(f"{x['multiplier']}:{x['name']}" == key for x in data['bonuses']):
                    data['bonuses'].append({'multiplier': mult, 'name': name, 'note': note})

    # ── Salvage (section-scoped text) ────────────────────────────────
    salvage_body = get_section_text(text, r'special\s+activit|salvage\s+yard')
    for m in re.finditer(
        r'(?:The\s+)?([\w\s]+Robbery)\s*[:–\-]\s*([A-Z][A-Za-z\s]+?)(?:\n|·|,|\.|$)',
        salvage_body, re.MULTILINE
    ):
        robbery = 'The ' + re.sub(r'^The\s+', '', m.group(1).strip())
        car = m.group(2).strip().rstrip('.')
        if 2 < len(car) < 60 and not any(s['robbery'] == robbery for s in data['salvage']):
            data['salvage'].append({'tier': salvage_tier(robbery), 'robbery': robbery, 'car': car})
    data['salvage'].sort(key=lambda x: x['tier'])

    # ── Discounts (HTML headings — most reliable) ────────────────────
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
        items = []
        level = int(heading.name[1])
        for sib in heading.find_next_siblings():
            if not sib.name: continue
            if sib.name in ['h1','h2','h3','h4'] and int(sib.name[1]) <= level: break
            if sib.name in ['ul','ol']:
                items.extend([li.get_text(strip=True) for li in sib.find_all('li')])
            for ul in sib.find_all(['ul','ol']):
                items.extend([li.get_text(strip=True) for li in ul.find_all('li')])
        items = list(dict.fromkeys(items))
        existing = next((d for d in data['discounts'] if d['pct'] == pct), None)
        if existing:
            if len(items) > len(existing['items']):
                existing['items'] = items
                existing['category'] = cat
        else:
            data['discounts'].append({'pct': pct, 'category': cat, 'until': '', 'items': items})

    # Business discount sentence ("All Bail Offices are 40% off")
    biz_m = (
        re.search(r'All\s+([A-Z][A-Za-z\s]{3,30}(?:Office|Offices|Yard|Hangar|Bunker|Lab)s?)[^.]*?(\d+)%\s*off', text, re.IGNORECASE) or
        re.search(r'([A-Z][A-Za-z\s]{3,30}(?:Office|Offices|Yard|Hangar|Bunker|Lab)s?)\s+(?:are|is)\s+(\d+)%\s*off', text, re.IGNORECASE)
    )
    if biz_m:
        raw = biz_m.group(0)
        cat = ('All ' if not raw.lower().startswith('all') else '') + clean(biz_m.group(1))
        pct = int(biz_m.group(2))
        if not any(d['category'].lower().startswith('all') and str(d['pct']) == str(pct)
                   for d in data['discounts']):
            data['discounts'].insert(0, {'pct': pct, 'category': cat, 'until': '', 'items': []})

    # ── Gun Van (section-scoped text) ────────────────────────────────
    gv_body = get_section_text(text, r'gun\s+van')
    # Also try HTML bullets as fallback
    gv_bullets = extract_bullets_from_text(gv_body)
    if not gv_bullets:
        gv_bullets = get_html_section_bullets(soup, r'gun\s+van')
    for b in gv_bullets:
        b = b.replace('**', '').strip()
        name = re.sub(r'[-–:].+$', '', b).replace('GTA+', '').strip()
        if not (3 <= len(name) <= 50): continue
        free  = bool(re.search(r'\bfree\b', b, re.IGNORECASE))
        pct_m = re.search(r'(\d+)%\s*off', b, re.IGNORECASE)
        plus  = bool(re.search(r'GTA\+', b, re.IGNORECASE))
        deal  = 'FREE' if free else (f"{pct_m.group(1)}% OFF" if pct_m else '')
        if deal:
            data['gunVan'].append({'name': name, 'deal': deal, 'gtaPlus': plus})

    # ── Podium / Lucky Wheel ─────────────────────────────────────────
    m = re.search(r'Lucky\s+Wheel[:\s·]+([A-Z][A-Za-z\s]+?)(?:\n|·|,)', text, re.IGNORECASE)
    if m:
        data['podium'] = m.group(1).strip()

    # ── Prize Ride (never capture heading words like "Challenge") ────
    # Look for pattern after the colon, not in the heading itself
    m = re.search(
        r'Prize\s+Ride\s+Challenge[:\s]*\n+([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,|\.)',
        text, re.IGNORECASE
    )
    if not m:
        m = re.search(
            r'Prize\s+Ride[:\s]+(?!Challenge)([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,|to\s+win)',
            text, re.IGNORECASE
        )
    if not m:
        m = re.search(r'to\s+win\s+the\s+([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,|\.)', text, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        if candidate.lower() not in ('challenge', 'the', 'a', 'an'):
            data['prizeRide'] = candidate

    # ── FIB File ─────────────────────────────────────────────────────
    m = (re.search(r'(?:FIB\s+Priority\s+File|Priority\s+File)[:\s]+(?:the\s+)?([A-Z][A-Za-z\s]+File)', text, re.IGNORECASE) or
         re.search(r'The\s+([A-Z][A-Za-z\s]+File)\s*[–\-]', text, re.IGNORECASE))
    if m:
        data['fibFile'] = m.group(1).strip()

    # ── Most Wanted ──────────────────────────────────────────────────
    for m in re.finditer(
        r'([A-Z][a-z]+ [A-Z][a-z\']+(?:\s+[A-Z][a-z]+)?)\s*[-–]\s*((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+)\s*[-–]\s*(?:GTA)?\$?([\d,]+)',
        text
    ):
        name, date, reward = m.group(1).strip(), m.group(2).strip(), '$' + m.group(3)
        if not any(t['name'] == name and t['date'] == date for t in data['mostWanted']):
            data['mostWanted'].append({'name': name, 'date': date, 'reward': reward})
    data['mostWanted'].sort(key=lambda x: int(re.sub(r'\D','',x['date']) or '0'))

    # ── LS Car Meet ──────────────────────────────────────────────────
    cm_body = get_section_text(text, r'LS\s+Car\s+Meet\s+Activit')
    if not cm_body:
        cm_body = get_section_text(text, r'LS\s+Car\s+Meet')

    if cm_body:
        if data['prizeRide']:
            data['carMeet']['prizeRide'] = data['prizeRide']
        m = re.search(r'Prize\s+Ride[:\s]+(?:[^\n]*to\s+win\s+(?:the\s+)?)?([A-Z][A-Za-z\s]+?)(?:\n|·|,)', cm_body, re.IGNORECASE)
        if m:
            candidate = re.sub(r'(?:top|place).+', '', m.group(1), flags=re.IGNORECASE).strip()
            if candidate.lower() not in ('challenge',''):
                data['carMeet']['prizeRide'] = candidate
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
    pdm_body = get_section_text(text, r'Premium\s+Deluxe\s+Motorsport')
    pdm_bullets = extract_bullets_from_text(pdm_body)
    if not pdm_bullets:
        pdm_bullets = get_html_section_bullets(soup, r'Premium\s+Deluxe\s+Motorsport')
    data['carMeet']['pdm'] = [re.sub(r'\s*\(\d+%.*?\)','',v).strip() for v in pdm_bullets if 2<len(v)<60][:8]

    # Luxury Autos
    la_body = get_section_text(text, r'Luxury\s+Autos')
    la_bullets = extract_bullets_from_text(la_body)
    if not la_bullets:
        la_bullets = get_html_section_bullets(soup, r'Luxury\s+Autos')
    data['carMeet']['luxuryAutos'] = [v for v in la_bullets if 2<len(v)<60][:6]

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
            b_list = [str(b['multiplier'])+'x '+b['name'][:20] for b in parsed['bonuses']]
            s_list = [s['car'] for s in parsed['salvage']]
            d_list = [str(d['pct'])+'% '+d['category'][:15] for d in parsed['discounts']]
            g_list = [g['name'] for g in parsed['gunVan']]
            print(f"    bonuses    = {len(parsed['bonuses'])} {b_list}")
            print(f"    salvage    = {len(parsed['salvage'])} {s_list}")
            print(f"    discounts  = {len(parsed['discounts'])} {d_list}")
            print(f"    gunVan     = {len(parsed['gunVan'])} {g_list}")
            print(f"    mostWanted = {len(parsed['mostWanted'])}")
            print(f"    podium     = '{parsed['podium']}'")
            print(f"    prizeRide  = '{parsed['prizeRide']}'")
            print(f"    fibFile    = '{parsed['fibFile']}'")
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

    def pick(s, e):
        if isinstance(s, list): return s if s else e
        if isinstance(s, dict): return s if any(s.values()) else e
        return s if s else e

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
          f"GunVan: {len(output['gunVan'])} | "
          f"Discounts: {len(output['discounts'])}")

if __name__ == "__main__":
    main()
