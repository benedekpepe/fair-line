"""
export_margin_odds.py — high-scoring sports via the margin model + The Odds API.

Starts with WNBA (in season now). The same code handles the whole margin family
(NBA, EuroLeague, NCAAB, NFL, NCAAF, AFL, NRL) — just add entries to LEAGUES.

For each league:
  * history  -> ESPN public scoreboard (espn_loader), cached to data/raw
  * fair line -> margin_model (normal margin/total) fitted on that history
  * fixtures + odds -> The Odds API (h2h, spreads, totals)
We build the market groups in Python (Moneyline 2-way, Spread, Total) with model
probabilities + market odds in the outs, exactly like the tennis block, so the
frontend renders them as-is with value chips. No frontend/sync changes needed.

NOTE: these sports are NOT yet ROI-backtested (no free historical odds), so they
are honest "model live, track record pending" additions.

Env: ODDS_API_KEY
"""
import os, sys, io, json, time, re, unicodedata, urllib.request, urllib.parse, difflib
from datetime import datetime, date, timedelta
from pathlib import Path
import pandas as pd

from config import PROJECT, RAW, DATA_JS, SRC

RAW.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SRC))
try:
    from config import load_env; load_env()
except Exception:
    pass
from models import margin_model
from sources import espn_loader

KEY = os.environ.get("ODDS_API_KEY")
API = "https://api.the-odds-api.com/v4"
HD = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

# Each league: Odds API key, ESPN path, league label, the sport block + its tab label.
# Leagues sharing a block (e.g. NBA+WNBA -> "kosar") merge into the same tab.
LEAGUES = [
    {"key": "basketball_wnba", "espn": "basketball/wnba", "label": "WNBA",
     "block": "kosar", "sport_label": "Basketball", "hist_days": 430},
    {"key": "aussierules_afl", "espn": "australian-football/afl", "label": "AFL",
     "block": "afl", "sport_label": "AFL", "hist_days": 430},
    {"key": "rugbyleague_nrl", "espn": "rugby-league/3", "label": "NRL",
     "block": "nrl", "sport_label": "NRL", "hist_days": 430},
    {"key": "baseball_mlb", "espn": "baseball/mlb", "label": "MLB",
     "block": "baseball", "sport_label": "Baseball", "hist_days": 250},
    # Off-season now -> "not active" until their season starts, then auto-activate:
    {"key": "basketball_nba", "espn": "basketball/nba", "label": "NBA",
     "block": "kosar", "sport_label": "Basketball", "hist_days": 260},
    {"key": "basketball_euroleague", "espn": "basketball/euroleague", "label": "EuroLeague",
     "block": "kosar", "sport_label": "Basketball", "hist_days": 300},
    {"key": "americanfootball_nfl", "espn": "football/nfl", "label": "NFL",
     "block": "amfoci", "sport_label": "Am. football", "hist_days": 430},
    {"key": "icehockey_nhl", "espn": "hockey/nhl", "label": "NHL",
     "block": "hoki", "sport_label": "Ice hockey", "hist_days": 260},
]

ALIASES = {}  # Odds-API-name -> our (ESPN/model) name, filled in as needed from the unmatched log


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
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
        df = espn_loader.fetch_results(espn_path, start, end)
        if df is None or not len(df):
            return None
        df.to_csv(p, index=False)
    return pd.read_csv(p)


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(sorted(xs)[len(xs) // 2], 2) if xs else None


def _h2h(ev):
    h, a = ev.get("home_team"), ev.get("away_team")
    H, A = [], []
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h":
                continue
            for o in mk.get("outcomes", []):
                if o.get("name") == h: H.append(o.get("price"))
                elif o.get("name") == a: A.append(o.get("price"))
    return _med(H), _med(A)


def _line_market(ev, key):
    """spreads/totals: pick the most-quoted line, return (line, home/over price, away/under price)."""
    from collections import defaultdict
    h, a = ev.get("home_team"), ev.get("away_team")
    lines = defaultdict(lambda: {"P1": [], "P2": []})
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != key:
                continue
            outs = {o.get("name"): o for o in mk.get("outcomes", [])}
            if key == "spreads":
                o1, o2 = outs.get(h), outs.get(a)
                if o1 and o2 and o1.get("point") is not None:
                    lines[o1["point"]]["P1"].append(o1.get("price")); lines[o1["point"]]["P2"].append(o2.get("price"))
            else:  # totals
                ov, un = outs.get("Over"), outs.get("Under")
                if ov and un and ov.get("point") is not None:
                    lines[ov["point"]]["P1"].append(ov.get("price")); lines[ov["point"]]["P2"].append(un.get("price"))
    if not lines:
        return None
    ln, d = max(lines.items(), key=lambda kv: len(kv[1]["P1"]))
    return ln, _med(d["P1"]), _med(d["P2"])


def _tok_match(n, teams):
    """Token-subset: the shorter name's tokens appear as a contiguous run in the longer one.
    Handles 'Carlton Blues'<->'Carlton' (leading) and 'Melbourne Storm'<->'Storm' (trailing)."""
    nt = n.split(); best, bk = None, 0
    for t in teams:
        tt = _norm(t).split()
        lng, sht = (nt, tt) if len(nt) >= len(tt) else (tt, nt)
        L = len(sht)
        if L >= 1 and any(lng[i:i + L] == sht for i in range(len(lng) - L + 1)) and L > bk:
            best, bk = t, L
    return best


def map_name(name, teams, norm_index):
    if name in teams:
        return name
    if name in ALIASES and ALIASES[name] in teams:
        return ALIASES[name]
    n = _norm(name)
    if n in norm_index:
        return norm_index[n]
    tm = _tok_match(n, teams)
    if tm:
        return tm
    cand = difflib.get_close_matches(n, list(norm_index.keys()), n=1, cutoff=0.86)
    return norm_index[cand[0]] if cand else None


def _sgn(x):
    return f"+{x}" if x > 0 else f"{x}"


POST_TOTAL_FACTOR = 0.96   # playoffs/finals: tighter defense, slower pace -> ~4% lower total (margin unchanged)


def _margin_fixtures_only(lg, model, teams, norm_index, sports_state):
    """No odds (quota out): build cards from free ESPN upcoming fixtures, model read only."""
    ups = espn_loader.fetch_upcoming(lg["espn"], days_ahead=10)
    out = []
    for u in ups:
        H = map_name(u["home"], teams, norm_index); A = map_name(u["away"], teams, norm_index)
        if not H or not A:
            continue
        tf = POST_TOTAL_FACTOR if u.get("post") else 1.0
        e = margin_model.expected(model, H, A, tf); ph = margin_model.p_home_win(model, H, A)
        tl = round(e["total"] * 2) / 2          # model's expected total as the O/U line
        po = margin_model.p_over(model, H, A, tl, tf)
        sl = round(e["margin"] * 2) / 2          # model's expected home margin as the spread line
        pc = margin_model.p_home_cover(model, H, A, -sl)
        try:
            d = datetime.fromisoformat(u["date"].replace("Z", "+00:00"))
            dstr = f"{HD[d.weekday()]} {d.month}.{d.day:02d}"; ko = u["date"]
        except Exception:
            dstr, ko = "", None
        out.append({"home": u["home"], "away": u["away"], "league": lg["label"], "date": dstr, "kickoff": ko,
                    "insight": f"Expected {e['home_pts']:.0f}-{e['away_pts']:.0f} (margin {e['margin']:+.1f})"
                               + (" · playoff defense" if u.get("post") else "") + ".",
                    "base": [{"name": "Moneyline", "grid": "c2",
                              "outs": [{"k": u["home"], "p": round(ph, 3)}, {"k": u["away"], "p": round(1 - ph, 3)}]}],
                    "extra": [
                        {"name": "Spread", "grid": "c2", "outs": [
                            {"k": f"{u['home']} {_sgn(-sl)}", "p": round(pc, 3)},
                            {"k": f"{u['away']} {_sgn(sl)}", "p": round(1 - pc, 3)}]},
                        {"name": "Total", "grid": "c2", "outs": [
                            {"k": f"Over {tl}", "p": round(po, 3)},
                            {"k": f"Under {tl}", "p": round(1 - po, 3)}]},
                    ]})
    if not out:
        return None
    print(f"  {lg['label']}: {len(out)} upcoming fixtures from ESPN (model only — no odds)")
    sports_state.setdefault(lg["block"], {"label": lg["sport_label"], "matches": []})
    sports_state[lg["block"]]["matches"] += out
    return out


def build_league(lg, sports_state):
    df = load_history(lg["espn"], f'{lg["key"]}_hist.csv', lg["hist_days"])
    if df is None or not len(df):
        print(f"  {lg['label']}: no history — skipped"); return []
    model = margin_model.fit_margin(df)
    if not model:
        print(f"  {lg['label']}: model fit failed — skipped"); return []
    teams = model["teams"]; norm_index = {_norm(t): t for t in teams}
    q = urllib.parse.urlencode({"apiKey": KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"})
    try:
        evs = _get(f"{API}/sports/{lg['key']}/odds?{q}")
    except Exception as e:
        print(f"  {lg['label']}: odds unavailable ({str(e)[:30]}) — ESPN fixtures (model only)")
        return _margin_fixtures_only(lg, model, teams, norm_index, sports_state)
    post = espn_loader.league_is_postseason(lg["espn"])
    tf = POST_TOTAL_FACTOR if post else 1.0
    if post:
        print(f"  {lg['label']}: postseason detected — total dampened x{POST_TOTAL_FACTOR}")
    out, missing = [], set()
    for ev in evs:
        H = map_name(ev.get("home_team"), teams, norm_index)
        A = map_name(ev.get("away_team"), teams, norm_index)
        if not H or not A:
            if not H: missing.add(ev.get("home_team"))
            if not A: missing.add(ev.get("away_team"))
            continue
        hd, ad = ev.get("home_team"), ev.get("away_team")
        e = margin_model.expected(model, H, A, tf)
        ph = margin_model.p_home_win(model, H, A)
        ml_h, ml_a = _h2h(ev)
        base_outs = [{"k": hd, "p": round(ph, 3)}, {"k": ad, "p": round(1 - ph, 3)}]
        if ml_h: base_outs[0]["mkt"] = ml_h
        if ml_a: base_outs[1]["mkt"] = ml_a
        extra = []
        sp = _line_market(ev, "spreads")
        if sp:
            ln, p1, p2 = sp; pc = margin_model.p_home_cover(model, H, A, ln)
            so = [{"k": f"{hd} {_sgn(ln)}", "p": round(pc, 3)}, {"k": f"{ad} {_sgn(-ln)}", "p": round(1 - pc, 3)}]
            if p1: so[0]["mkt"] = p1
            if p2: so[1]["mkt"] = p2
            extra.append({"name": "Spread", "grid": "c2", "outs": so})
        to = _line_market(ev, "totals")
        if to:
            L, pov, pun = to; po = margin_model.p_over(model, H, A, L, tf)
            oo = [{"k": f"Over {L}", "p": round(po, 3)}, {"k": f"Under {L}", "p": round(1 - po, 3)}]
            if pov: oo[0]["mkt"] = pov
            if pun: oo[1]["mkt"] = pun
            extra.append({"name": "Total", "grid": "c2", "outs": oo})
        d = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        out.append({"home": hd, "away": ad, "league": lg["label"],
                    "date": f"{HD[d.weekday()]} {d.month}.{d.day:02d}", "kickoff": ev["commence_time"],
                    "insight": f"Expected {e['home_pts']:.0f}-{e['away_pts']:.0f} (margin {e['margin']:+.1f})"
                               + (" · playoff defense" if post else "") + ".",
                    "base": [{"name": "Moneyline", "grid": "c2", "outs": base_outs}], "extra": extra})
    print(f"  {lg['label']}: {len(out)} matches from {len(df)} historical games" +
          (f" | unmatched: {sorted(missing)}" if missing else ""))
    # register the sport block + label
    sports_state.setdefault(lg["block"], {"label": lg["sport_label"], "matches": []})
    sports_state[lg["block"]]["matches"] += out
    return out


def main():
    if os.environ.get("FAIRLINE_NO_ODDS"):
        print("  Margin sports: skipped (--no-odds, 0 credits; existing data kept)."); return
    if not KEY:
        print("  Margin sports: ODDS_API_KEY not set — skipped."); return
    active = _active({lg["key"] for lg in LEAGUES})
    print(f"  Margin sports: active keys now: {sorted(active)}")
    djs = DATA_JS; data = {}
    if djs.exists():
        s = djs.read_text(encoding="utf-8"); data = json.loads(s[s.find("{"):s.rfind("}") + 1])
    # snapshot existing blocks so a failed odds fetch (e.g. quota 401) preserves the last good data
    old_blocks = {lg["block"]: data.get(lg["block"]) for lg in LEAGUES}
    for lg in LEAGUES:
        data.pop(lg["block"], None)
    for lg in LEAGUES:
        if lg["key"] not in active:
            print(f"  {lg['label']}: not in season — skipped"); continue
        res = build_league(lg, data)
        if res is None:   # odds fetch failed -> keep this league's previous matches instead of dropping them
            ob = old_blocks.get(lg["block"])
            prev = [m for m in ob["matches"] if m.get("league") == lg["label"]] if ob else []
            if prev:
                data.setdefault(lg["block"], {"label": lg["sport_label"], "matches": []})
                data[lg["block"]]["matches"] += prev
                print(f"  {lg['label']}: odds unavailable — kept {len(prev)} previous matches")
        time.sleep(0.3)
    djs.write_text("window.SPORTS_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    print("  Margin sports: data.js updated.")


if __name__ == "__main__":
    main()
