"""
export_fights_odds.py — combat sports (UFC/MMA, boxing) via fighter Elo + The Odds API.

  * history   -> ESPN combat scoreboards (espn_loader.fetch_fights), cached to data/raw
  * fair line -> fighter_elo (global Elo win probability)
  * fixtures + odds -> The Odds API (h2h, 2-way match winner)
Builds the "Match winner" group in Python (model p + market odds in the outs), like
the tennis block, so the frontend renders it with value chips. NOT backtested
(honest "model live, track record pending" addition).

Env: ODDS_API_KEY
"""
import os, sys, json, time, re, unicodedata, urllib.request, urllib.parse, difflib
from datetime import datetime, date, timedelta
import pandas as pd

from config import RAW, DATA_JS, SRC

RAW.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SRC))
try:
    from config import load_env; load_env()
except Exception:
    pass
from models import fighter_elo
from sources import espn_loader

KEY = os.environ.get("ODDS_API_KEY")
API = "https://api.the-odds-api.com/v4"
HD = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

LEAGUES = [
    {"key": "mma_mixed_martial_arts", "espn": "mma/ufc", "label": "UFC", "block": "mma",
     "sport_label": "MMA", "hist_days": 1100},
    {"key": "boxing", "espn": "boxing", "label": "Boxing", "block": "boksz",
     "sport_label": "Boxing", "hist_days": 1100},
]

ALIASES = {}


def _pnorm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _active(keys):
    try:
        sports = _get(f"{API}/sports?apiKey={KEY}")
    except Exception:
        return set()
    return {s["key"] for s in sports if s.get("active") and s["key"] in keys}


def load_history(espn_path, cache, days):
    p = RAW / cache
    if not p.exists():
        end = date.today(); start = end - timedelta(days=days)
        df = espn_loader.fetch_fights(espn_path, start, end)
        if df is None or not len(df):
            return None
        df.to_csv(p, index=False)
    return pd.read_csv(p)


def _h2h(ev):
    a, b = ev.get("home_team"), ev.get("away_team")
    A, B = [], []
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h":
                continue
            for o in mk.get("outcomes", []):
                if o.get("name") == a: A.append(o.get("price"))
                elif o.get("name") == b: B.append(o.get("price"))
    med = lambda xs: round(sorted(xs)[len(xs) // 2], 2) if xs else None
    return med(A), med(B)


def _tokset_match(n, norm_index):
    """Order-independent name match: 'Weili Zhang'<->'Zhang Weili', 'Paulo Henrique Costa'<->'Paulo Costa'."""
    ns = set(n.split())
    best, bo = None, 0
    for key, f in norm_index.items():
        fs = set(key.split())
        sh = ns & fs
        if len(sh) >= 2 and (ns <= fs or fs <= ns) and len(sh) > bo:
            best, bo = f, len(sh)
    return best


def map_name(name, fighters, norm_index):
    if name in fighters:
        return name
    if name in ALIASES and ALIASES[name] in fighters:
        return ALIASES[name]
    n = _pnorm(name)
    if n in norm_index:
        return norm_index[n]
    ts = _tokset_match(n, norm_index)
    if ts:
        return ts
    cand = difflib.get_close_matches(n, list(norm_index.keys()), n=1, cutoff=0.88)
    return norm_index[cand[0]] if cand else None


def _fights_fixtures_only(lg, model, fighters, norm_index, data):
    """No odds (quota out): build cards from free ESPN upcoming fights, Elo read only."""
    ups = espn_loader.fetch_upcoming(lg["espn"], days_ahead=14, fights=True)
    out = []
    for u in ups:
        fa = map_name(u["home"], fighters, norm_index); fb = map_name(u["away"], fighters, norm_index)
        if not fa or not fb:
            continue
        pa = fighter_elo.predict(model, fa, fb)
        try:
            d = datetime.fromisoformat(u["date"].replace("Z", "+00:00"))
            dstr = f"{HD[d.weekday()]} {d.month}.{d.day:02d}"; ko = u["date"]
        except Exception:
            dstr, ko = "", None
        out.append({"home": u["home"], "away": u["away"], "league": lg["label"], "date": dstr, "kickoff": ko,
                    "insight": f"Elo: {pa * 100:.0f}% / {(1 - pa) * 100:.0f}%.",
                    "base": [{"name": "Match winner", "grid": "c2",
                              "outs": [{"k": u["home"], "p": round(pa, 3)}, {"k": u["away"], "p": round(1 - pa, 3)}]}],
                    "extra": []})
    if not out:
        return None
    print(f"  {lg['label']}: {len(out)} upcoming fights from ESPN (model only — no odds)")
    data.setdefault(lg["block"], {"label": lg["sport_label"], "matches": []})
    data[lg["block"]]["matches"] += out
    return out


def build_league(lg, data):
    df = load_history(lg["espn"], f'{lg["key"]}_hist.csv', lg["hist_days"])
    if df is None or not len(df):
        print(f"  {lg['label']}: no history — skipped"); return
    model = fighter_elo.build_elo(df)
    if not model:
        print(f"  {lg['label']}: model fit failed — skipped"); return
    fighters = model["fighters"]; norm_index = {_pnorm(f): f for f in fighters}
    q = urllib.parse.urlencode({"apiKey": KEY, "regions": "us", "markets": "h2h", "oddsFormat": "decimal"})
    try:
        evs = _get(f"{API}/sports/{lg['key']}/odds?{q}")
    except Exception as e:
        print(f"  {lg['label']}: odds unavailable ({str(e)[:30]}) — ESPN fixtures (model only)")
        return _fights_fixtures_only(lg, model, fighters, norm_index, data)
    out, missing = [], set()
    for ev in evs:
        ad, bd = ev.get("home_team"), ev.get("away_team")
        fa = map_name(ad, fighters, norm_index)
        fb = map_name(bd, fighters, norm_index)
        if not fa or not fb:
            if not fa: missing.add(ad)
            if not fb: missing.add(bd)
            continue
        pa = fighter_elo.predict(model, fa, fb)
        oa, ob = _h2h(ev)
        outs = [{"k": ad, "p": round(pa, 3)}, {"k": bd, "p": round(1 - pa, 3)}]
        if oa: outs[0]["mkt"] = oa
        if ob: outs[1]["mkt"] = ob
        d = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        out.append({"home": ad, "away": bd, "league": lg["label"],
                    "date": f"{HD[d.weekday()]} {d.month}.{d.day:02d}", "kickoff": ev["commence_time"],
                    "insight": f"Elo: {pa * 100:.0f}% / {(1 - pa) * 100:.0f}%.",
                    "base": [{"name": "Match winner", "grid": "c2", "outs": outs}], "extra": []})
    print(f"  {lg['label']}: {len(out)} fights from {len(df)} historical bouts" +
          (f" | unmatched: {sorted(missing)}" if missing else ""))
    data.setdefault(lg["block"], {"label": lg["sport_label"], "matches": []})
    data[lg["block"]]["matches"] += out
    return out


def main():
    if os.environ.get("FAIRLINE_NO_ODDS"):
        print("  Combat sports: skipped (--no-odds, 0 credits; existing data kept)."); return
    if not KEY:
        print("  Combat sports: ODDS_API_KEY not set — skipped."); return
    active = _active({lg["key"] for lg in LEAGUES})
    print(f"  Combat sports: active keys now: {sorted(active)}")
    djs = DATA_JS; data = {}
    if djs.exists():
        s = djs.read_text(encoding="utf-8"); data = json.loads(s[s.find("{"):s.rfind("}") + 1])
    old_blocks = {lg["block"]: data.get(lg["block"]) for lg in LEAGUES}
    for lg in LEAGUES:
        data.pop(lg["block"], None)
    for lg in LEAGUES:
        if lg["key"] not in active:
            print(f"  {lg['label']}: not active now — skipped"); continue
        res = build_league(lg, data)
        if res is None:
            ob = old_blocks.get(lg["block"])
            prev = [m for m in ob["matches"] if m.get("league") == lg["label"]] if ob else []
            if prev:
                data.setdefault(lg["block"], {"label": lg["sport_label"], "matches": []})
                data[lg["block"]]["matches"] += prev
                print(f"  {lg['label']}: odds unavailable — kept {len(prev)} previous matches")
        time.sleep(0.3)
    djs.write_text("window.SPORTS_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    print("  Combat sports: data.js updated.")


if __name__ == "__main__":
    main()
