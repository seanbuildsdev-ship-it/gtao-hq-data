#!/usr/bin/env python3
"""
GTA Online HQ — Weekly Data Scraper v6
- PCQuest first (new URL every week)
- Fandomwire supplement (constructed URL — no scraping search pages)
- Freshness + quality validation
- 10x bonus support
- No markdown artifacts
"""

import json, re, sys, datetime, urllib.parse
import requests
from bs4 import BeautifulSoup

# ── Sources ───────────────────────────────────────────────────────────

ROLLING_SOURCES = [
    "https://rockstarintel.com/gta-online-event-week/",
    "https://techwiser.com/gta-online-weekly-update/",
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

# ── Fandomwire URL builder ────────────────────────────────────────────

def ordinal(n):
    """1->'1st', 2->'2nd', 23->'23rd', 30->'30th'"""
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"

def find_fandomwire_url(now):
    """
    Construct Fandomwire URL directly from the current GTA week dates.
    Fandomwire uses Thursday-to-next-Thursday (7 days).
    Pattern: gta-online-weekly-update-april-23rd-30th-2026/
             gta-online-weekly-update-march-26th-april-2nd-2026/
    """
    days_since_thursday = (now.weekday() - 3) % 7
    thursday = now - datetime.timedelta(days=days_since_thursday)
    end_date  = thursday + datetime.timedelta(days=7)

    start_month = thursday.strftime('%B').lower()
    end_month   = end_date.strftime('%B').lower()
    start_day   = ordinal(thursday.day)
    end_day     = ordinal(end_date.day)
    year        = thursday.year

    if start_month == end_month:
        slug = f"{start_month}-{start_day}-{end_day}-{year}"
    else:
        slug = f"{start_month}-{start_day}-{end_month}-{end_day}-{year}"

    url = f"https://fandomwire.com/gta-online-weekly-update-{slug}/"
    print(f"  Fandomwire URL: {url}")
    return url

# ── Freshness checks ──────────────────────────────────────────────────

def is_current_week(label):
    """True if label matches the current real-world GTA week (within 7 days)."""
    if not label:
        return False
    now = datetime.datetime.utcnow()
    month_map = {
        'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
        'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12
    }
    label_up    = label.upper()
    label_month = next((v for k, v in month_map.items() if k in label_up), None)
    if label_month is None:
        return True
    if label_month != now.month:
        # Allow month crossover (e.g. APR 28 checked on MAY 2)
        # Check if the other month is adjacent
        if abs(label_month - now.month) not in (1, 11):
            return False
    numbers = re.findall(r'\d+', label)
    if not numbers:
        return True
    try:
        start_day = int(numbers[0])
        scraped_date = datetime.datetime(now.year, label_month, start_day)
        delta = now - scraped_date
        return abs(delta.days) <= 7
    except Exception:
        return True

def is_fresh(scraped_label, stored_label):
    """True only if scraped label matches the current calendar week."""
    if not scraped_label:
        return False
    if not is_current_week(scraped_label):
        print(f"  [REJECT] '{scraped_label}' does not match current calendar week")
        return False
    return True

def is_data_actually_new(scraped, stored):
    """
    Even if the week label is current, check that key fields actually changed.
    Prevents saving mid-update ghost data where the date flipped but content didn't.
    Returns (bool, reason_string).
    """
    scraped_label = scraped.get('weekLabel', '')
    stored_label  = stored.get('weekLabel', '')
    norm = lambda s: re.sub(r'[^A-Z0-9]', '', s.upper())

    if scraped_label and stored_label and norm(scraped_label) == norm(stored_label):
        s_names = {b.get('name', '') for b in scraped.get('bonuses', [])}
        e_names = {b.get('name', '') for b in stored.get('bonuses', [])}
        if s_names and s_names != e_names:
            return True, "Same week but bonuses differ — updating"
        return False, "Same week, data unchanged"

    # ── Salvage Yard as primary canary ───────────────────────────────
    # Salvage cars are the most reliable freshness indicator — sites almost
    # never update the title/date without also updating salvage targets.
    if scraped.get('salvage') and stored.get('salvage'):
        scraped_cars = {s.get('car', '') for s in scraped['salvage']}
        stored_cars  = {s.get('car', '') for s in stored['salvage']}
        if scraped_cars and scraped_cars == stored_cars:
            print(f"  [WARN] Salvage cars identical {scraped_cars} — site is mid-update")
            return False, "Salvage yard unchanged — site is mid-update (canary check)"
        elif scraped_cars != stored_cars:
            print(f"  ✓ Salvage cars changed — confirmed fresh data")
            return True, "Salvage yard changed — confirmed fresh"

    checks_passed, checks_total = 0, 0

    if scraped.get('podium') and stored.get('podium'):
        checks_total += 1
        if scraped['podium'] != stored['podium']:
            checks_passed += 1
        else:
            print(f"  [WARN] Podium unchanged ({scraped['podium']}) — may be mid-update")

    if scraped.get('prizeRide') and stored.get('prizeRide'):
        checks_total += 1
        if scraped['prizeRide'] != stored['prizeRide']:
            checks_passed += 1
        else:
            print(f"  [WARN] Prize ride unchanged ({scraped['prizeRide']}) — may be mid-update")

    if scraped.get('bonuses'):
        checks_total += 1
        checks_passed += 1

    if checks_total == 0:
        return True, "No comparison data available"

    score = checks_passed / checks_total
    if score >= 0.5:
        return True, f"Quality {checks_passed}/{checks_total}"
    return False, f"Low quality {checks_passed}/{checks_total} — likely mid-update ghost"

# ── HTML section helpers ──────────────────────────────────────────────

def get_section_items(soup, pattern, include_paragraphs=True):
    """Find heading matching pattern, return li items until next heading."""
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        if not re.search(pattern, heading.get_text(strip=True), re.IGNORECASE):
            continue
        level = int(heading.name[1])
        items, seen = [], set()
        for el in heading.find_all_next():
            if el.name in ['h1', 'h2', 'h3', 'h4']:
                if int(el.name[1]) <= level:
                    break
            if el.name == 'li':
                text = el.get_text(separator=' ', strip=True)
                text = re.sub(r'\s*\(down from[^)]+\)', '', text, flags=re.IGNORECASE).strip()
                text = re.sub(r'\s*\(\$[^)]+\)', '', text).strip()
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
        parts, seen = [], set()
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

def clean(s):
    return re.sub(r'\s+', ' ', (s or '').replace('*', '')).strip()

def salvage_tier(name):
    n = name.lower()
    if re.search(r'cargo|ship|duggan|podium', n): return 3
    if re.search(r'mctony', n):                   return 2
    if re.search(r'gangbanger', n):               return 1
    return 2


# ── Improvement 1: Priority selector fallbacks for podium ────────────


def get_podium_vehicle(soup, full_text):
    """
    Try multiple selector strategies to find the podium vehicle.
    Priority selectors before falling back to regex.
    """
    for tag in ["strong", "span", "h3", "h4", "b"]:
        target = soup.find(tag, string=re.compile(r"Lucky\s+Wheel|Podium\s+Vehicle", re.I))
        if target:
            nxt = target.find_next(["span", "strong", "a", "li", "p"])
            if nxt:
                text = clean(nxt.get_text())
                if 3 < len(text) < 50 and text[0].isupper():
                    return text
    lw_section = get_section_text(soup, r"lucky\s+wheel|podium\s+vehicle")
    if lw_section:
        m = re.search(r"(?:Podium|Lucky\s+Wheel)[:\s]+([A-Z][A-Za-z\s]+?)(?:\n|,|\.)", lw_section, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    for pattern in [
        r"Lucky\s+Wheel\s+Podium\s+Vehicle[:\s]+([A-Z][A-Za-z\s]+?)(?:\n|,|\.)",
        r"Lucky\s+Wheel[:\s]+([A-Z][A-Za-z\s]+?)(?:\n|,)",
        r"Podium\s+Vehicle[:\s]+([A-Z][A-Za-z\s]+?)(?:\n|,|\.)",
    ]:
        m = re.search(pattern, full_text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""

def parse_tiered_multiplier(text):
    """
    Detect "4x for GTA+ / 2x for everyone" patterns.
    Returns dict with base multiplier and optional gta_plus_multiplier.
    """
    gta_plus_m = re.search(r'([2-9]|10)[x×].*?GTA\+', text, re.IGNORECASE)
    standard_m = re.search(r'([2-9]|10)[x×](?!.*GTA\+)', text, re.IGNORECASE)

    if gta_plus_m and standard_m:
        gta_mult  = int(gta_plus_m.group(1))
        base_mult = int(standard_m.group(1))
        if gta_mult != base_mult:
            return {'multiplier': min(gta_mult, base_mult), 'gta_plus_multiplier': max(gta_mult, base_mult)}
    return None

# ── Parser ────────────────────────────────────────────────────────────

def parse(html):
    soup = BeautifulSoup(html, 'html.parser')

    for tag in soup.find_all(['nav', 'footer', 'aside', 'script', 'style', 'header']):
        tag.decompose()
    for el in soup.find_all(class_=re.compile(r'toc|table.of.content|ez-toc', re.I)):
        el.decompose()
    for el in soup.find_all(id=re.compile(r'toc|table.of.content', re.I)):
        el.decompose()
    for ul in soup.find_all('ul'):
        links = ul.find_all('a')
        lis   = ul.find_all('li', recursive=False)
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

    # ── Week label ──
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

    # ── Challenge ──
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

    # ── Bonus Money ──
    SKIP_WORDS = ['log in', 'login', 'receive', 'subscribers', 'peyote plants',
                  'outfit', 'email', 'rockstar propaganda', 'tee', 'return']

    for mult, pattern in [
        (10, r'(?:10[x×]|ten\s+times).*(?:money|bonus|reward|gta|rp)'),
        (4,  r'(?:quadruple|4[x×]).*(?:money|bonus|reward|gta|rp)'),
        (3,  r'(?:triple|3[x×]).*(?:money|bonus|reward|gta|rp)'),
        (2,  r'(?:double|2[x×]).*(?:money|bonus|reward|gta|rp)'),
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
                if any(w in name.lower() for w in SKIP_WORDS):
                    continue
                key = f"{mult}:{name}"
                if not any(f"{x['multiplier']}:{x['name']}" == key for x in data['bonuses']):
                    # Check for tiered GTA+ multiplier in same bullet
                    # e.g. "4x for GTA+ / 2x for everyone else"
                    tiered = parse_tiered_multiplier(b)
                    if tiered and tiered['multiplier'] != mult:
                        # Add base multiplier for regular players
                        base_key = f"{tiered['multiplier']}:{name}"
                        if not any(f"{x['multiplier']}:{x['name']}" == base_key for x in data['bonuses']):
                            data['bonuses'].append({
                                'multiplier': tiered['multiplier'],
                                'name': name,
                                'note': note,
                                'gtaPlusMultiplier': tiered.get('gta_plus_multiplier')
                            })
                    else:
                        data['bonuses'].append({'multiplier': mult, 'name': name, 'note': note})

    # Inline high-multiplier catch (10x mentioned in paragraph, not heading)
    for mult_val, mult_re in [(10, r'10[Xx×]'), (8, r'8[Xx×]')]:
        for m in re.finditer(mult_re + r'\s+(?:GTA\$\s+and\s+RP\s+(?:on|for)\s+)?([A-Z][A-Za-z\s\']+?)(?:\.|,|\n)', full_text):
            name = m.group(1).strip().rstrip('.')
            if 3 < len(name) < 80 and not any(w in name.lower() for w in SKIP_WORDS):
                key = f"{mult_val}:{name}"
                if not any(f"{x['multiplier']}:{x['name']}" == key for x in data['bonuses']):
                    data['bonuses'].append({'multiplier': mult_val, 'name': name, 'note': 'Event bonus'})

    # ── Salvage ──
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

    # ── Discounts ──
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        heading_text = heading.get_text(strip=True)
        pct_m = re.search(r'(\d+)%\s*[Oo]ff', heading_text)
        if not pct_m:
            continue
        pct = int(pct_m.group(1))
        cat_raw = re.sub(r'\d+%\s*[Oo]ff\s*', '', heading_text).strip()
        cat_raw = re.sub(r'^[\(\)]+$', '', cat_raw).strip()
        cat_raw = re.sub(r'^(?:Business\s+Discounts?|Vehicle\s+Discounts?|Discounts?)\s*', '', cat_raw, flags=re.IGNORECASE).strip()
        cat_raw = re.sub(r'\(\s*\)', '', cat_raw).strip()
        cat = clean(cat_raw) or 'Various Vehicles'
        level = int(heading.name[1])
        items, seen = [], set()
        for el in heading.find_all_next():
            if el.name in ['h1', 'h2', 'h3', 'h4'] and int(el.name[1]) <= level:
                break
            if el.name == 'li':
                text = el.get_text(separator=' ', strip=True)
                if text and text not in seen and 2 < len(text) < 100:
                    items.append(text)
                    seen.add(text)
        existing_disc = next((d for d in data['discounts'] if d['pct'] == pct), None)
        if existing_disc:
            if len(items) > len(existing_disc['items']):
                existing_disc['items'] = items
                existing_disc['category'] = cat
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

    # ── Gun Van ──
    gv_items = get_section_items(soup, r'gun\s+van', include_paragraphs=False)
    for b in gv_items:
        b = b.replace('**', '').strip()
        plus  = bool(re.search(r'GTA\+|Members?', b, re.IGNORECASE))
        free  = bool(re.search(r'\bfree\b|100%', b, re.IGNORECASE))
        pct_m = re.search(r'(\d+)%\s*[Oo]ff', b, re.IGNORECASE)
        name = b
        name = re.sub(r'(?:Free\s+or\s+)?100%\s*[Oo]ff\s*', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\d+%\s*[Oo]ff[:\s]*', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\bFree\b[:\s]*', '', name, flags=re.IGNORECASE)
        name = re.sub(r'GTA\+|\bMembers?\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'[-–:]', '', name).strip()
        if not (3 <= len(name) <= 50):
            continue
        deal = 'FREE' if free else (f"{pct_m.group(1)}% OFF" if pct_m else '')
        if deal:
            data['gunVan'].append({'name': name, 'deal': deal, 'gtaPlus': plus})

    # ── Podium (selector fallbacks for reliability) ──
    data['podium'] = get_podium_vehicle(soup, full_text)

    # ── Prize Ride ──
    m = (
        re.search(r'to\s+win\s+the\s+([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,|\.)', full_text, re.IGNORECASE) or
        re.search(r'Prize\s+Ride[:\s]+(?!Challenge\b)([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,)', full_text, re.IGNORECASE)
    )
    if m:
        candidate = m.group(1).strip()
        if candidate.lower() not in ('challenge', 'the', 'a', 'an', ''):
            data['prizeRide'] = candidate
    if not data['prizeRide']:
        m = re.search(r'(?:Car\s+Meet\s+)?Prize\s+Ride[:\s]+([A-Z][A-Za-z\s]{2,40}?)\s*[-–]', full_text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if candidate.lower() not in ('challenge', 'the', 'a', 'an', ''):
                data['prizeRide'] = candidate

    # ── FIB File ──
    m = (
        re.search(r'(?:FIB\s+Priority\s+File|Priority\s+File)[:\s]+(?:the\s+)?([A-Z][A-Za-z\s]+File)', full_text, re.IGNORECASE) or
        re.search(r'The\s+([A-Z][A-Za-z\s]+File)\s*[–\-]', full_text, re.IGNORECASE)
    )
    if m:
        data['fibFile'] = m.group(1).strip()

    # ── Most Wanted (scoped to section + date validation) ──
    # Scope to Bail Office / Most Wanted section only — avoids sidebar pollution
    mw_text = get_section_text(soup, r'most\s+wanted|bail\s+office\s+bounty|bail\s+office\s+target')
    if not mw_text:
        mw_text = full_text  # fallback to full text if section not found
    now_utc = datetime.datetime.utcnow()
    for m in re.finditer(
        r'([A-Z][a-z]+ [A-Z][a-z\']+(?:\s+[A-Z][a-z]+)?)\s*[-–]\s*'
        r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+)\s*[-–]\s*(?:GTA)?\$?([\d,]+)',
        mw_text
    ):
        name   = m.group(1).strip()
        date   = m.group(2).strip()
        reward = '$' + m.group(3)
        # Validate date is within 14 days of now (discard stale archive entries)
        try:
            month_map_mw = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                            'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
            parts = date.split()
            mw_month = month_map_mw.get(parts[0], 0)
            mw_day   = int(parts[1]) if len(parts) > 1 else 0
            if mw_month and mw_day:
                mw_date = datetime.datetime(now_utc.year, mw_month, mw_day)
                if abs((now_utc - mw_date).days) > 14:
                    continue  # Skip stale dates from sidebars/archives
        except Exception:
            pass
        if not any(t['name'] == name and t['date'] == date for t in data['mostWanted']):
            data['mostWanted'].append({'name': name, 'date': date, 'reward': reward})
    data['mostWanted'].sort(key=lambda x: int(re.sub(r'\D', '', x['date']) or '0'))

    # ── LS Car Meet ──
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
        # Segregate standard test rides from HSW premium test ride
        # Filter out premium test car if it appears in the test track list
        premium_car = data['carMeet'].get('premiumTest', '')
        m = re.search(r'Test\s+(?:Vehicles?|Track|Rides?)[:\s]+((?:[^\n]+\n?){1,6})', cm_text, re.IGNORECASE)
        if m:
            rides = []
            for line in m.group(1).split('\n'):
                line = re.sub(r'[-–:].+$', '', line.strip().lstrip('-•* ')).strip()
                # Skip if it's the premium test car or a generic heading
                if 2 < len(line) < 60 and line != premium_car and line.lower() not in ('test vehicles', 'test track', 'test rides'):
                    rides.append(line)
            # Dedupe preserving order
            seen_rides = set()
            unique_rides = []
            for r in rides:
                if r not in seen_rides:
                    seen_rides.add(r)
                    unique_rides.append(r)
            data['carMeet']['testRides'] = unique_rides[:3]

    pdm_items = get_section_items(soup, r'Premium\s+Deluxe\s+Motorsport', include_paragraphs=False)
    data['carMeet']['pdm'] = [re.sub(r'\s*\(\d+%.*?\)', '', v).strip() for v in pdm_items if 2 < len(v) < 60][:8]

    la_items = get_section_items(soup, r'Luxury\s+Autos', include_paragraphs=False)
    data['carMeet']['luxuryAutos'] = [v for v in la_items if 2 < len(v) < 60][:6]

    return data

# ── Fandomwire supplement ─────────────────────────────────────────────

def supplement_from_fandomwire(parsed, now, existing):
    """Fetch Fandomwire article and fill in any missing fields."""
    url = find_fandomwire_url(now)
    html = fetch(url)
    if not html or len(html) < 1000:
        print("  [Supplement] Fandomwire fetch failed")
        return parsed

    supp = parse(html)
    print(f"  [Supplement] Fandomwire — podium:'{supp.get('podium')}' "
          f"prizeRide:'{supp.get('prizeRide')}' mostWanted:{len(supp.get('mostWanted', []))}")

    if not parsed.get('podium') and supp.get('podium'):
        parsed['podium'] = supp['podium']
        print(f"  [Supplement] podium filled: {parsed['podium']}")

    if not parsed.get('prizeRide') and supp.get('prizeRide'):
        parsed['prizeRide'] = supp['prizeRide']
        print(f"  [Supplement] prizeRide filled: {parsed['prizeRide']}")

    if supp.get('mostWanted'):
        parsed['mostWanted'] = supp['mostWanted']
        print(f"  [Supplement] mostWanted: {len(parsed['mostWanted'])} targets")

    cm  = parsed.get('carMeet', {})
    scm = supp.get('carMeet', {})
    for field in ['prizeRide', 'prizeReq', 'premiumTest', 'premiumTestNote', 'testRides', 'luxuryAutos', 'pdm']:
        if not cm.get(field) and scm.get(field):
            cm[field] = scm[field]
            print(f"  [Supplement] carMeet.{field} filled")
    parsed['carMeet'] = cm

    if not parsed.get('fibFile') and supp.get('fibFile'):
        parsed['fibFile'] = supp['fibFile']

    if not parsed.get('salvage') and supp.get('salvage'):
        parsed['salvage'] = supp['salvage']
        print(f"  [Supplement] salvage filled: {len(parsed['salvage'])} targets")

    return parsed

# ── Main ──────────────────────────────────────────────────────────────

def main():
    now = datetime.datetime.utcnow()
    print(f"[GTA HQ Scraper] Starting — {now.strftime('%Y-%m-%d %H:%M UTC')}")

    try:
        with open("weekly-data.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing_label = existing.get("weekLabel", "")
    except Exception:
        existing = {}
        existing_label = ""
    print(f"  Stored week: '{existing_label}'")

    parsed = None

    def try_parse(html, url, label):
        if not html or len(html) < 1000:
            print("  [SKIP] Response too short/empty")
            return None
        result = parse(html)
        scraped_label = result.get('weekLabel', '')

        # Synthesize label from URL if empty (PCQuest "april-23-2026" pattern)
        if not scraped_label and str(now.year) in url:
            now_month_abbr = now.strftime("%B").lower()[:3]
            if now_month_abbr in url.lower():
                day_m = re.search(
                    r'(january|february|march|april|may|june|july|august|september|october|november|december)-(\d+)-(\d{4})',
                    url.lower()
                )
                if day_m:
                    month_map = {
                        'january':1,'february':2,'march':3,'april':4,
                        'may':5,'june':6,'july':7,'august':8,
                        'september':9,'october':10,'november':11,'december':12
                    }
                    month_num = month_map[day_m.group(1)]
                    start_day = int(day_m.group(2))
                    year_num  = int(day_m.group(3))
                    import datetime as _dt
                    start = _dt.datetime(year_num, month_num, start_day)
                    end   = start + _dt.timedelta(days=6)
                    end_str = str(end.day)
                    if end.month != start.month:
                        end_str = end.strftime('%b').upper() + ' ' + end_str
                    synthesized = start.strftime('%b').upper() + ' ' + str(start_day) + ' \u2013 ' + end_str
                    result['weekLabel'] = synthesized
                    scraped_label = synthesized
                    print(f"  Synthesized label: '{scraped_label}'")

        has_data = bool(result.get('bonuses') or result.get('discounts') or scraped_label)
        if not has_data:
            print("  [SKIP] No useful data")
            return None
        if not is_fresh(scraped_label, existing_label):
            print(f"  [SKIP] Not current week: '{scraped_label}'")
            return None
        is_new, reason = is_data_actually_new(result, existing)
        if not is_new:
            print(f"  [SKIP] Quality check: {reason}")
            return None
        print(f"  ✓ Valid! Week:'{scraped_label}' — {reason}")
        return result

    # ── Strategy 1: PCQuest (new URL every week) ──────────────────────
    print("\n── Strategy 1: PCQuest ──")
    pcq_month = now.strftime("%B").lower()
    pcq_search = f"https://www.pcquest.com/gaming/?s=gta+online+weekly+update+{pcq_month}+{now.year}"
    pcq_html = fetch(pcq_search)
    if pcq_html:
        pcq_urls = re.findall(r'href="(https://www\.pcquest\.com/gaming/gta-online-weekly-update[^"]+)"', pcq_html)
        for url in pcq_urls[:3]:
            print(f"  Trying: {url}")
            result = try_parse(fetch(url), url, 'PCQuest')
            if result:
                parsed = result
                break

    # ── Strategy 2: Rolling URLs ──────────────────────────────────────
    if not parsed:
        print("\n── Strategy 2: Rolling URLs ──")
        for url in ROLLING_SOURCES:
            print(f"  Trying {url} …")
            result = try_parse(fetch(url), url, 'Rolling')
            if result:
                parsed = result
                break

    # ── Strategy 3: Fandomwire direct URL ────────────────────────────
    if not parsed:
        print("\n── Strategy 3: Fandomwire direct ──")
        fw_url = find_fandomwire_url(now)
        result = try_parse(fetch(fw_url), fw_url, 'Fandomwire')
        if result:
            parsed = result

    # ── No fresh data — use stored if it's current week ───────────────
    if not parsed:
        if existing_label and is_current_week(existing_label):
            print(f"\n[INFO] Stored '{existing_label}' is current week — supplementing gaps only")
            parsed = dict(existing)
        else:
            print("\n[INFO] No fresh data found. Sites may not have published yet.")
            print("       Cron retries at 13:00 and 16:00 UTC today.")
            sys.exit(0)

    # ── Supplement from Fandomwire ────────────────────────────────────
    print("\n── Supplementing from Fandomwire ──")
    parsed = supplement_from_fandomwire(parsed, now, existing)

    # ── Debug summary ─────────────────────────────────────────────────
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
    print(f"  fibFile    = '{parsed['fibFile']}'")

    # ── Merge & save ──────────────────────────────────────────────────
    week_changed = bool(
        parsed.get('weekLabel') and existing.get('weekLabel') and
        parsed['weekLabel'] != existing.get('weekLabel')
    )

    def pick(s, e, time_sensitive=False):
        if time_sensitive and week_changed:
            return s if s else None
        if isinstance(s, list):
            if not s: return e
            if not e: return s
            return s if len(s) >= max(1, len(e) // 2) else e
        if isinstance(s, dict):
            return s if any(s.values()) else e
        return s if s else e

    output = {
        "_updated":    now.strftime("%Y-%m-%d"),
        "weekLabel":   pick(parsed["weekLabel"],   existing.get("weekLabel",   "")),
        "challenge":   pick(parsed["challenge"],   existing.get("challenge",   {})),
        "bonuses":     pick(parsed["bonuses"],     existing.get("bonuses",     [])),
        "newVehicles": pick(parsed["newVehicles"], existing.get("newVehicles", [])),
        "discounts":   pick(parsed["discounts"],   existing.get("discounts",   [])),
        "podium":      pick(parsed["podium"],      existing.get("podium",      ""), time_sensitive=True),
        "prizeRide":   pick(parsed["prizeRide"],   existing.get("prizeRide",   ""), time_sensitive=True),
        "salvage":     pick(parsed["salvage"],     existing.get("salvage",     [])),
        "gunVan":      pick(parsed["gunVan"],      existing.get("gunVan",      [])),
        "fibFile":     pick(parsed["fibFile"],     existing.get("fibFile",     "")),
        "mostWanted":  pick(parsed["mostWanted"],  existing.get("mostWanted",  []), time_sensitive=True),
        "carMeet": {
            "prizeRide":       pick(parsed["carMeet"]["prizeRide"],       existing.get("carMeet", {}).get("prizeRide",       "")),
            "prizeReq":        pick(parsed["carMeet"]["prizeReq"],        existing.get("carMeet", {}).get("prizeReq",        "")),
            "premiumTest":     pick(parsed["carMeet"]["premiumTest"],     existing.get("carMeet", {}).get("premiumTest",     "")),
            "premiumTestNote": pick(parsed["carMeet"]["premiumTestNote"], existing.get("carMeet", {}).get("premiumTestNote", "")),
            "testRides":       pick(parsed["carMeet"]["testRides"],       existing.get("carMeet", {}).get("testRides",       [])),
            "luxuryAutos":     pick(parsed["carMeet"]["luxuryAutos"],     existing.get("carMeet", {}).get("luxuryAutos",     [])),
            "pdm":             pick(parsed["carMeet"]["pdm"],             existing.get("carMeet", {}).get("pdm",             [])),
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
