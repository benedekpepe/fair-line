"""
oddsapi_events.py — upcoming fixtures from The Odds API's /events endpoint.

/events is FREE (it does not count against the usage quota) and reachable from CI,
so it replaces the old ESPN fixture fallback (ESPN blocks datacenter/CI IPs with 403).

fetch_events(sport_key) returns the same shape the exporters expect from the old
espn_loader.fetch_upcoming(): a list of {"date", "home", "away", "post"}.
'date' is the ISO commence_time; 'post' is always False (the endpoint carries no
postseason flag), which only disables the small playoff total-dampening in the
model-only view.
"""
import os, json, urllib.request, urllib.error

API = "https://api.the-odds-api.com/v4"
KEY = os.environ.get("ODDS_API_KEY", "")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_events(sport_key, verbose=True):
    """Free upcoming fixtures for one Odds API sport key -> [{date, home, away, post}]."""
    if not KEY:
        return []
    try:
        evs = _get(f"{API}/sports/{sport_key}/events?apiKey={KEY}")
    except Exception as e:
        if verbose:
            print(f"  /events {sport_key}: unavailable ({str(e)[:50]})")
        return []
    out = []
    for ev in evs or []:
        h, a = ev.get("home_team"), ev.get("away_team")
        if not h or not a:
            continue
        out.append({"date": ev.get("commence_time") or "", "home": h, "away": a, "post": False})
    if verbose:
        print(f"  /events {sport_key}: {len(out)} upcoming")
    return out
