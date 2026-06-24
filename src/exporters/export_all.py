"""
export_all.py — all sports into one data.js, from real models.
Structure:  sport (top level)  ->  competition (league)  ->  matches
  Football: World Cup 2026 (fixtures from the API when available, with the
            national-team strengths estimated from the martj42 history)

NOTE: the user-facing strings (league names, insight texts) are kept in
Hungarian on purpose, because the interface is localised for Hungarian users.
"""
import json, urllib.request, os
from pathlib import Path
import numpy as np, pandas as pd
from scipy.optimize import minimize

from config import RAW, DATA_JS

RAW.mkdir(parents=True, exist_ok=True)
try:
    from config import load_env; load_env()
except Exception:
    pass
FD_KEY = os.environ.get("FOOTBALL_DATA_KEY")  # same key as the club leagues

def dl(url, path):
    if not Path(path).exists(): urllib.request.urlretrieve(url, path)

# ============ 1) FOOTBALL – World Cup ============
# The model "brain": national-team strengths from years of international results.
dl("https://raw.githubusercontent.com/martj42/international_results/master/results.csv", RAW / "results.csv")
df = pd.read_csv(RAW / "results.csv"); df["date"] = pd.to_datetime(df["date"], errors="coerce")
tr = df[(df["date"] >= "2018-01-01") & df["home_score"].notna() & df["away_score"].notna()].copy()
teams = sorted(set(tr["home_team"]) | set(tr["away_team"])); idx = {t: i for i, t in enumerate(teams)}; n = len(teams)
hi = tr["home_team"].map(idx).to_numpy(); ai = tr["away_team"].map(idx).to_numpy()
hg = tr["home_score"].to_numpy(); ag = tr["away_score"].to_numpy(); neu = tr["neutral"].astype(int).to_numpy()
def nll(p):
    atk = p[:n]; dfc = p[n:2 * n]; home = p[2 * n]
    lam = np.exp(atk[hi] + dfc[ai] + home * (1 - neu)); mu = np.exp(atk[ai] + dfc[hi])
    return np.sum(lam - hg * np.log(lam)) + np.sum(mu - ag * np.log(mu))
r = minimize(nll, np.concatenate([np.zeros(2 * n), [0.3]]), method="L-BFGS-B", options={"maxiter": 500})
atk = r.x[:n]; dfc = r.x[n:2 * n]; home = r.x[2 * n]
def fpred(h, a, neutral=True):
    i, j = idx[h], idx[a]
    return float(np.exp(atk[i] + dfc[j] + home * (0 if neutral else 1))), float(np.exp(atk[j] + dfc[i]))

HD = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
# national-team name aliases: football-data.org -> martj42 (extend from the
# first run's printed misses if needed)
NAT_ALIAS = {"Korea Republic": "South Korea", "Korea DPR": "North Korea", "IR Iran": "Iran",
             "USA": "United States", "Côte d'Ivoire": "Ivory Coast", "Cabo Verde": "Cape Verde",
             "China PR": "China", "Czechia": "Czech Republic", "Türkiye": "Turkey",
             "Bosnia-Herzegovina": "Bosnia and Herzegovina", "Cape Verde Islands": "Cape Verde",
             "Congo DR": "DR Congo"}
nat = lambda s: NAT_ALIAS.get(s, s)

def wc_from_api():
    """World Cup fixtures from football-data.org (fresh); None if no key / unavailable."""
    if not FD_KEY: return None
    try:
        url = "https://api.football-data.org/v4/competitions/WC/matches"
        req = urllib.request.Request(url, headers={"X-Auth-Token": FD_KEY})
        ms = json.loads(urllib.request.urlopen(req, timeout=30).read())["matches"]
        ms = [m for m in ms if m.get("status") in ("SCHEDULED", "TIMED")]  # all not-yet-started
        return ms or None
    except Exception as e:
        print(f"  WC API unavailable ({str(e)[:40]}) — martj42 fallback"); return None

foci = []; api = wc_from_api()
if api:
    skipped = set()
    for m in sorted(api, key=lambda x: x["utcDate"])[:24]:
        h, a = nat(m["homeTeam"]["name"]), nat(m["awayTeam"]["name"])
        if h not in idx or a not in idx:
            if h not in idx: skipped.add(m["homeTeam"]["name"])
            if a not in idx: skipped.add(m["awayTeam"]["name"])
            continue
        lam, mu = fpred(h, a, True); d = pd.to_datetime(m["utcDate"])
        foci.append({"home": h, "away": a, "league": "World Cup 2026", "date": f"{HD[d.weekday()]} {d.month}.{d.day:02d}", "kickoff": m["utcDate"],
                     "lam": round(lam, 2), "mu": round(mu, 2), "insight": f"Group stage. Expected goals: {lam:.2f}–{mu:.2f}."})
    if skipped: print(f"  Missing aliases (add to NAT_ALIAS): {sorted(skipped)}")
    print(f"Football/WC (API, fresh): {len(foci)} matches")
else:
    wc = df[(df["tournament"] == "FIFA World Cup") & df["home_score"].isna() & (df["date"] >= "2026-06-11")].sort_values("date")
    for _, x in wc.iterrows():
        h, a = x["home_team"], x["away_team"]
        if h in idx and a in idx:
            lam, mu = fpred(h, a, bool(x["neutral"])); d = x["date"]
            foci.append({"home": h, "away": a, "league": "World Cup 2026", "date": f"{HD[d.weekday()]} {d.month}.{d.day:02d}",
                         "lam": round(lam, 2), "mu": round(mu, 2), "insight": f"Group stage. Expected goals: {lam:.2f}–{mu:.2f}."})
    print(f"Football/WC (martj42 fallback): {len(foci)} matches")

# Tennis is produced separately by export_tennis_odds.py (ESPN history + The Odds
# API draws). export_all only writes the football block here and leaves every
# other sport's block in data.js untouched.

# ============ output: sport -> competition -> match ============
# Preserve any other sport blocks already in data.js (tennis/margin/combat/etc. from later exports);
# only (re)write the foci (football) block here.
out = DATA_JS; out.parent.mkdir(parents=True, exist_ok=True)
data = {}
if out.exists():
    try:
        prev = out.read_text(encoding="utf-8"); data = json.loads(prev[prev.find("{"):prev.rfind("}") + 1])
    except Exception:
        data = {}
data["foci"] = {"label": "Football",
                "matches": [m for m in data.get("foci", {}).get("matches", [])
                            if m.get("league") not in {x["league"] for x in foci}] + foci}
out.write_text("window.SPORTS_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
print(f"data.js ready: Football(WC) {len(foci)} matches")

# push to Supabase only when explicitly requested; the combined runner
# (run_all.py) pushes the FULL set (club + WC + tennis) once at the end.
if os.environ.get("EXPORT_PUSH") == "1":
    try:
        import sync_supabase
        sync_supabase.push(data)
    except Exception as e:
        print(f"  Supabase push skipped/failed: {e}")
else:
    print("  (Supabase push skipped here — run run_all.py to push the full combined set.)")
