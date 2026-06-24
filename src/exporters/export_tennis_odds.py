"""
export_tennis_odds.py — re-enables TENNIS with a real value signal.

The long-standing blocker was the lack of a free upcoming-draw source. The Odds
API solves it: it lists upcoming ATP/WTA matches (Grand Slams, ATP/WTA 1000/500)
with match-winner (h2h) odds. The surface Elo (tennis_elo.py) is built from free
ESPN match results — the same public scoreboard the other sports use — so:

  * fixtures + market odds -> The Odds API   (tennis_atp_* / tennis_wta_* keys)
  * fair line              -> Elo on ESPN match history

For each upcoming match we predict the win probability, attach the market odds to
the two players, and the frontend shows the value chip (EV = model_p * odds - 1).
Player names are reconciled (normalise + fuzzy + skip/log).

Env:  ODDS_API_KEY
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
from models import tennis_elo
from sources import espn_loader

KEY = os.environ.get("ODDS_API_KEY")
API = "https://api.the-odds-api.com/v4"
HD = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}


def _pnorm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


TENNIS_HIST_DAYS = 540   # ~1.5 seasons of ESPN results -> stable Elo for active players


def load_history(repo, cache):
    """Tennis match history for the Elo model, built from ESPN's public
    scoreboard — the same free source the other sports use. The result is cached
    under data/raw and refreshed by the scheduled job; no third-party dataset is
    bundled. When ESPN returns nothing, the tennis cards are simply skipped.
    """
    p = RAW / cache
    if p.exists():
        return pd.read_csv(p)
    slug = "tennis/wta" if repo.endswith("wta") else "tennis/atp"
    end = date.today(); start = end - timedelta(days=TENNIS_HIST_DAYS)
    df = espn_loader.fetch_tennis_results(start, end, tours=(slug,))
    if df is None or not len(df):
        return None
    df = df.drop(columns=[c for c in ("wta",) if c in df.columns])
    df.to_csv(p, index=False)
    return df


def predict_gen(model, p1, p2):
    r1 = model["gen"].get(p1, 1500.0); r2 = model["gen"].get(p2, 1500.0)
    return 1 / (1 + 10 ** ((r2 - r1) / 400))


CLAY_TOURNAMENTS = ("french_open", "monte_carlo", "madrid", "rome", "barcelona", "hamburg",
                    "estoril", "munich", "gstaad", "bastad", "umag", "kitzbuhel", "bucharest", "geneva")


def is_clay(sport_key):
    return any(t in sport_key for t in CLAY_TOURNAMENTS)


def map_player(name, players, norm_index):
    if name in players:
        return name
    n = _pnorm(name)
    if n in norm_index:
        return norm_index[n]
    cand = difflib.get_close_matches(n, list(norm_index.keys()), n=1, cutoff=0.9)
    return norm_index[cand[0]] if cand else None


def _consensus(ev):
    """Median h2h price per player -> {player_name: odd}."""
    prices = {}
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h":
                continue
            for o in mk.get("outcomes", []):
                prices.setdefault(o.get("name"), []).append(o.get("price"))
    return {k: round(sorted(v)[len(v) // 2], 2) for k, v in prices.items() if v}


def build():
    if not KEY:
        print("  Tennis: ODDS_API_KEY not set — skipped."); return [], True
    try:
        sports = _get(f"{API}/sports?apiKey={KEY}")
    except Exception as e:
        print(f"  Tennis: sports list error: {str(e)[:60]}"); return [], True
    active = sorted(s["key"] for s in sports if s.get("active") and s["key"].startswith("tennis_"))
    print(f"  Tennis: active keys now: {active}")
    if not active:
        print("  Tennis: no ATP/WTA events live right now."); return [], False

    models = {}
    def model_for(tour):
        if tour in models:
            return models[tour]
        repo, cache = ("tennis_atp", "atp_hist.csv") if tour == "atp" else ("tennis_wta", "wta_hist.csv")
        df = load_history(repo, cache)
        models[tour] = tennis_elo.build_elo(df) if df is not None and len(df) else None
        return models[tour]

    out, missing, any_fail = [], set(), False
    for sk in active:
        tour = "atp" if sk.startswith("tennis_atp") else "wta"
        model = model_for(tour)
        if not model:
            print(f"  Tennis: no history for {tour} — {sk} skipped"); continue
        players = set(model["gen"]); norm_index = {_pnorm(p): p for p in players}
        q = urllib.parse.urlencode({"apiKey": KEY, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"})
        try:
            evs = _get(f"{API}/sports/{sk}/odds?{q}")
        except Exception as e:
            print(f"  Tennis: {sk} odds error: {str(e)[:50]}"); any_fail = True; continue
        title = next((s.get("title") for s in sports if s["key"] == sk), sk)
        clay = is_clay(sk)
        n = 0
        for ev in evs:
            a_disp, b_disp = ev.get("home_team"), ev.get("away_team")
            pa = map_player(a_disp, players, norm_index)
            pb = map_player(b_disp, players, norm_index)
            if not pa or not pb:
                if not pa: missing.add(a_disp)
                if not pb: missing.add(b_disp)
                continue
            pr = tennis_elo.predict_clay(model, pa, pb) if clay else predict_gen(model, pa, pb)
            od = _consensus(ev)
            outs = [{"k": a_disp, "p": round(pr, 3)}, {"k": b_disp, "p": round(1 - pr, 3)}]
            if od.get(a_disp): outs[0]["mkt"] = od[a_disp]
            if od.get(b_disp): outs[1]["mkt"] = od[b_disp]
            d = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            out.append({"home": a_disp, "away": b_disp, "league": title,
                        "date": f"{HD[d.weekday()]} {d.month}.{d.day:02d}", "kickoff": ev["commence_time"],
                        "insight": f"{'Clay ' if clay else ''}Elo: {pr * 100:.0f}% / {(1 - pr) * 100:.0f}%.",
                        "base": [{"name": "Match winner", "grid": "c2", "outs": outs}], "extra": []})
            n += 1
        print(f"  Tennis {title}: {n} matches")
        time.sleep(0.3)

    # No odds at all (quota out): fall back to ESPN upcoming singles (model only)
    if any_fail and not out:
        from sources import espn_loader
        ups = espn_loader.fetch_tennis_upcoming(days_ahead=9)
        emiss = set()
        for u in ups:
            if u["home"] == "TBD" or u["away"] == "TBD":
                continue  # opponent not decided yet (future round) — can't predict
            tour = "wta" if u.get("wta") else "atp"
            model = model_for(tour)
            if not model:
                continue
            players = set(model["gen"]); norm_index = {_pnorm(p): p for p in players}
            pa = map_player(u["home"], players, norm_index); pb = map_player(u["away"], players, norm_index)
            if not pa or not pb:
                if not pa: emiss.add(u["home"])
                if not pb: emiss.add(u["away"])
                continue
            tname = (u.get("tour") or "").lower()
            clay = ("roland garros" in tname) or ("french" in tname)
            pr = tennis_elo.predict_clay(model, pa, pb) if clay else predict_gen(model, pa, pb)
            try:
                d = datetime.fromisoformat(u["date"].replace("Z", "+00:00"))
                dstr = f"{HD[d.weekday()]} {d.month}.{d.day:02d}"; ko = u["date"]
            except Exception:
                dstr, ko = "", None
            out.append({"home": u["home"], "away": u["away"], "league": u.get("tour") or "Tennis",
                        "date": dstr, "kickoff": ko,
                        "insight": f"{'Clay ' if clay else ''}Elo: {pr * 100:.0f}% / {(1 - pr) * 100:.0f}%.",
                        "base": [{"name": "Match winner", "grid": "c2",
                                  "outs": [{"k": u["home"], "p": round(pr, 3)}, {"k": u["away"], "p": round(1 - pr, 3)}]}],
                        "extra": []})
        if emiss:
            print(f"  Tennis ESPN unmatched players: {sorted(emiss)}")
        print(f"  Tennis: {len(out)} upcoming matches from ESPN (model only — no odds)")
        if out:
            any_fail = False

    if missing:
        print(f"  Tennis unmatched players: {sorted(missing)}")
    return out, any_fail


def main():
    if os.environ.get("FAIRLINE_NO_ODDS"):
        print("  Tennis: skipped (--no-odds, 0 credits; existing data kept)."); return
    matches, failed = build()
    djs = DATA_JS; data = {}
    if djs.exists():
        s = djs.read_text(encoding="utf-8"); data = json.loads(s[s.find("{"):s.rfind("}") + 1])
    prev = (data.get("tenisz") or {}).get("matches") or []
    if not matches and failed and prev:
        print(f"Tennis: odds unavailable — kept {len(prev)} previous matches")
    else:
        data["tenisz"] = {"label": "Tennis", "matches": matches}
    djs.write_text("window.SPORTS_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    print(f"Tennis: wrote {len(matches)} matches." if matches or not failed else "Tennis: kept previous (odds unavailable).")


if __name__ == "__main__":
    main()
