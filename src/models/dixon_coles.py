"""
Dixon-Coles football model
===========================

What this module does:
  1) Fits a Dixon-Coles model from historical match results. It estimates
     an ATTACK and a DEFENCE strength for every team, plus a shared home
     advantage. From these it derives the expected goals for any fixture.
  2) For a given match it builds a score matrix (P[i, j] = probability that
     the home side scores i and the away side scores j) and aggregates the
     market probabilities from it: 1 / X / 2 and Over/Under 2.5 goals.
  3) Compares the model probability with the bookmaker odds and computes the
     value (expected value).

Using it with your own data:
  Prepare a CSV with these columns (the free football-data.co.uk files look
  exactly like this):
      Date, HomeTeam, AwayTeam, FTHG, FTAG
  (FTHG = full-time home goals, FTAG = full-time away goals)
  Then: matches = load_matches("your_file.csv")

NOTE: this is a STRONG BASELINE, not a magic bullet. Its goal is to produce
honest probabilities that you then compare against the odds to look for value.
Always backtest it yourself before relying on it for anything.
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson


# ---------------------------------------------------------------------------
# 1) DATA LOADING
# ---------------------------------------------------------------------------
def load_matches(csv_path):
    """Load matches from a football-data.co.uk-style CSV."""
    df = pd.read_csv(csv_path)
    df = df.rename(columns={
        "FTHG": "home_goals",
        "FTAG": "away_goals",
        "HomeTeam": "home_team",
        "AwayTeam": "away_team",
    })
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df[["Date", "home_team", "away_team", "home_goals", "away_goals"]].dropna()


# ---------------------------------------------------------------------------
# 2) DIXON-COLES LOW-SCORE CORRECTION
# ---------------------------------------------------------------------------
def _tau(home_goals, away_goals, lam, mu, rho):
    """Corrects the dependence between the 0-0, 1-0, 0-1, 1-1 scores (Dixon-Coles)."""
    if home_goals == 0 and away_goals == 0:
        return 1.0 - lam * mu * rho
    elif home_goals == 0 and away_goals == 1:
        return 1.0 + lam * rho
    elif home_goals == 1 and away_goals == 0:
        return 1.0 + mu * rho
    elif home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    else:
        return 1.0


# ---------------------------------------------------------------------------
# 3) FITTING THE MODEL (maximum likelihood)
# ---------------------------------------------------------------------------
def fit_dixon_coles(matches, xi=0.0):
    """
    Fit the model. Returns a dict with the parameters.
    xi = time weighting: if > 0, older matches count less
         (e.g. xi=0.0019 ~ half-life of about half a year). 0 = all matches equal.
    """
    teams = sorted(set(matches["home_team"]) | set(matches["away_team"]))
    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}

    # time weights (the most recent match has weight 1)
    if xi > 0:
        latest = matches["Date"].max()
        days = (latest - matches["Date"]).dt.days.to_numpy()
        weights = np.exp(-xi * days)
    else:
        weights = np.ones(len(matches))

    h_idx = matches["home_team"].map(idx).to_numpy()
    a_idx = matches["away_team"].map(idx).to_numpy()
    hg = matches["home_goals"].to_numpy().astype(int)
    ag = matches["away_goals"].to_numpy().astype(int)

    # parameters: [attack(n), defence(n), rho, home_advantage]
    init = np.concatenate([
        np.zeros(n),            # attack strengths
        np.zeros(n),            # defence strengths
        [-0.05],                # rho
        [0.25],                 # home advantage
    ])

    def neg_log_likelihood(params):
        attack = params[:n]
        defence = params[n:2 * n]
        rho = params[2 * n]
        home = params[2 * n + 1]

        lam = np.exp(np.clip(attack[h_idx] + defence[a_idx] + home, -10, 10))   # home expected goals
        mu = np.exp(np.clip(attack[a_idx] + defence[h_idx], -10, 10))           # away expected goals

        log_lik = (poisson.logpmf(hg, lam) + poisson.logpmf(ag, mu))
        # Dixon-Coles correction for the low scores
        tau = np.array([_tau(hg[k], ag[k], lam[k], mu[k], rho)
                        for k in range(len(hg))])
        tau = np.clip(tau, 1e-10, None)
        log_lik = log_lik + np.log(tau)
        return -np.sum(weights * log_lik)

    # identifiability constraint: the mean of the attack strengths must be 0
    constraint = {"type": "eq", "fun": lambda p: np.sum(p[:n])}

    res = minimize(neg_log_likelihood, init, method="SLSQP",
                   constraints=[constraint], options={"maxiter": 200, "ftol": 1e-7})

    return {
        "teams": teams,
        "idx": idx,
        "attack": res.x[:n],
        "defence": res.x[n:2 * n],
        "rho": res.x[2 * n],
        "home_adv": res.x[2 * n + 1],
    }


# ---------------------------------------------------------------------------
# 4) PREDICTING A SINGLE MATCH
# ---------------------------------------------------------------------------
def predict_match(model, home_team, away_team, max_goals=10):
    """Build the score matrix and compute the market probabilities."""
    i, j = model["idx"][home_team], model["idx"][away_team]
    lam = np.exp(np.clip(model["attack"][i] + model["defence"][j] + model["home_adv"], -10, 10))
    mu = np.exp(np.clip(model["attack"][j] + model["defence"][i], -10, 10))
    rho = model["rho"]

    # Poisson probabilities for 0..max_goals goals
    home_p = poisson.pmf(np.arange(max_goals + 1), lam)
    away_p = poisson.pmf(np.arange(max_goals + 1), mu)
    matrix = np.outer(home_p, away_p)

    # Dixon-Coles correction on the 4 low cells
    for hg in (0, 1):
        for ag in (0, 1):
            matrix[hg, ag] *= _tau(hg, ag, lam, mu, rho)
    matrix /= matrix.sum()  # renormalise

    home_win = np.tril(matrix, -1).sum()   # i > j
    draw = np.trace(matrix)                # i == j
    away_win = np.triu(matrix, 1).sum()    # i < j

    # Over/Under 2.5 goals
    total = np.add.outer(np.arange(max_goals + 1), np.arange(max_goals + 1))
    over25 = matrix[total >= 3].sum()
    under25 = matrix[total <= 2].sum()

    return {
        "exp_home_goals": lam,
        "exp_away_goals": mu,
        "home_win": home_win, "draw": draw, "away_win": away_win,
        "over25": over25, "under25": under25,
    }


# ---------------------------------------------------------------------------
# 5) VALUE CALCULATION (vig removal + expected value)
# ---------------------------------------------------------------------------
def remove_vig(odds):
    """Remove the bookmaker margin (vig) -> 'fair' market probabilities."""
    inv = np.array([1.0 / o for o in odds])
    return inv / inv.sum()


def value(model_prob, odds):
    """value = model_probability * odds - 1 (positive -> favourable)."""
    return model_prob * odds - 1.0


# ---------------------------------------------------------------------------
# DEMO: a synthetic season, so you can see it run and produce sensible numbers
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    rng = np.random.default_rng(42)

    # 16 teams with "true" strengths; we generate a season from them
    n_teams = 16
    team_names = [f"Team_{c}" for c in "ABCDEFGHIJKLMNOP"[:n_teams]]
    true_attack = rng.normal(0, 0.35, n_teams)
    true_attack -= true_attack.mean()
    true_defence = rng.normal(0, 0.30, n_teams)
    true_home = 0.26

    rows = []
    base_date = pd.Timestamp("2025-08-01")
    md = 0
    for h in range(n_teams):
        for a in range(n_teams):
            if h == a:
                continue
            lam = np.exp(true_attack[h] + true_defence[a] + true_home)
            mu = np.exp(true_attack[a] + true_defence[h])
            rows.append({
                "Date": (base_date + pd.Timedelta(days=md // 8)).strftime("%d/%m/%Y"),
                "HomeTeam": team_names[h], "AwayTeam": team_names[a],
                "FTHG": rng.poisson(lam), "FTAG": rng.poisson(mu),
            })
            md += 1
    pd.DataFrame(rows).to_csv("/home/claude/demo_season.csv", index=False)

    matches = load_matches("/home/claude/demo_season.csv")
    print(f"Loaded matches: {len(matches)}")

    model = fit_dixon_coles(matches, xi=0.0)
    print(f"Home advantage (estimated): {model['home_adv']:.3f}  (true: {true_home})")
    print(f"Rho (low-score correction): {model['rho']:.3f}\n")

    # strength ranking by estimated attack
    order = np.argsort(model["attack"])[::-1]
    print("Estimated attack ranking (strong -> weak):")
    for k in order[:5]:
        print(f"  {model['teams'][k]:<10} attack={model['attack'][k]:+.2f} "
              f"defence={model['defence'][k]:+.2f}")

    # predict one specific match
    home, away = team_names[order[0]], team_names[order[-1]]
    p = predict_match(model, home, away)
    print(f"\nMatch: {home} (home) vs {away} (away)")
    print(f"  Expected goals: {p['exp_home_goals']:.2f} - {p['exp_away_goals']:.2f}")
    print(f"  1 (home win):  {p['home_win']*100:5.1f}%")
    print(f"  X (draw):      {p['draw']*100:5.1f}%")
    print(f"  2 (away win):  {p['away_win']*100:5.1f}%")
    print(f"  Over 2.5:      {p['over25']*100:5.1f}%")
    print(f"  Under 2.5:     {p['under25']*100:5.1f}%")

    # value example: suppose the bookmaker offers these 1X2 odds
    book_odds = [1.70, 4.00, 5.50]   # 1, X, 2
    fair = remove_vig(book_odds)
    model_probs = [p["home_win"], p["draw"], p["away_win"]]
    print("\nValue analysis (1X2):")
    print(f"  {'outcome':<10}{'odds':>6}{'fair%':>12}{'model%':>10}{'value':>9}")
    for label, o, fp, mp in zip(["1", "X", "2"], book_odds, fair, model_probs):
        v = value(mp, o)
        flag = "  <-- VALUE" if v > 0.03 else ""
        print(f"  {label:<10}{o:>6.2f}{fp*100:>11.1f}%{mp*100:>9.1f}%{v:>+9.1%}{flag}")
