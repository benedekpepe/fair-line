"""
export_odds_leagues.py — leagues whose FIXTURES are not in the football-data.org
free tier (mostly summer leagues that keep playing during the World Cup). For these:

  * fixtures + market odds  -> The Odds API   (one call gives both)
  * training history        -> football-data.co.uk extra files (new/{CODE}.csv)

For each active league we fit Dixon-Coles on the multi-season .co.uk history, predict
each upcoming Odds-API fixture, and attach the market 1X2 (so the value chip lights up).
Team names from the two sources are reconciled (normalise + alias + skip/log).

Only leagues currently IN SEASON (per the free /sports list) are queried, so credits
are spent only where there is something to show (~1 credit per active league).

Env:  ODDS_API_KEY
"""
import os, sys, io, json, time, re, unicodedata, urllib.request, urllib.parse
from datetime import datetime
import pandas as pd

from config import DATA_JS, SRC

sys.path.insert(0, str(SRC))
try:
    from config import load_env; load_env()
except Exception:
    pass
from models.dixon_coles import fit_dixon_coles, predict_match
from sources import espn_loader

KEY = os.environ.get("ODDS_API_KEY")
API = "https://api.the-odds-api.com/v4"
N_SEASONS = 5
XI = 0.0019
HD = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

# our display label -> (The Odds API sport key, football-data.co.uk extra code)
LEAGUES = {
    # label: (Odds API key, football-data.co.uk code, ESPN soccer slug for free fixtures)
    "MLS": ("soccer_usa_mls", "USA", "soccer/usa.1"),
    "Argentina": ("soccer_argentina_primera_division", "ARG", "soccer/arg.1"),
    "Norway": ("soccer_norway_eliteserien", "NOR", "soccer/nor.1"),
    "Sweden": ("soccer_sweden_allsvenskan", "SWE", "soccer/swe.1"),
    "Finland": ("soccer_finland_veikkausliiga", "FIN", "soccer/fin.1"),
    "Japan": ("soccer_japan_j_league", "JPN", "soccer/jpn.1"),
    "China": ("soccer_china_superleague", "CHN", "soccer/chn.1"),
    "Mexico": ("soccer_mexico_ligamx", "MEX", "soccer/mex.1"),
    "Korea": ("soccer_korea_kleague1", "KOR", "soccer/kor.1"),
}

# The Odds API normalised name -> our football-data.co.uk name (fill in from the logs)
ALIASES = {
    # Japan
    "machida zelvia": "Machida", "fagiano okayama": "Okayama",
    "hiroshima sanfrecce": "Sanfrecce Hiroshima", "kyoto purple sanga": "Kyoto",
    "kyoto sanga": "Kyoto", "tokyo verdy 1969": "Verdy",
    "tokyo verdy": "Verdy", "urawa red diamonds": "Urawa Reds",
    # China (incl. known renames: SIPG->Port, Luneng->Taishan)
    "beijing": "Beijing Guoan", "henan": "Henan Songshan Longmen",
    "shandong luneng taishan": "Shandong Taishan", "shanghai sipg": "Shanghai Port",
    "shenzhen peng city": "Shenzhen Xinpengcheng", "zhejiang": "Zhejiang Professional",
    # Finland (.co.uk uses short names)
    "hjk helsinki": "HJK", "ilves tampere": "Ilves", "kups kuopio": "KuPS",
    "sjk seinajoki": "SJK", "tps turku": "TPS", "vps vaasa": "VPS",
}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|afc|sc|ac|cd|ud|rcd|ssc|as|ss|club|calcio|the|de|of|ec|cr|ca|se|fr|and|fk|bk|if|ifk|sk)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _alias_norm(name):
    n = _norm(name)
    return _norm(ALIASES[n]) if n in ALIASES else n


def _normcols(df):
    ren = {}
    for c in df.columns:
        cl = str(c).strip()
        if cl in ("HomeTeam", "Home"): ren[c] = "home_team"
        elif cl in ("AwayTeam", "Away"): ren[c] = "away_team"
        elif cl in ("FTHG", "HG"): ren[c] = "home_goals"
        elif cl in ("FTAG", "AG"): ren[c] = "away_goals"
        elif cl == "Date": ren[c] = "Date"
    return df.rename(columns=ren)


def hist_extra(code):
    url = f"https://www.football-data.co.uk/new/{code}.csv"
    try:
        raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "ignore")
        df = _normcols(pd.read_csv(io.StringIO(raw)))
    except Exception:
        return None
    need = {"Date", "home_team", "away_team", "home_goals", "away_goals"}
    if not need.issubset(df.columns):
        return None
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date", "home_team", "away_team", "home_goals", "away_goals"])
    df = df[pd.to_numeric(df["home_goals"], errors="coerce").notna()]
    df = df[pd.to_numeric(df["away_goals"], errors="coerce").notna()]
    if len(df):
        df = df[df["Date"] >= df["Date"].max() - pd.Timedelta(days=365 * N_SEASONS)]
    return df[["Date", "home_team", "away_team", "home_goals", "away_goals"]] if len(df) else None


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _consensus_1x2(ev):
    home, away = ev.get("home_team"), ev.get("away_team")
    H, D, A = [], [], []
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h":
                continue
            for o in mk.get("outcomes", []):
                nm, pr = o.get("name"), o.get("price")
                if nm == home: H.append(pr)
                elif nm == away: A.append(pr)
                else: D.append(pr)
    med = lambda xs: round(sorted(xs)[len(xs) // 2], 2) if xs else None
    return med(H), med(D), med(A)


def _consensus_ou(ev, line=2.5):
    O, U = [], []
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "totals":
                continue
            for o in mk.get("outcomes", []):
                if o.get("point") != line:
                    continue
                if o.get("name") == "Over": O.append(o.get("price"))
                elif o.get("name") == "Under": U.append(o.get("price"))
    med = lambda xs: round(sorted(xs)[len(xs) // 2], 2) if xs else None
    return med(O), med(U)


def _consensus_ah(ev):
    from collections import defaultdict
    home, away = ev.get("home_team"), ev.get("away_team")
    lines = defaultdict(lambda: {"H": [], "A": []})
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "spreads":
                continue
            outs = {o.get("name"): o for o in mk.get("outcomes", [])}
            ho, ao = outs.get(home), outs.get(away)
            if ho and ao and ho.get("point") is not None:
                ln = ho["point"]
                lines[ln]["H"].append(ho.get("price")); lines[ln]["A"].append(ao.get("price"))
    if not lines:
        return None
    ln, d = max(lines.items(), key=lambda kv: len(kv[1]["H"]))
    med = lambda xs: round(sorted(xs)[len(xs) // 2], 2) if xs else None
    return {"line": ln, "home": med(d["H"]), "away": med(d["A"])}


def map_name(name, model_teams, norm_index):
    if name in model_teams:
        return name
    n = _norm(name)
    v = ALIASES.get(n)
    if v and v in model_teams:
        return v
    if n in norm_index:
        return norm_index[n]
    import difflib
    cand = difflib.get_close_matches(n, list(norm_index.keys()), n=1, cutoff=0.86)
    return norm_index[cand[0]] if cand else None


def build():
    """Return {label: [match dicts]} for the active odds-only leagues."""
    if not KEY:
        print("  Odds leagues: ODDS_API_KEY not set — skipped."); return {}
    try:
        sports = _get(f"{API}/sports?apiKey={KEY}")
    except Exception as e:
        print(f"  Odds leagues: sports list error: {str(e)[:60]}"); return {}
    active = {s["key"] for s in sports if s.get("active")}
    soccer_active = sorted(k for k in active if k.startswith("soccer_"))
    print(f"  Odds leagues: active soccer keys now: {soccer_active}")

    out = {}
    for label, (sk, code, espn) in LEAGUES.items():
        if sk not in active:
            continue
        hist = hist_extra(code)
        if hist is None or len(hist) < 50:
            print(f"  {label}: no/too little football-data.co.uk history ({code}) — skipped"); continue
        model = fit_dixon_coles(hist, xi=XI)
        teams = set(model["idx"]); norm_index = {_norm(t): t for t in teams}

        evs = None
        q = urllib.parse.urlencode({"apiKey": KEY, "regions": "eu", "markets": "h2h,totals,spreads", "oddsFormat": "decimal"})
        try:
            evs = _get(f"{API}/sports/{sk}/odds?{q}")
        except Exception as e:
            print(f"  {label}: odds unavailable ({str(e)[:30]}) — ESPN fixtures (model only)")
            evs = None

        if evs is None:
            out[label] = _rows_from_espn(espn, model, teams, norm_index, label)
            time.sleep(0.3)
            continue

        rows, missing = [], set()
        for ev in evs:
            h_disp, a_disp = ev.get("home_team"), ev.get("away_team")
            hm = map_name(h_disp, teams, norm_index)
            am = map_name(a_disp, teams, norm_index)
            if not hm or not am:
                if not hm: missing.add(h_disp)
                if not am: missing.add(a_disp)
                continue
            p = predict_match(model, hm, am)
            H, D, A = _consensus_1x2(ev)
            mkt = {}
            if H: mkt["1"] = H
            if D: mkt["X"] = D
            if A: mkt["2"] = A
            O, U = _consensus_ou(ev, 2.5)
            mkt_ou = {"line": 2.5}
            if O: mkt_ou["over"] = O
            if U: mkt_ou["under"] = U
            ah = _consensus_ah(ev)
            d = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            rows.append({"home": h_disp, "away": a_disp, "league": label,
                         "date": f"{HD[d.weekday()]} {d.month}.{d.day:02d}", "kickoff": ev["commence_time"],
                         "lam": round(p["exp_home_goals"], 2), "mu": round(p["exp_away_goals"], 2),
                         "mkt": mkt or None, "mkt_ou": (mkt_ou if (O or U) else None),
                         "mkt_ah": (ah if (ah and (ah["home"] or ah["away"])) else None),
                         "insight": f"Expected goals: {p['exp_home_goals']:.2f}-{p['exp_away_goals']:.2f}."})
        out[label] = rows
        note = f" | {len(missing)} skipped (no top-flight history): {sorted(missing)}" if missing else ""
        print(f"  {label}: {len(rows)} matches from {len(hist)} historical games{note}")
        time.sleep(0.3)
    return out


def _rows_from_espn(espn_slug, model, teams, norm_index, label):
    """No odds (quota out): upcoming fixtures from ESPN soccer scoreboard, model 1X2 only."""
    if not espn_slug:
        return []
    ups = espn_loader.fetch_upcoming(espn_slug, days_ahead=12)
    rows, missing = [], set()
    for u in ups:
        hm = map_name(u["home"], teams, norm_index); am = map_name(u["away"], teams, norm_index)
        if not hm or not am:
            if not hm: missing.add(u["home"])
            if not am: missing.add(u["away"])
            continue
        p = predict_match(model, hm, am)
        try:
            d = datetime.fromisoformat(u["date"].replace("Z", "+00:00"))
            dstr = f"{HD[d.weekday()]} {d.month}.{d.day:02d}"; ko = u["date"]
        except Exception:
            dstr, ko = "", None
        rows.append({"home": u["home"], "away": u["away"], "league": label, "date": dstr, "kickoff": ko,
                     "lam": round(p["exp_home_goals"], 2), "mu": round(p["exp_away_goals"], 2),
                     "mkt": None, "mkt_ou": None, "mkt_ah": None,
                     "insight": f"Expected goals: {p['exp_home_goals']:.2f}-{p['exp_away_goals']:.2f}."})
    note = f" | ESPN unmatched: {sorted(missing)}" if missing else ""
    print(f"  {label}: {len(rows)} upcoming fixtures from ESPN (model only — no odds){note}")
    return rows


def main():
    if os.environ.get("FAIRLINE_NO_ODDS"):
        print("Odds leagues: skipped (--no-odds, 0 credits; existing data kept)."); return
    new = build()
    if not new:
        print("Odds leagues: nothing to add right now."); return
    djs = DATA_JS; data = {}
    if djs.exists():
        s = djs.read_text(encoding="utf-8"); data = json.loads(s[s.find("{"):s.rfind("}") + 1])
    data.setdefault("foci", {"label": "Football", "matches": []})
    labels = set(new)
    keep = [m for m in data["foci"]["matches"] if m.get("league") not in labels]
    for lg, ms in new.items():
        keep += ms
    data["foci"]["matches"] = keep
    djs.write_text("window.SPORTS_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    print(f"Odds leagues: added {sum(len(v) for v in new.values())} matches across {len(new)} leagues.")


if __name__ == "__main__":
    main()
