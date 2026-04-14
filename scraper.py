#!/usr/bin/env python3
"""
GTA Online HQ — Weekly Data Scraper
Runs via GitHub Actions every Thursday after the weekly reset.
Scrapes TechWiser (primary) → Sportskeeda (fallback), writes weekly-data.json
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

# ── Helpers ───────────────────────────────────────────────────────────

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  [WARN] fetch failed for {url}: {e}")
        return None


def get_text(html):
    """Strip HTML tags, collapse whitespace, return clean plain text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["nav", "footer", "aside", "script", "style", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("*", "")).strip()


def extract_bullets(block):
    bullets = []
    for line in block.split("\n"):
        line = line.strip()
        if re.match(r"^[-•*+]", line) or re.match(r"^\d+\.", line):
            line = re.sub(r"^[-•*+]\s*", "", line)
            line = re.sub(r"^\d+\.\s*", "", line)
            line = line.replace("**", "").strip()
            if line:
                bullets.append(line)
    return bullets


def get_section(text, pattern):
    """Return the text block after the first heading matching pattern."""
    lines = text.split("\n")
    active = False
    body = []
    depth = 0
    for line in lines:
        t = line.strip()
        is_heading = (
            re.match(r"^#{1,4}\s", t) or
            (len(t) < 80 and t and t[0].isupper() and not re.search(r"[.!?,;]$", t))
        )
        heading_text = re.sub(r"^#+\s*", "", t)
        if is_heading:
            if re.search(pattern, heading_text, re.IGNORECASE):
                active = True
                depth = 0
                continue
            if active:
                depth += 1
                if depth > 3:
                    break
        if active:
            body.append(line)
    return "\n".join(body).strip()


def heading_multiplier(h):
    h = h.lower()
    if re.search(r"quadruple|4[x×]", h): return 4
    if re.search(r"triple|3[x×]", h):    return 3
    if re.search(r"double|2[x×]", h):    return 2
    return 0


def salvage_tier(name):
    n = name.lower()
    if re.search(r"cargo|ship|duggan|podium", n): return 3
    if re.search(r"mctony",                   n): return 2
    if re.search(r"gangbanger",               n): return 1
    return 2


# ── Parser ────────────────────────────────────────────────────────────

def parse(text):
    data = {
        "weekLabel":    "",
        "challenge":    {"desc": "", "reward": ""},
        "bonuses":      [],
        "newVehicles":  [],
        "discounts":    [],
        "podium":       "",
        "prizeRide":    "",
        "salvage":      [],
        "gunVan":       [],
        "fibFile":      "",
        "mostWanted":   [],
        "carMeet": {
            "prizeRide": "", "prizeReq": "",
            "premiumTest": "", "premiumTestNote": "",
            "testRides": [], "luxuryAutos": [], "pdm": [],
        },
    }

    # ── Week label ──
    m = re.search(r"\(([A-Z][a-z]+ \d+\s*[–\-]\s*(?:[A-Z][a-z]+ )?\d+,?\s*\d{4})\)", text)
    if m:
        label = m.group(1)
        label = re.sub(r",?\s*\d{4}", "", label)
        label = re.sub(r"(\w{3})\w*\s+(\d+)", lambda x: x.group(1).upper() + " " + x.group(2), label)
        data["weekLabel"] = label.strip()

    # ── Challenge ──
    m = re.search(r"Weekly Challenge[^\n]*\n+([^\n]{10,120})\n+([^\n]{5,120})", text, re.IGNORECASE)
    if m:
        data["challenge"]["desc"]   = clean(m.group(1))
        data["challenge"]["reward"] = clean(m.group(2))
    else:
        m = re.search(r"(?:Secure|Complete|Win)[^\n]{3,80}to (?:receive|get|earn)[^\n]{5,120}", text, re.IGNORECASE)
        if m:
            data["challenge"]["desc"] = clean(m.group(0))

    # ── Bonus money ──
    sections = []
    current = None
    for line in text.split("\n"):
        t = line.strip()
        if not t:
            continue
        is_h = (
            re.match(r"^#{1,4}\s", t) or
            (len(t) < 80 and t[0].isupper() and not re.search(r"[.!?,;]$", t) and
             re.search(r"Bonuses?|Money|Double|Triple|Quadruple", t, re.IGNORECASE))
        )
        if is_h:
            if current:
                sections.append(current)
            current = {"heading": re.sub(r"^#+\s*", "", t), "body": ""}
        elif current:
            current["body"] += line + "\n"
    if current:
        sections.append(current)

    for sec in sections:
        mult = heading_multiplier(sec["heading"])
        if not mult:
            continue
        for b in extract_bullets(sec["body"]):
            name = re.sub(r"[-–]\s*.+$", "", b).replace("*", "").strip()
            note_m = re.search(r"[-–]\s*(.+)$", b)
            note = note_m.group(1).strip()[:60] if note_m else ""
            if 2 < len(name) < 80:
                key = f"{mult}:{name}"
                if not any(f"{x['multiplier']}:{x['name']}" == key for x in data["bonuses"]):
                    data["bonuses"].append({"multiplier": mult, "name": name, "note": note})

    # ── Salvage ──
    for m in re.finditer(
        r"(?:The\s+)?([\w\s]+Robbery)\s*[:–\-]\s*([A-Z][A-Za-z\s]+?)(?:\n|·|,|$)",
        text, re.MULTILINE
    ):
        robbery = "The " + m.group(1).strip().replace("The ", "")
        car = m.group(2).strip()
        if 2 < len(car) < 60 and not any(s["robbery"] == robbery for s in data["salvage"]):
            data["salvage"].append({"tier": salvage_tier(robbery), "robbery": robbery, "car": car})
    data["salvage"].sort(key=lambda x: x["tier"])

    # ── Discounts ──
    for m in re.finditer(r"(\d+)%\s*Off\s*([^\n]{0,50})\n((?:[-•*]\s*.+\n?){1,25})", text, re.IGNORECASE):
        pct   = int(m.group(1))
        cat   = clean(m.group(2)) or "Various Vehicles"
        items = [v for v in extract_bullets(m.group(3)) if 2 < len(v) < 60]
        if items:
            data["discounts"].append({"pct": pct, "category": cat, "until": "", "items": items})

    for m in re.finditer(
        r"([A-Z][A-Za-z\s]{3,30}(?:Office|Offices|Yard|Hangar|Bunker|Lab)s?)\s+(?:are|is)\s+(\d+)%\s*off",
        text, re.IGNORECASE
    ):
        cat = clean(m.group(1))
        pct = int(m.group(2))
        if not any(d["category"].startswith(cat.split()[0]) for d in data["discounts"]):
            data["discounts"].append({"pct": pct, "category": cat, "until": "", "items": []})

    # ── Gun Van ──
    gv_body = get_section(text, r"gun\s+van")
    for b in extract_bullets(gv_body):
        name = re.sub(r"[-–:*].+$", "", b).replace("GTA+", "").strip()
        if not (3 <= len(name) <= 50):
            continue
        free  = bool(re.search(r"free", b, re.IGNORECASE))
        pct_m = re.search(r"(\d+)%\s*off", b, re.IGNORECASE)
        plus  = bool(re.search(r"GTA\+", b, re.IGNORECASE))
        deal  = "FREE" if free else (f"{pct_m.group(1)}% OFF" if pct_m else "")
        if deal:
            data["gunVan"].append({"name": name, "deal": deal, "gtaPlus": plus})

    # ── Podium ──
    m = re.search(r"Lucky\s+Wheel[:\s·]+([A-Z][A-Za-z\s]+?)(?:\n|·|,)", text, re.IGNORECASE)
    if m:
        data["podium"] = m.group(1).strip()

    # ── Prize Ride ──
    m = (re.search(r"Prize\s+Ride(?:\s+Challenge)?[:\s]+(?:win\s+)?(?:the\s+)?([A-Z][A-Za-z\s]+?)(?:\n|·|,|to\s+win)", text, re.IGNORECASE) or
         re.search(r"to\s+win\s+the\s+([A-Z][A-Za-z\s]{3,40}?)(?:\n|·|,|\.)", text, re.IGNORECASE))
    if m:
        data["prizeRide"] = m.group(1).strip()

    # ── FIB File ──
    m = (re.search(r"(?:FIB\s+Priority\s+File|Priority\s+File)[:\s]+(?:the\s+)?([A-Z][A-Za-z\s]+File)", text, re.IGNORECASE) or
         re.search(r"The\s+([A-Z][A-Za-z\s]+File)\s*[–\-]", text, re.IGNORECASE))
    if m:
        data["fibFile"] = m.group(1).strip()

    # ── Most Wanted ──
    for m in re.finditer(
        r"([A-Z][a-z]+ [A-Z][a-z']+(?:\s+[A-Z][a-z]+)?)\s*[-–]\s*((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+)\s*[-–]\s*(?:GTA)?\$?([\d,]+)",
        text
    ):
        name   = m.group(1).strip()
        date   = m.group(2).strip()
        reward = "$" + m.group(3)
        if not any(t["name"] == name and t["date"] == date for t in data["mostWanted"]):
            data["mostWanted"].append({"name": name, "date": date, "reward": reward})
    data["mostWanted"].sort(key=lambda x: int(re.sub(r"\D", "", x["date"])))

    # ── LS Car Meet ──
    cm_body = get_section(text, r"LS\s+Car\s+Meet")
    if cm_body:
        if data["prizeRide"]:
            data["carMeet"]["prizeRide"] = data["prizeRide"]

        m = re.search(r"Prize\s+Ride[:\s]+(?:[^\n]*to\s+win\s+(?:the\s+)?)?([A-Z][A-Za-z\s]+?)(?:\n|·|,)", cm_body, re.IGNORECASE)
        if m:
            data["carMeet"]["prizeRide"] = re.sub(r"(?:top|place).+", "", m.group(1), flags=re.IGNORECASE).strip()

        m = re.search(r"((?:top|place)\s+\d+[^\n.]+?(?:\d+\s+days|a\s+row))", cm_body, re.IGNORECASE)
        if m:
            data["carMeet"]["prizeReq"] = m.group(1).strip()

        m = re.search(r"Premium\s+Test\s+(?:Ride|Drive)[:\s]+([A-Z][A-Za-z\s]+?)(?:\n|·|,|-)", cm_body, re.IGNORECASE)
        if m:
            data["carMeet"]["premiumTest"] = m.group(1).strip()
            note_m = re.search(r"(?:enhanced|PS5|Series|exclusive)[^\n]*", cm_body, re.IGNORECASE)
            if note_m:
                data["carMeet"]["premiumTestNote"] = clean(note_m.group(0))

        m = re.search(r"Test\s+(?:Vehicles?|Track|Rides?)[:\s]+((?:[^\n]+\n?){1,5})", cm_body, re.IGNORECASE)
        if m:
            data["carMeet"]["testRides"] = [
                re.sub(r"[-–:].+$", "", v).strip()
                for v in extract_bullets(m.group(1))
                if 2 < len(v) < 60
            ][:5]

    pdm_body = get_section(text, r"Premium\s+Deluxe\s+Motorsport")
    if pdm_body:
        data["carMeet"]["pdm"] = [
            re.sub(r"\s*\(\d+%.*?\)", "", v).strip()
            for v in extract_bullets(pdm_body)
            if 2 < len(v) < 60
        ][:8]

    la_body = get_section(text, r"Luxury\s+Autos")
    if la_body:
        data["carMeet"]["luxuryAutos"] = [v for v in extract_bullets(la_body) if 2 < len(v) < 60][:6]

    return data


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print(f"[GTA HQ Scraper] Starting — {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    parsed = None
    for url in SOURCES:
        print(f"  Trying {url} …")
        html = fetch(url)
        if not html:
            continue
        text = get_text(html)
        if len(text) < 500:
            print("  [WARN] Page text too short, skipping")
            continue
        result = parse(text)
        if result.get("weekLabel") or result.get("bonuses") or result.get("salvage"):
            parsed = result
            print(f"  ✓ Parsed successfully from {url}")
            print(f"    bonuses={len(parsed['bonuses'])} salvage={len(parsed['salvage'])} "
                  f"discounts={len(parsed['discounts'])} mostWanted={len(parsed['mostWanted'])}")
            break
        else:
            print("  [WARN] Parsed but no useful data found, trying next source")

    if not parsed:
        print("[ERROR] All sources failed. Keeping existing weekly-data.json unchanged.")
        sys.exit(1)

    # Load existing JSON so we can preserve any fields the scraper missed
    try:
        with open("weekly-data.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = {}

    # Merge: scraped data wins, fall back to existing for any empty field
    def pick(scraped_val, existing_val):
        if isinstance(scraped_val, list):
            return scraped_val if scraped_val else existing_val
        if isinstance(scraped_val, dict):
            return scraped_val if any(scraped_val.values()) else existing_val
        return scraped_val if scraped_val else existing_val

    output = {
        "_updated":   datetime.datetime.utcnow().strftime("%Y-%m-%d"),
        "weekLabel":  pick(parsed["weekLabel"],  existing.get("weekLabel",  "")),
        "challenge":  pick(parsed["challenge"],  existing.get("challenge",  {})),
        "bonuses":    pick(parsed["bonuses"],    existing.get("bonuses",    [])),
        "newVehicles":pick(parsed["newVehicles"],existing.get("newVehicles",[])),
        "discounts":  pick(parsed["discounts"],  existing.get("discounts",  [])),
        "podium":     pick(parsed["podium"],     existing.get("podium",     "")),
        "prizeRide":  pick(parsed["prizeRide"],  existing.get("prizeRide",  "")),
        "salvage":    pick(parsed["salvage"],    existing.get("salvage",    [])),
        "gunVan":     pick(parsed["gunVan"],     existing.get("gunVan",     [])),
        "fibFile":    pick(parsed["fibFile"],    existing.get("fibFile",    "")),
        "mostWanted": pick(parsed["mostWanted"], existing.get("mostWanted", [])),
        "carMeet":    {
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

    print(f"\n✅ weekly-data.json updated successfully!")
    print(f"   Week: {output['weekLabel']}")
    print(f"   Bonuses: {len(output['bonuses'])}")
    print(f"   Salvage targets: {len(output['salvage'])}")
    print(f"   Most Wanted: {len(output['mostWanted'])}")
    print(f"   Discounts: {len(output['discounts'])}")


if __name__ == "__main__":
    main()
