"""
espn_loader.py — free historical results from ESPN's public scoreboard API.

ONE loader for many sports, because ESPN uses the same JSON shape across leagues:
    basketball/wnba, basketball/nba, hockey/nhl, football/nfl, ...
Returns a DataFrame[date, home, away, home_score, away_score] of COMPLETED games.

The container can't reach ESPN (network allow-list), so this is verified live on
your machine — it prints counts + a sample so we can adjust field names if ESPN's
shape differs. Results are cached to CSV by the caller.
"""
import json, time, urllib.request
from datetime import date, timedelta
import pandas as pd

ESPN = "https://site.api.espn.com/apis/site/v2/sports"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _is_post(*objs):
    """True if any object carries ESPN season.type == 3 (postseason). Defensive: many shapes."""
    for o in objs:
        if not isinstance(o, dict):
            continue
        t = o.get("season", {}).get("type")
        if isinstance(t, dict):
            t = t.get("type") or t.get("id")
        try:
            if int(t) == 3:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _parse_upcoming_day(data, fights=False):
    """Scheduled (not-yet-played) events for a date. Team sports -> home/away; fights -> two athletes."""
    out = []
    for ev in data.get("events", []):
        post = _is_post(data, ev)
        for comp in ev.get("competitions", []):
            st = comp.get("status", {}).get("type", {})
            if st.get("completed"):
                continue
            cs = comp.get("competitors", [])
            if len(cs) != 2:
                continue
            if fights:
                def nm(c):
                    a = c.get("athlete") or c.get("team") or {}
                    return a.get("displayName")
                a, b = nm(cs[0]), nm(cs[1])
                if not a or not b:
                    continue
                out.append({"date": ev.get("date") or "", "home": a, "away": b, "post": post})
            else:
                home = next((c for c in cs if c.get("homeAway") == "home"), None)
                away = next((c for c in cs if c.get("homeAway") == "away"), None)
                if not home or not away:
                    continue
                h = (home.get("team") or {}).get("displayName")
                aw = (away.get("team") or {}).get("displayName")
                if not h or not aw:
                    continue
                out.append({"date": ev.get("date") or "", "home": h, "away": aw, "post": post})
    return out


def fetch_upcoming(league_path, days_ahead=10, fights=False, throttle=0.12, verbose=True):
    """Free upcoming fixtures from ESPN (no odds). Returns list of {date, home, away}."""
    rows, today = [], date.today()
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        try:
            rows += _parse_upcoming_day(_get(f"{ESPN}/{league_path}/scoreboard?dates={d.strftime('%Y%m%d')}&limit=400"), fights=fights)
        except Exception:
            pass
        time.sleep(throttle)
    seen, uniq = set(), []
    for r in rows:
        k = (r["home"], r["away"], r["date"][:10])
        if k in seen:
            continue
        seen.add(k); uniq.append(r)
    if verbose:
        npost = sum(1 for r in uniq if r.get("post"))
        tail = f" ({npost} postseason)" if npost else ""
        print(f"  ESPN {league_path}: {len(uniq)} upcoming fixtures (free, no odds){tail}")
    return uniq


def league_is_postseason(league_path):
    """One light scoreboard probe: is this league currently in postseason? (for odds mode)."""
    today = date.today()
    for i in range(0, 4):
        d = today + timedelta(days=i)
        try:
            data = _get(f"{ESPN}/{league_path}/scoreboard?dates={d.strftime('%Y%m%d')}&limit=400")
        except Exception:
            continue
        if _is_post(data):
            return True
        for ev in data.get("events", []):
            if _is_post(ev):
                return True
    return False


def _parse_day(data):
    out = []
    for ev in data.get("events", []):
        for comp in ev.get("competitions", []):
            if not comp.get("status", {}).get("type", {}).get("completed"):
                continue
            cs = comp.get("competitors", [])
            home = next((c for c in cs if c.get("homeAway") == "home"), None)
            away = next((c for c in cs if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            try:
                hs, as_ = int(home.get("score")), int(away.get("score"))
            except (TypeError, ValueError):
                continue
            out.append({"date": (ev.get("date") or "")[:10],
                        "home": (home.get("team") or {}).get("displayName"),
                        "away": (away.get("team") or {}).get("displayName"),
                        "home_score": hs, "away_score": as_})
    return out


def _parse_fight_day(data):
    out = []
    for ev in data.get("events", []):
        for comp in ev.get("competitions", []):
            if not comp.get("status", {}).get("type", {}).get("completed"):
                continue
            cs = comp.get("competitors", [])
            if len(cs) != 2:
                continue
            w = next((c for c in cs if c.get("winner")), None)
            l = next((c for c in cs if not c.get("winner")), None)
            if not w or not l:
                continue
            def nm(c):
                a = c.get("athlete") or c.get("team") or {}
                return a.get("displayName")
            wn, ln = nm(w), nm(l)
            if not wn or not ln:
                continue
            out.append({"date": (ev.get("date") or "")[:10], "winner": wn, "loser": ln})
    return out


def fetch_fights(league_path, start, end, throttle=0.12, verbose=True):
    """Combat sports (MMA/boxing): returns DataFrame[date, winner, loser] of completed bouts."""
    rows, d, nd, ne = [], start, 0, 0
    while d <= end:
        try:
            rows += _parse_fight_day(_get(f"{ESPN}/{league_path}/scoreboard?dates={d.strftime('%Y%m%d')}&limit=400"))
        except Exception:
            ne += 1
        d += timedelta(days=1); nd += 1; time.sleep(throttle)
    df = pd.DataFrame(rows)
    if len(df):
        df = df.drop_duplicates(subset=["date", "winner", "loser"]).reset_index(drop=True)
    if verbose:
        print(f"  ESPN {league_path}: {len(df)} fights over {nd} days ({ne} day-errors)")
        if len(df):
            print(f"    sample: {df.iloc[0]['winner']} def. {df.iloc[0]['loser']} ({df.iloc[0]['date']})")
    return df


def fetch_results(league_path, start, end, throttle=0.12, verbose=True):
    """start, end: datetime.date. Iterates day by day (reliable across ESPN leagues)."""
    rows, d, n_days, n_err = [], start, 0, 0
    while d <= end:
        try:
            rows += _parse_day(_get(f"{ESPN}/{league_path}/scoreboard?dates={d.strftime('%Y%m%d')}&limit=400"))
        except Exception:
            n_err += 1
        d += timedelta(days=1); n_days += 1; time.sleep(throttle)
    df = pd.DataFrame(rows)
    if len(df):
        df = df.drop_duplicates(subset=["date", "home", "away"]).reset_index(drop=True)
    if verbose:
        print(f"  ESPN {league_path}: {len(df)} games over {n_days} days ({n_err} day-errors)")
        if len(df):
            print(f"    sample: {df.iloc[0]['home']} {df.iloc[0]['home_score']}-{df.iloc[0]['away_score']} {df.iloc[0]['away']} ({df.iloc[0]['date']})")
    return df


def fetch_tennis_upcoming(days_ahead=10, max_matches=60, throttle=0.12, verbose=True):
    """Upcoming singles matches from ESPN tennis. A tournament is ONE event whose
    `groupings` (Men's/Women's Singles) hold the individual matches. We keep only
    not-yet-played ones (status state 'pre'). Returns
    [{date, home, away, wta(bool), tour, round}]."""
    out, seen = [], set()
    for slug in ("tennis/atp", "tennis/wta"):
        ev = None
        for i in range(-1, days_ahead + 1):
            d = (date.today() + timedelta(days=i)).strftime("%Y%m%d")
            try:
                data = _get(f"{ESPN}/{slug}/scoreboard?dates={d}")
            except Exception:
                continue
            if data.get("events"):
                ev = data["events"][0]; break
        if not ev:
            continue
        tour_name = ev.get("name", "")
        for g in ev.get("groupings", []):
            gslug = (g.get("grouping") or {}).get("slug", "")
            if gslug not in ("mens-singles", "womens-singles"):
                continue
            wta = (gslug == "womens-singles")
            for c in g.get("competitions", []):
                st = c.get("status", {}).get("type", {})
                if st.get("state") != "pre" or st.get("completed"):
                    continue
                cid = c.get("id")
                if cid in seen:
                    continue
                cs = c.get("competitors", [])
                if len(cs) != 2:
                    continue
                hm = next((x for x in cs if x.get("homeAway") == "home"), cs[0])
                am = next((x for x in cs if x.get("homeAway") == "away"), cs[-1])
                hn = (hm.get("athlete") or {}).get("displayName")
                an = (am.get("athlete") or {}).get("displayName")
                if not hn or not an:
                    continue
                seen.add(cid)
                out.append({"date": c.get("date") or c.get("startDate"), "home": hn, "away": an,
                            "wta": wta, "tour": tour_name,
                            "round": (c.get("round") or {}).get("displayName") if isinstance(c.get("round"), dict) else None})
        time.sleep(throttle)
    if verbose:
        print(f"  ESPN tennis: {len(out)} upcoming singles matches (free, no odds)")
    return out[:max_matches]


def fetch_tennis_results(start, end, tours=("tennis/atp", "tennis/wta"), throttle=0.12, verbose=True):
    """Completed ATP/WTA singles matches from ESPN's public scoreboard, day by
    day, for the Elo history. Mirrors fetch_tennis_upcoming but keeps finished
    matches and records the winner and loser. Returns a DataFrame with the columns
    tennis_elo expects (tourney_date as a YYYYMMDD int, surface, winner_name,
    loser_name) plus a `wta` flag for tour splitting. ESPN does not reliably expose
    the court surface, so it defaults to 'Hard' — only the clay sub-Elo would use
    it, and that path is taken for clay events only."""
    rows, seen, n_days, n_err = [], set(), 0, 0
    for slug in tours:
        wta = (slug == "tennis/wta")
        d = start
        while d <= end:
            ds = d.strftime("%Y%m%d")
            try:
                data = _get(f"{ESPN}/{slug}/scoreboard?dates={ds}")
                for ev in data.get("events", []):
                    for g in ev.get("groupings", []):
                        gslug = (g.get("grouping") or {}).get("slug", "")
                        if gslug not in ("mens-singles", "womens-singles"):
                            continue
                        for c in g.get("competitions", []):
                            cid = c.get("id")
                            if cid in seen:   # ESPN repeats a tournament's matches on every day it is live
                                continue
                            st = c.get("status", {}).get("type", {})
                            if not (st.get("completed") or st.get("state") == "post"):
                                continue
                            cs = c.get("competitors", [])
                            if len(cs) != 2:
                                continue
                            win = next((x for x in cs if x.get("winner")), None)
                            los = next((x for x in cs if not x.get("winner")), None)
                            if not win or not los:
                                continue
                            wn = (win.get("athlete") or {}).get("displayName")
                            ln = (los.get("athlete") or {}).get("displayName")
                            if not wn or not ln:
                                continue
                            md = (c.get("date") or "")[:10].replace("-", "")   # the real match date, not the query day
                            seen.add(cid)
                            rows.append({"tourney_date": int(md) if md.isdigit() else int(ds),
                                         "surface": "Hard", "winner_name": wn, "loser_name": ln, "wta": wta})
            except Exception:
                n_err += 1
            d += timedelta(days=1); n_days += 1; time.sleep(throttle)
    df = pd.DataFrame(rows)
    if len(df):
        df = df.drop_duplicates(subset=["tourney_date", "winner_name", "loser_name"]).reset_index(drop=True)
    if verbose:
        print(f"  ESPN tennis: {len(df)} completed singles matches over {n_days} days ({n_err} day-errors)")
    return df
