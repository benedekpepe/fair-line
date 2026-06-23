"""
backtest_models.py — model-accuracy backtest for the margin and fighter-Elo sports.

Reads the cached ESPN histories in data/raw/*_hist.csv, splits each chronologically
(train on the older 70%, test on the newer 30%), fits the model on train, and scores
its predictions on test. Reports accuracy, Brier score and log-loss.

IMPORTANT: this measures PREDICTION QUALITY (is the model better than a coin / than
"always pick home"?), NOT betting ROI. ROI needs historical closing odds, which are
only available behind a paid feed — so these sports still have no verified P&L.
"""
import sys, random
import numpy as np
import pandas as pd

from config import RAW, SRC


sys.path.insert(0, str(SRC))
from models import margin_model
from models import fighter_elo

LABEL = {"basketball_wnba": "WNBA", "basketball_nba": "NBA", "basketball_euroleague": "EuroLeague",
         "aussierules_afl": "AFL", "rugbyleague_nrl": "NRL", "baseball_mlb": "MLB",
         "americanfootball_nfl": "NFL", "icehockey_nhl": "NHL",
         "mma_mixed_martial_arts": "UFC/MMA", "boxing": "Boxing"}


def _clip(p, e=1e-6):
    return min(1 - e, max(e, p))


def bt_margin(df, label):
    df = df.dropna(subset=["home", "away", "home_score", "away_score"]).copy()
    if "date" in df.columns:
        df = df.sort_values("date")
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"])
    n = len(df)
    if n < 60:
        print(f"  {label:11} too few games ({n})"); return
    k = int(n * 0.7); tr, te = df.iloc[:k], df.iloc[k:]
    m = margin_model.fit_margin(tr)
    if not m:
        print(f"  {label:11} fit failed"); return
    acc = br = ll = cnt = 0; homewins = 0; terr = []
    for _, r in te.iterrows():
        hs, as_ = r["home_score"], r["away_score"]
        if hs == as_:
            continue
        y = 1.0 if hs > as_ else 0.0
        p = _clip(margin_model.p_home_win(m, r["home"], r["away"]))
        acc += 1 if (p > 0.5) == (y > 0.5) else 0
        br += (p - y) ** 2; ll += -(y * np.log(p) + (1 - y) * np.log(1 - p)); cnt += 1
        homewins += y
        e = margin_model.expected(m, r["home"], r["away"]); terr.append(e["total"] - (hs + as_))
    if not cnt:
        print(f"  {label:11} no decided test games"); return
    base = homewins / cnt
    rmse = float(np.sqrt(np.mean(np.square(terr))))
    print(f"  {label:11} test={cnt:5d}  acc={acc / cnt * 100:5.1f}%  (home-base {base * 100:4.1f}%)  "
          f"Brier={br / cnt:.3f}  logloss={ll / cnt:.3f}  total-RMSE={rmse:4.1f}")


def bt_fights(df, label):
    df = df.dropna(subset=["winner", "loser"]).copy()
    if "date" in df.columns:
        df = df.sort_values("date")
    n = len(df)
    if n < 80:
        print(f"  {label:11} too few fights ({n})"); return
    k = int(n * 0.7); tr, te = df.iloc[:k], df.iloc[k:]
    m = fighter_elo.build_elo(tr)
    if not m:
        print(f"  {label:11} fit failed"); return
    random.seed(0)
    acc = br = ll = cnt = 0; known = 0
    for _, r in te.iterrows():
        w, l = r["winner"], r["loser"]
        if random.random() < 0.5:
            a, b, y = w, l, 1.0
        else:
            a, b, y = l, w, 0.0
        p = _clip(fighter_elo.predict(m, a, b))
        acc += 1 if (p > 0.5) == (y > 0.5) else 0
        br += (p - y) ** 2; ll += -(y * np.log(p) + (1 - y) * np.log(1 - p)); cnt += 1
        if w in m["fighters"] and l in m["fighters"]:
            known += 1
    print(f"  {label:11} test={cnt:5d}  acc={acc / cnt * 100:5.1f}%  (coin 50%)        "
          f"Brier={br / cnt:.3f}  logloss={ll / cnt:.3f}  both-known={known / cnt * 100:3.0f}%")



def main():
    files = sorted(RAW.glob("*_hist.csv"))
    if not files:
        print("No *_hist.csv in data/raw — run run_all.py first."); return
    print("=== Model-accuracy backtest (chronological 70/30 split) ===")
    print("  Measures PREDICTION quality, not betting ROI. Baselines: coin=Brier 0.250;")
    print("  beating 'home-base' accuracy = the model adds signal over home-field alone.\n")
    for f in files:
        key = f.stem[:-5] if f.stem.endswith("_hist") else f.stem
        label = LABEL.get(key, key)
        try:
            df = pd.read_csv(f)
        except Exception as e:
            print(f"  {label}: read error {str(e)[:40]}"); continue
        cols = set(df.columns)
        if {"winner", "loser"} <= cols:
            bt_fights(df, label)
        elif {"home", "away", "home_score", "away_score"} <= cols:
            bt_margin(df, label)
        else:
            print(f"  {label}: unrecognised columns {sorted(cols)}")


if __name__ == "__main__":
    main()
