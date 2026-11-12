#!/usr/bin/env python3
"""Daily WA Legislature bill tracker (standard library only).
Run:  python tracker.py            (updates data.json)
      python tracker.py --inspect  (prints raw field names, for debugging)
Optional: set ANTHROPIC_API_KEY to get plain-language summaries of new/edited bills."""
import json, os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta

BIENNIUM = os.getenv("BIENNIUM", "2027-28")
YEARS = [int(y) for y in os.getenv("YEARS", "2027,2028").split(",")]
BASE = "https://wslwebservices.leg.wa.gov/LegislationService.asmx/"
DATA, MAX_SUMMARIES = "data.json", 60

TOPICS = {  # edit freely: a bill matches a topic if its title/description contains a keyword
    "Data centers": ["data center", "datacenter", "large load", "computing facilit", "server farm"],
    "Autonomous vehicles": ["autonomous", "driverless", "self-driving", "automated driving"],
    "Clean energy": ["clean energy", "renewable", "solar", "wind power", "battery", "storage", "nuclear",
                     "hydrogen", "electrification", "transmission", "grid", "decarbon", "clean fuel", "carbon", "emission"],
    "Water & wastewater": ["water", "wastewater", "stormwater", "sewer", "drinking"],
    "Utilities & infrastructure": ["utility", "utilities", "infrastructure", "electric", "natural gas", "broadband"],
    "Environment & resources": ["environment", "climate", "forest", "wildfire", "salmon", "habitat",
                                "pollution", "waste", "recycl", "conservation", "resource"],
}

def call(method, **params):
    url = BASE + method + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=90) as r:
        return ET.fromstring(r.read())

def kids(el):
    return {c.tag.split("}")[-1]: (c.text or "").strip() for c in el}

def records(root):
    out = {}
    for el in root.iter():
        k = kids(el)
        if k.get("BillId"):
            rec = out.setdefault(k["BillId"], {})
            for key, v in k.items():
                if v and key not in rec: rec[key] = v
    return out

def latest_changes(root):
    out = {}
    for el in root.iter():
        k = kids(el)
        if k.get("BillId") and k.get("ActionDate") and k["ActionDate"] >= out.get(k["BillId"], {}).get("ActionDate", ""):
            out[k["BillId"]] = k
    return out

def first(d, *names):
    return next((d[n] for n in names if d.get(n)), "")

def summarize(b, kind):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key: return b["desc"][:300]
    prompt = (f"Washington State bill {b['id']}: {b['title']}. Description: {b['desc']}. "
              f"Latest action: {b['status']}. This bill is {kind} today. In two plain sentences for a city "
              "sustainability policy analyst, say what the bill does and, if edited, what the latest action means. "
              "Stick to the text given; do not speculate.")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages",
        json.dumps({"model": "claude-haiku-5-5", "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}]}).encode(),
        {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))["content"][0]["text"].strip()
    except Exception as e:
        print("summary failed:", e); return b["desc"][:300]

def main():
    db = json.load(open(DATA)) if os.path.exists(DATA) else {}
    if db.get("sample") or "bills" not in db:
        db = {"bills": {}, "feed": [], "last_run": None}
    today = date.today()
    since = (date.fromisoformat(db["last_run"]) - timedelta(days=1)) if db["last_run"] \
        else date(int(BIENNIUM[:4]) - 1, 12, 1)
    base = {}
    for y in YEARS:
        try: base.update(records(call("GetLegislationByYear", year=y)))
        except Exception as e: print(f"year {y}: {e}")
    chg = latest_changes(call("GetLegislativeStatusChangesByDateRange", biennium=BIENNIUM,
                              beginDate=since.isoformat(), endDate=(today + timedelta(days=1)).isoformat()))
    if "--inspect" in sys.argv:
        for sample in (base, chg):
            print(json.dumps(next(iter(sample.values()), {}), indent=1))
        return
    feed, n = [], 0
    for bid in sorted(set(base) | set(chg)):
        r, c, old = base.get(bid, {}), chg.get(bid, {}), db["bills"].get(bid)
        title = first(r, "LongDescription", "ShortDescription", "LegalTitle", "Title") or (old or {}).get("title", bid)
        desc = first(r, "LegalTitle", "LongDescription", "ShortDescription") or (old or {}).get("desc", "")
        status = c.get("HistoryLine") or c.get("Status") or (old or {}).get("status", "")
        adate = c.get("ActionDate", "")[:10] or (old or {}).get("date", "")
        ver = f"{r.get('SubstituteVersion', '')}/{r.get('EngrossedVersion', '')}" if r else (old or {}).get("version", "")
        text = f"{title} {desc}".lower()
        topics = [t for t, kws in TOPICS.items() if any(re.search(r"\b" + re.escape(k), text) for k in kws)]
        num = (re.search(r"\d+", bid) or [""])[0]
        b = {"id": bid, "title": title, "desc": desc, "status": status, "date": adate, "version": ver,
             "topics": topics, "summary": (old or {}).get("summary", ""),
             "url": f"https://app.leg.wa.gov/billsummary?BillNumber={num}&Year={BIENNIUM[:4]}"}
        kind = "new" if not old else ("edited" if (old["status"], old["version"]) != (status, ver) else None)
        if kind and topics:
            b["summary"] = summarize(b, kind) if n < MAX_SUMMARIES else b["desc"][:300]
            n += 1
            feed.append({"date": today.isoformat(), "id": bid, "kind": kind, "status": status, "summary": b["summary"]})
        db["bills"][bid] = b
    db["feed"] = (feed + db["feed"])[:600]
    db["last_run"] = today.isoformat()
    json.dump(db, open(DATA, "w"), ensure_ascii=False)
    print(f"{len(db['bills'])} bills tracked, {len(feed)} new/edited topic matches today")

if __name__ == "__main__":
    main()
