"""
Backtest: walk-forward ROI measurement
=======================================

What it does:
  It walks through a season in chronological order and ALWAYS learns only
  from the earlier matches (walk-forward), then looks for value on the next
  match based on the real odds stored in the CSV. If it finds value above
  the threshold, it "places" a 1-unit stake and settles the win/loss based
  on the actual result. At the end it prints the ROI, the number of bets and
  the hit rate.

  This is the moment of truth: if the ROI here is consistently negative, then
  the model does not (yet) beat the market -- and there is no point charging
  money for it.

Using it with real data:
  1) Download a season from football-data.co.uk (e.g. Premier League
     2023/24 -> "E0.csv"). Put it in the data/raw folder.
  2) Set the CSV_FILE variable below to the file name.
  3) python src/backtest.py
"""


import pandas as pd

from models.dixon_coles import fit_dixon_coles, predict_match, value


# football-data.co.uk odds columns. We try Bet365 first, then the market
# average (Avg). [home, draw, away]
ODDS_COLUMN_SETS = [
    ("B365H", "B365D", "B365A"),
    ("AvgH", "AvgD", "AvgA"),
    ("BbAvH", "BbAvD", "BbAvA"),
]


def load_with_odds(csv_path):
    """Load the matches together with the result AND odds columns."""
    df = pd.read_csv(csv_path)

    # which odds set is present in the file?
    odds_cols = None
    for cols in ODDS_COLUMN_SETS:
        if all(c in df.columns for c in cols):
            odds_cols = cols
            break
    if odds_cols is None:
        raise ValueError("Could not find odds columns in the CSV "
                         "(B365H/D/A or AvgH/D/A).")

    df = df.rename(columns={
        "FTHG": "home_goals", "FTAG": "away_goals",
        "HomeTeam": "home_team", "AwayTeam": "away_team",
        odds_cols[0]: "odds_home", odds_cols[1]: "odds_draw", odds_cols[2]: "odds_away",
    })
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    keep = ["Date", "home_team", "away_team", "home_goals", "away_goals",
            "odds_home", "odds_draw", "odds_away"]
    df = df[keep].dropna().sort_values("Date").reset_index(drop=True)
    print(f"Odds columns used: {odds_cols}")
    return df


def actual_result(home_goals, away_goals):
    """The real outcome of the match: '1', 'X' or '2'."""
    if home_goals > away_goals:
        return "1"
    elif home_goals == away_goals:
        return "X"
    return "2"


def run_backtest(df, min_train=100, retrain_every=10,
                 value_threshold=0.05, stake=1.0, xi=0.0):
    """
    Walk-forward backtest.
      min_train       : start betting after this many matches (training window)
      retrain_every   : refit the model every this many matches
      value_threshold : bet when value is above this (0.05 = +5%)
      stake           : stake per bet (in units)
    """
    bets = []
    model = None

    for i in range(min_train, len(df)):
        # refit periodically, from ALL matches BEFORE the current one
        if model is None or (i - min_train) % retrain_every == 0:
            train = df.iloc[:i]
            model = fit_dixon_coles(train, xi=xi)

        row = df.iloc[i]
        # if either team has not appeared in training yet, skip it
        if row["home_team"] not in model["idx"] or row["away_team"] not in model["idx"]:
            continue

        p = predict_match(model, row["home_team"], row["away_team"])
        model_probs = {"1": p["home_win"], "X": p["draw"], "2": p["away_win"]}
        odds = {"1": row["odds_home"], "X": row["odds_draw"], "2": row["odds_away"]}

        # value for all three outcomes; bet on the best one if above threshold
        values = {k: value(model_probs[k], odds[k]) for k in ("1", "X", "2")}
        best = max(values, key=values.get)
        if values[best] < value_threshold:
            continue  # not enough value, no bet

        result = actual_result(row["home_goals"], row["away_goals"])
        profit = (odds[best] - 1) * stake if best == result else -stake
        bets.append({
            "date": row["Date"], "match": f"{row['home_team']} - {row['away_team']}",
            "bet": best, "odds": odds[best], "value": values[best],
            "result": result, "profit": profit,
        })

    bets_df = pd.DataFrame(bets)
    n = len(bets_df)
    if n == 0:
        print("\nNo bets cleared the threshold. "
              "Try lowering value_threshold.")
        return bets_df

    total_stake = n * stake
    total_profit = bets_df["profit"].sum()
    roi = total_profit / total_stake
    hit_rate = (bets_df["profit"] > 0).mean()

    print("\n=== BACKTEST RESULT ===")
    print(f"Number of bets:      {n}")
    print(f"Total stake:         {total_stake:.1f} units")
    print(f"Profit:              {total_profit:+.2f} units")
    print(f"ROI:                 {roi:+.1%}")
    print(f"Hit rate:            {hit_rate:.1%}")
    print(f"Average odds:        {bets_df['odds'].mean():.2f}")
    return bets_df


if __name__ == "__main__":
    from config import PROJECT as project_root

    # ----- SET YOUR OWN SEASON FILE HERE -----
    CSV_FILE = "E0.csv"   # e.g. Premier League; put it in the data/raw folder
    csv_path = project_root / "data" / "raw" / CSV_FILE

    if not csv_path.exists():
        print(f"Not found: {csv_path}")
        print("Download a season from football-data.co.uk into data/raw,")
        print("and set the CSV_FILE variable to the file name.")
    else:
        df = load_with_odds(csv_path)
        print(f"Loaded matches: {len(df)}")
        bets_df = run_backtest(df, min_train=100, retrain_every=10,
                               value_threshold=0.05)
        # save the bet log so you can analyse it later
        if len(bets_df) > 0:
            out = project_root / "data" / "processed" / "backtest_bets.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            bets_df.to_csv(out, index=False)
            print(f"\nBet log saved: {out}")
