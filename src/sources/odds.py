"""
odds.py — attach REAL market 1X2 odds to matches, for an honest value signal.

Source: The Odds API (free tier, 500 credits/month). The /v4/sports list is FREE
(0 credits) and tells us which leagues are in season right now, so we only spend
credits on active competitions (~1 credit per active league per run).

Flow (called from run_all.py on the combined data dict, before the Supabase push):
  1) GET /v4/sports               -> which of our leagues are active (free)
  2) GET /v4/sports/{key}/odds    -> 1X2 (h2h) decimal odds per upcoming event
  3) match each event to our match by normalised team names (+ small alias map)
  4) attach m["mkt"] = {"1": home, "X": draw, "2": away}  (consensus = median)

The frontend turns m.mkt into a per-outcome value chip (EV = model_p * market - 1).
Unmatched in-season matches are printed so the alias map can be extended.

Env:  ODDS_API_KEY  (the-odds-api.com free key)
"""
import os, re, json, unicodedata, urllib.request, urllib.parse

KEY = os.environ.get("ODDS_API_KEY")
API = "https://api.the-odds-api.com/v4"

# our league label -> The Odds API sport key
SPORT_KEYS = {
    "Brasileirao": "soccer_brazil_campeonato",
    "World Cup 2026": "soccer_fifa_world_cup",
    # European mains (auto-activate in August; harmless while inactive):
    "Premier League": "soccer_epl", "Championship": "soccer_efl_champ",
    "La Liga": "soccer_spain_la_liga", "Serie A": "soccer_italy_serie_a",
    "Bundesliga": "soccer_germany_bundesliga", "Ligue 1": "soccer_france_ligue_one",
    "Eredivisie": "soccer_netherlands_eredivisie", "Primeira Liga": "soccer_portugal_primeira_liga",
    "Champions League": "soccer_uefa_champs_league",
}

# normalised The Odds API name -> OUR (football-data.co.uk / martj42) name
ODDS_ALIAS = {
    "flamengo": "Flamengo RJ", "botafogo": "Botafogo RJ", "atletico mineiro": "Atletico-MG",
    "athletico paranaense": "Athletico-PR", "vasco da gama": "Vasco", "red bull bragantino": "Bragantino",
    "rb bragantino": "Bragantino", "gremio": "Gremio", "sport recife": "Sport Recife",
    # national teams (The Odds API -> our martj42 names)
    "usa": "United States",
}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|afc|sc|ac|cd|ud|rcd|ssc|as|ss|club|calcio|the|de|of|ec|cr|ca|se|fr|and)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _alias_norm(name):
    """Map a The Odds API team name into OUR name-space, then normalise."""
    n = _norm(name)
    return _norm(ODDS_ALIAS[n]) if n in ODDS_ALIAS else n


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _active_keys(wanted):
    """Return the subset of `wanted` sport keys that are currently in season (free call)."""
    try:
        sports = _get(f"{API}/sports?apiKey={KEY}")
    except Exception as e:
        print(f"  Odds: sports list error: {str(e)[:60]}"); return set()
    active = {s["key"] for s in sports if s.get("active")}
    return {k for k in wanted if k in active}


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
    ln, d = max(lines.items(), key=lambda kv: len(kv[1]["H"]))   # most-quoted line
    med = lambda xs: round(sorted(xs)[len(xs) // 2], 2) if xs else None
    return {"line": ln, "home": med(d["H"]), "away": med(d["A"])}


def attach(data):
    if not KEY:
        print("  Odds: ODDS_API_KEY not set — skipped (no market odds / value signal).")
        return
    wanted = {SPORT_KEYS[m["league"]]
              for blk in data.values() for m in blk.get("matches", [])
              if m.get("league") in SPORT_KEYS}
    if not wanted:
        print("  Odds: no matches in odds-covered leagues."); return

    active = _active_keys(wanted)
    print(f"  Odds: active now {sorted(active)}; not in season {sorted(wanted - active)}.")
    if not active:
        print("  Odds: none of the present leagues are in season right now."); return

    index = {}        # (norm_home, norm_away) -> raw event
    api_names = set()  # raw The Odds API team names seen (for the unmatched diagnostic)
    spent = {}
    for sk in active:
        q = urllib.parse.urlencode({"apiKey": KEY, "regions": "eu", "markets": "h2h,totals,spreads", "oddsFormat": "decimal"})
        try:
            evs = _get(f"{API}/sports/{sk}/odds?{q}")
        except Exception as e:
            print(f"  Odds: {sk} fetch error: {str(e)[:60]}"); continue
        spent[sk] = len(evs)
        for ev in evs:
            api_names.add(ev.get("home_team")); api_names.add(ev.get("away_team"))
            index[(_alias_norm(ev.get("home_team")), _alias_norm(ev.get("away_team")))] = ev

    attached, missing = 0, []
    for blk in data.values():
        for m in blk.get("matches", []):
            if m.get("league") not in SPORT_KEYS:
                continue
            ev = index.get((_norm(m["home"]), _norm(m["away"])))
            if not ev:
                if SPORT_KEYS[m["league"]] in active:
                    missing.append(f'{m["home"]} v {m["away"]}')
                continue
            H, D, A = _consensus_1x2(ev)
            mkt = {}
            if H: mkt["1"] = H
            if D: mkt["X"] = D
            if A: mkt["2"] = A
            O, U = _consensus_ou(ev, 2.5)
            if O or U:
                m["mkt_ou"] = {"line": 2.5}
                if O: m["mkt_ou"]["over"] = O
                if U: m["mkt_ou"]["under"] = U
            ah = _consensus_ah(ev)
            if ah and (ah["home"] or ah["away"]):
                m["mkt_ah"] = ah
            if mkt:
                m["mkt"] = mkt
                attached += 1
    print(f"  Odds: attached market 1X2 to {attached} matches (events fetched: {spent}).")
    if missing:
        print(f"  Odds unmatched (add to ODDS_ALIAS): {sorted(set(missing))}")
        print(f"  Odds: The Odds API names seen ({len(api_names)}): {sorted(n for n in api_names if n)}")
