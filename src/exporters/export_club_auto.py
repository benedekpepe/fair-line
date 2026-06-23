"""
export_club_auto.py — club football with MULTI-SEASON history.

Two free sources, combined:
  * football-data.org   -> UPCOMING fixtures (next round) + matchday + dates
  * football-data.co.uk -> MULTI-SEASON historical results to TRAIN the model

The Dixon-Coles model is fit on several seasons of football-data.co.uk results
(recent matches weighted more via time-decay), then used to predict the upcoming
football-data.org fixtures. The two sources name teams differently, so fixture
names are mapped to the model's names via an alias table + normalisation; any
team that can't be matched is SKIPPED and printed (extend ALIASES from the log).

Competitions without a football-data.co.uk league file (Champions League, Euros)
fall back to the previous behaviour: current-season results from football-data.org.

Env:  FOOTBALL_DATA_KEY  (football-data.org free key, for fixtures)
"""
import os, sys, io, json, time, re, unicodedata, urllib.request
from datetime import datetime, date
import pandas as pd

from config import DATA_JS, SRC

sys.path.insert(0, str(SRC))
try:
    from config import load_env; load_env()
except Exception:
    pass
from models.dixon_coles import fit_dixon_coles, predict_match

API_KEY = os.environ.get("FOOTBALL_DATA_KEY") or "PUT_YOUR_FREE_KEY_HERE"
N_SEASONS = 5          # how many past seasons of football-data.co.uk history to use
XI = 0.0019            # time-decay (~half-year half-life): recent matches weigh more
HD = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

# org_code: (display label, source_kind, football-data.co.uk code)
#   "main"  -> mmz4281/{season}/{CODE}.csv      (per-season files)
#   "extra" -> new/{CODE}.csv                   (one file, many seasons)
#   "org"   -> no fd.co.uk file; current-season football-data.org fallback
COMPS = {
    "BSA": ("Brasileirao", "extra", "BRA"),
    "PL":  ("Premier League", "main", "E0"),
    "ELC": ("Championship", "main", "E1"),
    "PD":  ("La Liga", "main", "SP1"),
    "SA":  ("Serie A", "main", "I1"),
    "BL1": ("Bundesliga", "main", "D1"),
    "FL1": ("Ligue 1", "main", "F1"),
    "DED": ("Eredivisie", "main", "N1"),
    "PPL": ("Primeira Liga", "main", "P1"),
    "CL":  ("Champions League", "org", None),
    "EC":  ("European Championship", "org", None),
}

# football-data.org name -> football-data.co.uk name (extend from the printed log)
ALIASES = {
    "Manchester City": "Man City", "Manchester United": "Man United",
    "Tottenham Hotspur": "Tottenham", "Wolverhampton Wanderers": "Wolves",
    "Brighton & Hove Albion": "Brighton", "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest", "West Ham United": "West Ham",
    "Leeds United": "Leeds", "Leicester City": "Leicester", "Norwich City": "Norwich",
    "Athletic Club": "Ath Bilbao", "Club Atletico de Madrid": "Ath Madrid",
    "Atletico Madrid": "Ath Madrid", "Real Betis Balompie": "Betis",
    "Real Sociedad": "Sociedad", "Rayo Vallecano": "Vallecano", "Celta de Vigo": "Celta",
    "Deportivo Alaves": "Alaves", "FC Barcelona": "Barcelona", "Real Madrid CF": "Real Madrid",
    "Sevilla FC": "Sevilla", "Borussia Dortmund": "Dortmund",
    "Borussia Monchengladbach": "M'gladbach", "Bayern Munchen": "Bayern Munich",
    "Bayer 04 Leverkusen": "Leverkusen", "Eintracht Frankfurt": "Ein Frankfurt",
    "VfB Stuttgart": "Stuttgart", "Paris Saint-Germain": "Paris SG",
    "Olympique de Marseille": "Marseille", "Olympique Lyonnais": "Lyon",
    "AS Monaco FC": "Monaco", "OGC Nice": "Nice", "AC Milan": "Milan",
    "FC Internazionale Milano": "Inter", "SSC Napoli": "Napoli", "AS Roma": "Roma",
    "Juventus FC": "Juventus", "ACF Fiorentina": "Fiorentina",
    # Brazil (football-data.org -> football-data.co.uk; keys matched accent-insensitively)
    "CR Flamengo": "Flamengo RJ", "SE Palmeiras": "Palmeiras", "Sao Paulo FC": "Sao Paulo",
    "Botafogo FR": "Botafogo RJ", "Cruzeiro EC": "Cruzeiro", "RB Bragantino": "Bragantino",
    "Fluminense FC": "Fluminense", "EC Bahia": "Bahia", "SC Internacional": "Internacional",
    "SC Corinthians Paulista": "Corinthians", "CR Vasco da Gama": "Vasco",
    "CA Mineiro": "Atletico-MG", "CA Paranaense": "Athletico-PR", "Mirassol FC": "Mirassol",
    "Gremio FBPA": "Gremio", "Ceara SC": "Ceara", "Fortaleza EC": "Fortaleza",
    "EC Vitoria": "Vitoria", "Santos FC": "Santos", "EC Juventude": "Juventude",
    "Clube do Remo": "Remo", "Coritiba FBC": "Coritiba", "Sport Club do Recife": "Sport Recife",
    # England Championship
    "Hull City AFC": "Hull",
}


def season_codes(n=N_SEASONS):
    """Most recent n season codes like '2526', '2425', ... (auto-advances by date)."""
    y, m = date.today().year, date.today().month
    start = y if m >= 7 else y - 1
    return [f"{(start - k) % 100:02d}{(start - k + 1) % 100:02d}" for k in range(n)]


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


def _clean(df):
    need = {"Date", "home_team", "away_team", "home_goals", "away_goals"}
    if not need.issubset(df.columns):
        return None
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date", "home_team", "away_team", "home_goals", "away_goals"])
    df = df[pd.to_numeric(df["home_goals"], errors="coerce").notna()]
    df = df[pd.to_numeric(df["away_goals"], errors="coerce").notna()]
    return df[["Date", "home_team", "away_team", "home_goals", "away_goals"]]


def hist_main(code):
    frames = []
    for s in season_codes():
        url = f"https://www.football-data.co.uk/mmz4281/{s}/{code}.csv"
        try:
            raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "ignore")
            frames.append(_normcols(pd.read_csv(io.StringIO(raw))))
        except Exception:
            pass
        time.sleep(0.5)
    if not frames:
        return None
    return _clean(pd.concat(frames, ignore_index=True))


def hist_extra(code):
    url = f"https://www.football-data.co.uk/new/{code}.csv"
    try:
        raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "ignore")
        df = _normcols(pd.read_csv(io.StringIO(raw)))
    except Exception:
        return None
    df = _clean(df)
    if df is not None and len(df):
        cutoff = df["Date"].max() - pd.Timedelta(days=365 * N_SEASONS)
        df = df[df["Date"] >= cutoff]
    return df


def fd_org(comp, status):
    base = f"https://api.football-data.org/v4/competitions/{comp}/matches"
    url = base if status == "UPCOMING" else f"{base}?status={status}"
    req = urllib.request.Request(url, headers={"X-Auth-Token": API_KEY})
    ms = json.loads(urllib.request.urlopen(req, timeout=30).read())["matches"]
    if status == "UPCOMING":
        ms = [m for m in ms if m.get("status") in ("SCHEDULED", "TIMED")]
    return ms


def org_history(comp):
    """Fallback: current-season finished results from football-data.org."""
    try:
        fin = fd_org(comp, "FINISHED"); time.sleep(6)
    except Exception:
        return None
    rows = [{"Date": m["utcDate"], "home_team": m["homeTeam"]["name"], "away_team": m["awayTeam"]["name"],
             "home_goals": m["score"]["fullTime"]["home"], "away_goals": m["score"]["fullTime"]["away"]} for m in fin]
    return _clean(_normcols(pd.DataFrame(rows))) if rows else None


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|afc|sc|ac|cd|ud|rcd|ssc|as|ss|club|calcio|the|de|of)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


NORM_ALIASES = {_norm(k): v for k, v in ALIASES.items()}


def map_name(org_name, model_teams, norm_index):
    if org_name in model_teams:
        return org_name
    n = _norm(org_name)
    v = NORM_ALIASES.get(n)
    if v and v in model_teams:
        return v
    if n in norm_index:
        return norm_index[n]
    import difflib
    cand = difflib.get_close_matches(n, list(norm_index.keys()), n=1, cutoff=0.84)
    return norm_index[cand[0]] if cand else None


if API_KEY.startswith("PUT_YOUR"):
    print("First set the free API key (FOOTBALL_DATA_KEY env var).")
    sys.exit(0)

all_new = {}
for comp, (label, kind, code) in COMPS.items():
    # 1) upcoming fixtures (football-data.org)
    try:
        sched = fd_org(comp, "UPCOMING"); time.sleep(6)
    except Exception as e:
        print(f"  {label}: fixtures error ({str(e)[:45]}) -- skipped"); continue
    if not sched:
        print(f"  {label}: out of season -- skipped"); continue

    # 2) training history
    if kind == "main":
        hist = hist_main(code)
    elif kind == "extra":
        hist = hist_extra(code)
    else:
        hist = org_history(comp)
    if hist is None or len(hist) < 50:
        print(f"  {label}: too little history -- skipped"); continue

    model = fit_dixon_coles(hist, xi=XI)
    teams = set(model["idx"]); norm_index = {_norm(t): t for t in teams}

    mds = [m.get("matchday") for m in sched if m.get("matchday")]
    next_md = min(mds) if mds else None
    sel = [m for m in sched if next_md is None or m.get("matchday") == next_md]

    out, missing = [], set()
    for m in sel:
        h, a = m["homeTeam"]["name"], m["awayTeam"]["name"]
        hm, am = map_name(h, teams, norm_index), map_name(a, teams, norm_index)
        if not hm or not am:
            if not hm: missing.add(h)
            if not am: missing.add(a)
            continue
        p = predict_match(model, hm, am)
        d = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        out.append({"home": h, "away": a, "league": label, "date": f"{HD[d.weekday()]} {d.month}.{d.day:02d}",
                    "kickoff": m["utcDate"],
                    "lam": round(p["exp_home_goals"], 2), "mu": round(p["exp_away_goals"], 2), "md": next_md,
                    "insight": f"Round {next_md}. Expected goals: {p['exp_home_goals']:.2f}-{p['exp_away_goals']:.2f}."})
    all_new[label] = out
    note = f" (unmapped: {sorted(missing)})" if missing else ""
    print(f"  {label}: {len(out)} matches (matchday {next_md}) from {len(hist)} historical games{note}")

# merge into data.js (replace only the affected leagues inside football)
djs = DATA_JS; data = {}
if djs.exists():
    s = djs.read_text(encoding="utf-8"); data = json.loads(s[s.find("{"):s.rfind("}") + 1])
data.setdefault("foci", {"label": "Football", "matches": []})
keep = [m for m in data["foci"]["matches"] if m.get("league") not in all_new]
for lg, ms in all_new.items():
    keep += ms
data["foci"]["matches"] = keep
djs.parent.mkdir(parents=True, exist_ok=True)
djs.write_text("window.SPORTS_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
print(f"\nDone: {sum(len(v) for v in all_new.values())} matches updated across {len(all_new)} competitions.")
