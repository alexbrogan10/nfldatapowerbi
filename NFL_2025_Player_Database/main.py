"""
NFL 2025 Player Statistics Database Builder
=============================================

Downloads 2025 NFL regular-season play-by-play, roster, schedule and
advanced-stat data from the public nflverse data project, aggregates it
into a Power BI-ready star schema, and exports clean UTF-8 CSV files to
the ``data`` folder.

Nothing here is hand-typed: every statistic is derived at runtime from
nflverse's published data releases (https://github.com/nflverse/nflverse-data).

Run with:  python main.py
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEASON = 2025
SEASON_TYPE = "REG"  # regular season only

DATA_DIR = Path(__file__).resolve().parent / "data"

NFLVERSE_RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
PBP_URL = f"{NFLVERSE_RELEASES}/pbp/play_by_play_{SEASON}.parquet"
ROSTER_URL = f"{NFLVERSE_RELEASES}/rosters/roster_{SEASON}.parquet"
GAMES_URL = f"{NFLVERSE_RELEASES}/schedules/games.parquet"
TEAM_DESC_URL = "https://raw.githubusercontent.com/nflverse/nflfastR-data/master/teams_colors_logos.csv"
PFR_ADV_URL = f"{NFLVERSE_RELEASES}/pfr_advstats/advstats_season_{{category}}.parquet"

REQUEST_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def log(message: str) -> None:
    """Print a timestamped progress message."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}")


def download_bytes(url: str, description: str) -> bytes:
    """Fetch a URL's raw content with a bounded timeout and clear error handling."""
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Failed to download {description} from {url}: {exc}") from exc
    return response.content


def fetch_parquet(url: str, description: str) -> pd.DataFrame:
    """Download a parquet file from nflverse with basic error handling."""
    log(f"Downloading {description} ...")
    raw = download_bytes(url, description)
    try:
        df = pd.read_parquet(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 - surface any parse failure
        raise RuntimeError(f"Failed to parse {description} from {url}: {exc}") from exc
    log(f"  -> {len(df):,} rows retrieved")
    return df


def fetch_csv(url: str, description: str) -> pd.DataFrame:
    """Download a CSV file from a public URL with basic error handling."""
    log(f"Downloading {description} ...")
    raw = download_bytes(url, description)
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to parse {description} from {url}: {exc}") from exc
    log(f"  -> {len(df):,} rows retrieved")
    return df


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise division that returns NaN instead of raising on 0/0."""
    with np.errstate(divide="ignore", invalid="ignore"):
        result = numerator / denominator
    return result.replace([np.inf, -np.inf], np.nan)


def count_id_occurrences(df: pd.DataFrame, id_columns: list[str]) -> pd.Series:
    """Count how many times each player id appears across a set of columns."""
    ids = pd.concat([df[col].dropna() for col in id_columns if col in df.columns])
    return ids.value_counts()


def games_by_player(df: pd.DataFrame, id_columns: list[str]) -> pd.Series:
    """Count distinct games a player shows up in across a set of id columns."""
    frames = []
    for col in id_columns:
        if col in df.columns:
            frames.append(df.loc[df[col].notna(), ["game_id", col]].rename(columns={col: "player_id"}))
    if not frames:
        return pd.Series(dtype="int64")
    stacked = pd.concat(frames, ignore_index=True).drop_duplicates()
    return stacked.groupby("player_id")["game_id"].nunique()


def tidy_numeric_columns(df: pd.DataFrame, count_cols: list[str], rate_cols: dict[str, int]) -> pd.DataFrame:
    """Round counting stats to whole numbers (nullable Int64, so missing
    values stay blank rather than becoming 0) and round rate/average
    columns to a sane number of decimals for clean CSV output."""
    df = df.copy()
    for col in count_cols:
        if col in df.columns:
            df[col] = df[col].round().astype("Int64")
    for col, decimals in rate_cols.items():
        if col in df.columns:
            df[col] = df[col].round(decimals)
    return df


def export_csv(df: pd.DataFrame, name: str) -> int:
    """Write a DataFrame to the data folder as a clean UTF-8 CSV."""
    path = DATA_DIR / name
    df.to_csv(path, index=False, encoding="utf-8")
    log(f"Wrote {name} ({len(df):,} rows, {len(df.columns)} columns)")
    return len(df)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_play_by_play() -> pd.DataFrame:
    """Load 2025 play-by-play data and filter to the regular season."""
    df = fetch_parquet(PBP_URL, f"{SEASON} play-by-play data")
    df = df[df["season_type"] == SEASON_TYPE].copy()
    log(f"  -> {len(df):,} regular-season plays across {df['game_id'].nunique()} games")
    return df


def load_roster() -> pd.DataFrame:
    """Load the 2025 seasonal roster (one row per player with bio info)."""
    df = fetch_parquet(ROSTER_URL, f"{SEASON} player roster")
    df["birth_date"] = pd.to_datetime(df["birth_date"], errors="coerce")
    return df


def load_games() -> pd.DataFrame:
    """Load the full schedule/results file and filter to 2025 regular season games."""
    df = fetch_parquet(GAMES_URL, "team schedules & results")
    df = df[(df["season"] == SEASON) & (df["game_type"] == "REG")].copy()
    log(f"  -> {len(df):,} regular-season games for {SEASON}")
    return df


def load_team_desc() -> pd.DataFrame:
    """Load team metadata (conference/division) and keep only current teams."""
    return fetch_csv(TEAM_DESC_URL, "team conference/division reference data")


def load_pfr_advanced(category: str) -> pd.DataFrame:
    """Load Pro-Football-Reference advanced season stats for a category
    (pass, rush, rec, or def) and filter to the target season. Used only
    as a supplemental source for games-started and optional metrics -
    the core stat tables are built directly from play-by-play data."""
    url = PFR_ADV_URL.format(category=category)
    try:
        df = fetch_parquet(url, f"PFR advanced {category} stats")
    except RuntimeError as exc:
        log(f"  -> WARNING: {exc}. Continuing without this supplemental source.")
        return pd.DataFrame()
    return df[df["season"] == SEASON].copy()


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------

PARTICIPATION_COLUMNS = [
    "passer_player_id", "rusher_player_id", "receiver_player_id",
    "kicker_player_id", "punter_player_id",
    "solo_tackle_1_player_id", "solo_tackle_2_player_id",
    "assist_tackle_1_player_id", "assist_tackle_2_player_id",
    "assist_tackle_3_player_id", "assist_tackle_4_player_id",
    "tackle_with_assist_1_player_id", "tackle_with_assist_2_player_id",
    "tackle_for_loss_1_player_id", "tackle_for_loss_2_player_id",
    "qb_hit_1_player_id", "qb_hit_2_player_id",
    "sack_player_id", "half_sack_1_player_id", "half_sack_2_player_id",
    "interception_player_id", "pass_defense_1_player_id", "pass_defense_2_player_id",
    "forced_fumble_player_1_player_id", "forced_fumble_player_2_player_id",
    "fumble_recovery_1_player_id", "fumble_recovery_2_player_id",
    "safety_player_id",
]


def build_players_table(pbp: pd.DataFrame, roster: pd.DataFrame, pfr_games_started: pd.Series) -> pd.DataFrame:
    """Build the central Players dimension table.

    A player is included if he recorded at least one statistical play
    (pass, rush, reception, tackle, sack, kick, punt, etc.) in a 2025
    regular-season game - this is the population every stat table below
    is built from, which keeps the star schema fully connected. Players
    with zero individually-tracked plays (e.g. offensive linemen) are not
    represented as standalone rows because nflverse does not publish
    individual snap/play data for them.
    """
    log("Building Players table ...")

    participants = games_by_player(pbp, PARTICIPATION_COLUMNS)
    players = pd.DataFrame({"PlayerID": participants.index, "GamesPlayed": participants.values})

    roster_cols = [
        "gsis_id", "first_name", "last_name", "full_name", "team", "position",
        "jersey_number", "birth_date", "height", "weight", "college", "pfr_id",
    ]
    roster_slim = roster[roster_cols].drop_duplicates(subset="gsis_id").rename(columns={"gsis_id": "PlayerID"})

    players = players.merge(roster_slim, on="PlayerID", how="left")

    # Age as of the start of the season (Sept 1), the standard nflverse convention
    season_start = pd.Timestamp(year=SEASON, month=9, day=1)
    age_years = (season_start - players["birth_date"]).dt.days / 365.25
    players["Age"] = age_years.round().astype("Int64")

    players["GamesStarted"] = players["PlayerID"].map(pfr_games_started).fillna(0).astype(int)
    players["college"] = players["college"].fillna("Unknown")

    players = players.rename(columns={
        "first_name": "FirstName",
        "last_name": "LastName",
        "full_name": "FullName",
        "team": "Team",
        "position": "Position",
        "jersey_number": "Jersey",
        "height": "Height",
        "weight": "Weight",
        "college": "College",
    })

    players = players.drop_duplicates(subset="PlayerID")

    final_cols = [
        "PlayerID", "FirstName", "LastName", "FullName", "Team", "Position",
        "Jersey", "Age", "Height", "Weight", "College", "GamesPlayed", "GamesStarted",
    ]
    players = players[final_cols].sort_values("FullName", na_position="last").reset_index(drop=True)
    players = tidy_numeric_columns(players, count_cols=["Jersey", "Height", "Weight"], rate_cols={})
    return players


def build_passing_table(pbp: pd.DataFrame, pfr_games_started: pd.Series) -> pd.DataFrame:
    log("Building PassingStats table ...")

    pass_plays = pbp[pbp["passer_player_id"].notna()].copy()

    games = pass_plays.groupby("passer_player_id")["game_id"].nunique()

    agg = pass_plays.groupby("passer_player_id").agg(
        Completions=("complete_pass", "sum"),
        Attempts=("pass_attempt", "sum"),
        PassingYards=("passing_yards", "sum"),
        PassingTD=("pass_touchdown", "sum"),
        Interceptions=("interception", "sum"),
        SacksTaken=("sack", "sum"),
        SackYardsLost=("sack", lambda s: pass_plays.loc[s.index, "yards_gained"].where(s == 1, 0).sum()),
        LongestPass=("passing_yards", lambda s: s.where(pass_plays.loc[s.index, "complete_pass"] == 1).max()),
    )

    df = agg.reset_index().rename(columns={"passer_player_id": "PlayerID"})
    df["Games"] = df["PlayerID"].map(games).fillna(0).astype(int)
    df["Starts"] = df["PlayerID"].map(pfr_games_started).fillna(0).astype(int)

    df["CompletionPct"] = safe_divide(df["Completions"], df["Attempts"]) * 100
    df["YardsPerAttempt"] = safe_divide(df["PassingYards"], df["Attempts"])
    df["YardsPerCompletion"] = safe_divide(df["PassingYards"], df["Completions"])

    # Standard NFL passer rating formula
    att = df["Attempts"].replace(0, np.nan)
    a = ((df["Completions"] / att) - 0.3) * 5
    b = ((df["PassingYards"] / att) - 3) * 0.25
    c = (df["PassingTD"] / att) * 20
    d = 2.375 - ((df["Interceptions"] / att) * 25)
    a, b, c, d = (comp.clip(lower=0, upper=2.375) for comp in (a, b, c, d))
    df["PasserRating"] = (((a + b + c + d) / 6) * 100).round(1)

    df["SackYardsLost"] = df["SackYardsLost"].abs()

    final_cols = [
        "PlayerID", "Games", "Starts", "Completions", "Attempts", "CompletionPct",
        "PassingYards", "PassingTD", "Interceptions", "PasserRating", "SacksTaken",
        "SackYardsLost", "LongestPass", "YardsPerAttempt", "YardsPerCompletion",
    ]
    df = df[final_cols].sort_values("PassingYards", ascending=False).reset_index(drop=True)
    df = tidy_numeric_columns(
        df,
        count_cols=["Completions", "Attempts", "PassingYards", "PassingTD", "Interceptions",
                    "SacksTaken", "SackYardsLost", "LongestPass"],
        rate_cols={"CompletionPct": 1, "PasserRating": 1, "YardsPerAttempt": 2, "YardsPerCompletion": 2},
    )
    return df


def build_rushing_table(pbp: pd.DataFrame) -> pd.DataFrame:
    log("Building RushingStats table ...")

    rush_plays = pbp[pbp["rusher_player_id"].notna()].copy()
    games = rush_plays.groupby("rusher_player_id")["game_id"].nunique()

    fumbles = rush_plays[rush_plays["fumbled_1_player_id"] == rush_plays["rusher_player_id"]]
    fumble_counts = fumbles.groupby("rusher_player_id").size()

    agg = rush_plays.groupby("rusher_player_id").agg(
        RushAttempts=("rush_attempt", "sum"),
        RushYards=("rushing_yards", "sum"),
        RushLong=("rushing_yards", "max"),
        RushTD=("rush_touchdown", "sum"),
        FirstDownRushes=("first_down_rush", "sum"),
    )

    df = agg.reset_index().rename(columns={"rusher_player_id": "PlayerID"})
    df["Games"] = df["PlayerID"].map(games).fillna(0).astype(int)
    df["Fumbles"] = df["PlayerID"].map(fumble_counts).fillna(0).astype(int)
    df["RushAverage"] = safe_divide(df["RushYards"], df["RushAttempts"]).round(2)

    final_cols = [
        "PlayerID", "Games", "RushAttempts", "RushYards", "RushAverage",
        "RushLong", "RushTD", "Fumbles", "FirstDownRushes",
    ]
    df = df[final_cols].sort_values("RushYards", ascending=False).reset_index(drop=True)
    df = tidy_numeric_columns(
        df,
        count_cols=["RushAttempts", "RushYards", "RushLong", "RushTD", "FirstDownRushes"],
        rate_cols={"RushAverage": 2},
    )
    return df


def build_receiving_table(pbp: pd.DataFrame, pfr_rec: pd.DataFrame) -> pd.DataFrame:
    log("Building ReceivingStats table ...")

    targets = pbp[pbp["receiver_player_id"].notna()].copy()

    agg = targets.groupby("receiver_player_id").agg(
        Targets=("pass_attempt", "sum"),
        Receptions=("complete_pass", "sum"),
        ReceivingYards=("receiving_yards", "sum"),
        ReceivingLong=("receiving_yards", lambda s: s.where(targets.loc[s.index, "complete_pass"] == 1).max()),
        ReceivingTD=("pass_touchdown", "sum"),
        ReceivingFirstDowns=("first_down_pass", "sum"),
        YardsAfterCatch=("yards_after_catch", "sum"),
    )

    df = agg.reset_index().rename(columns={"receiver_player_id": "PlayerID"})
    df["CatchPct"] = safe_divide(df["Receptions"], df["Targets"]) * 100
    df["ReceivingAverage"] = safe_divide(df["ReceivingYards"], df["Receptions"]).round(2)

    # Optional enrichment: PFR-tracked drops, joined via the pfr_id crosswalk
    if not pfr_rec.empty and "pfr_id" in pfr_rec.columns:
        drop_map = pfr_rec.groupby("pfr_id")["drop"].sum()
        id_crosswalk = pbp_receiver_to_pfr_id(pbp)
        df["Drops"] = df["PlayerID"].map(id_crosswalk).map(drop_map)
    else:
        df["Drops"] = np.nan

    final_cols = [
        "PlayerID", "Targets", "Receptions", "CatchPct", "ReceivingYards",
        "ReceivingAverage", "ReceivingLong", "ReceivingTD", "ReceivingFirstDowns",
        "Drops", "YardsAfterCatch",
    ]
    df = df[final_cols].sort_values("ReceivingYards", ascending=False).reset_index(drop=True)
    df = tidy_numeric_columns(
        df,
        count_cols=["Targets", "Receptions", "ReceivingYards", "ReceivingLong", "ReceivingTD",
                    "ReceivingFirstDowns", "Drops", "YardsAfterCatch"],
        rate_cols={"CatchPct": 1, "ReceivingAverage": 2},
    )
    return df


def pbp_receiver_to_pfr_id(pbp: pd.DataFrame) -> dict:
    """Placeholder crosswalk hook - populated by main() once the roster
    (which carries both gsis_id and pfr_id) is available."""
    return {}


def build_defense_table(pbp: pd.DataFrame) -> pd.DataFrame:
    log("Building DefenseStats table ...")

    solo = count_id_occurrences(pbp, ["solo_tackle_1_player_id", "solo_tackle_2_player_id"])
    assisted = count_id_occurrences(pbp, [
        "assist_tackle_1_player_id", "assist_tackle_2_player_id",
        "assist_tackle_3_player_id", "assist_tackle_4_player_id",
        "tackle_with_assist_1_player_id", "tackle_with_assist_2_player_id",
    ])
    tfl = count_id_occurrences(pbp, ["tackle_for_loss_1_player_id", "tackle_for_loss_2_player_id"])
    qb_hits = count_id_occurrences(pbp, ["qb_hit_1_player_id", "qb_hit_2_player_id"])
    full_sacks = count_id_occurrences(pbp, ["sack_player_id"])
    half_sacks = count_id_occurrences(pbp, ["half_sack_1_player_id", "half_sack_2_player_id"])
    interceptions = count_id_occurrences(pbp, ["interception_player_id"])
    pass_defended = count_id_occurrences(pbp, ["pass_defense_1_player_id", "pass_defense_2_player_id"])
    forced_fumbles = count_id_occurrences(pbp, ["forced_fumble_player_1_player_id", "forced_fumble_player_2_player_id"])
    fumble_recoveries = count_id_occurrences(pbp, ["fumble_recovery_1_player_id", "fumble_recovery_2_player_id"])
    safeties = count_id_occurrences(pbp, ["safety_player_id"])

    # Defensive/return touchdowns: scoring plays off an interception or fumble
    def_td_plays = pbp[(pbp["touchdown"] == 1) & ((pbp["interception"] == 1) | (pbp["fumble"] == 1)) & pbp["td_player_id"].notna()]
    def_tds = def_td_plays.groupby("td_player_id").size()

    all_ids = sorted(set().union(
        solo.index, assisted.index, tfl.index, qb_hits.index, full_sacks.index,
        half_sacks.index, interceptions.index, pass_defended.index,
        forced_fumbles.index, fumble_recoveries.index, safeties.index, def_tds.index,
    ))

    df = pd.DataFrame({"PlayerID": all_ids})
    df["SoloTackles"] = df["PlayerID"].map(solo).fillna(0).astype(int)
    df["AssistedTackles"] = df["PlayerID"].map(assisted).fillna(0).astype(int)
    df["TotalTackles"] = df["SoloTackles"] + df["AssistedTackles"]
    df["TacklesForLoss"] = df["PlayerID"].map(tfl).fillna(0).astype(int)
    df["Sacks"] = (df["PlayerID"].map(full_sacks).fillna(0) + df["PlayerID"].map(half_sacks).fillna(0) * 0.5)
    df["QBHits"] = df["PlayerID"].map(qb_hits).fillna(0).astype(int)
    df["Interceptions"] = df["PlayerID"].map(interceptions).fillna(0).astype(int)
    df["PassesDefended"] = df["PlayerID"].map(pass_defended).fillna(0).astype(int)
    df["ForcedFumbles"] = df["PlayerID"].map(forced_fumbles).fillna(0).astype(int)
    df["FumbleRecoveries"] = df["PlayerID"].map(fumble_recoveries).fillna(0).astype(int)
    df["DefensiveTouchdowns"] = df["PlayerID"].map(def_tds).fillna(0).astype(int)
    df["Safeties"] = df["PlayerID"].map(safeties).fillna(0).astype(int)

    final_cols = [
        "PlayerID", "SoloTackles", "AssistedTackles", "TotalTackles", "TacklesForLoss",
        "Sacks", "QBHits", "Interceptions", "PassesDefended", "ForcedFumbles",
        "FumbleRecoveries", "DefensiveTouchdowns", "Safeties",
    ]
    df = df[final_cols].sort_values("TotalTackles", ascending=False).reset_index(drop=True)
    df = tidy_numeric_columns(df, count_cols=[], rate_cols={"Sacks": 1})
    return df


def build_kicking_table(pbp: pd.DataFrame) -> pd.DataFrame:
    log("Building KickingStats table ...")

    fg_plays = pbp[pbp["field_goal_attempt"] == 1].copy()
    xp_plays = pbp[pbp["extra_point_attempt"] == 1].copy()

    fg = fg_plays.groupby("kicker_player_id").agg(
        FGAttempts=("field_goal_attempt", "sum"),
        FGMade=("field_goal_result", lambda s: (s == "made").sum()),
        LongestFG=("kick_distance", lambda s: fg_plays.loc[s.index, "kick_distance"].where(fg_plays.loc[s.index, "field_goal_result"] == "made").max()),
    )
    xp = xp_plays.groupby("kicker_player_id").agg(
        XPAttempts=("extra_point_attempt", "sum"),
        XPMade=("extra_point_result", lambda s: (s == "good").sum()),
    )

    df = fg.join(xp, how="outer").fillna(0).reset_index().rename(columns={"kicker_player_id": "PlayerID"})
    int_cols = ["FGAttempts", "FGMade", "XPAttempts", "XPMade"]
    df[int_cols] = df[int_cols].astype(int)

    df["FGPct"] = safe_divide(df["FGMade"], df["FGAttempts"]) * 100
    df["XPPct"] = safe_divide(df["XPMade"], df["XPAttempts"]) * 100
    df["PointsScored"] = df["FGMade"] * 3 + df["XPMade"]

    final_cols = [
        "PlayerID", "FGMade", "FGAttempts", "FGPct", "LongestFG",
        "XPMade", "XPAttempts", "XPPct", "PointsScored",
    ]
    df = df[final_cols].sort_values("PointsScored", ascending=False).reset_index(drop=True)
    df = tidy_numeric_columns(df, count_cols=["LongestFG"], rate_cols={"FGPct": 1, "XPPct": 1})
    return df


def build_punting_table(pbp: pd.DataFrame) -> pd.DataFrame:
    log("Building PuntingStats table ...")

    punts = pbp[pbp["punt_attempt"] == 1].copy()
    punts["net_yards"] = punts["kick_distance"] - punts["return_yards"].fillna(0)

    agg = punts.groupby("punter_player_id").agg(
        Punts=("punt_attempt", "sum"),
        PuntYards=("kick_distance", "sum"),
        LongestPunt=("kick_distance", "max"),
        Inside20=("punt_inside_twenty", "sum"),
        Touchbacks=("touchback", "sum"),
        FairCatches=("punt_fair_catch", "sum"),
        NetAverage=("net_yards", "mean"),
    )

    df = agg.reset_index().rename(columns={"punter_player_id": "PlayerID"})
    int_cols = ["Punts", "Inside20", "Touchbacks", "FairCatches"]
    df[int_cols] = df[int_cols].astype(int)
    df["Average"] = safe_divide(df["PuntYards"], df["Punts"]).round(2)
    df["NetAverage"] = df["NetAverage"].round(2)

    final_cols = [
        "PlayerID", "Punts", "PuntYards", "Average", "LongestPunt",
        "Inside20", "Touchbacks", "FairCatches", "NetAverage",
    ]
    df = df[final_cols].sort_values("Punts", ascending=False).reset_index(drop=True)
    df = tidy_numeric_columns(df, count_cols=["PuntYards", "LongestPunt"], rate_cols={"Average": 2, "NetAverage": 2})
    return df


def build_teams_table(games: pd.DataFrame, team_desc: pd.DataFrame) -> pd.DataFrame:
    log("Building Teams table ...")

    current_teams = sorted(set(games["home_team"]) | set(games["away_team"]))
    records = []
    for team in current_teams:
        home = games[games["home_team"] == team]
        away = games[games["away_team"] == team]

        wins = (home["home_score"] > home["away_score"]).sum() + (away["away_score"] > away["home_score"]).sum()
        losses = (home["home_score"] < home["away_score"]).sum() + (away["away_score"] < away["home_score"]).sum()
        ties = (home["home_score"] == home["away_score"]).sum() + (away["away_score"] == away["home_score"]).sum()
        points_for = home["home_score"].sum() + away["away_score"].sum()
        points_against = home["away_score"].sum() + away["home_score"].sum()

        records.append({
            "TeamID": team,
            "Team": team,
            "Wins": int(wins),
            "Losses": int(losses),
            "Ties": int(ties),
            "PointsFor": int(points_for),
            "PointsAgainst": int(points_against),
        })

    df = pd.DataFrame(records)

    desc_slim = team_desc[team_desc["team_abbr"].isin(current_teams)][
        ["team_abbr", "team_name", "team_conf", "team_division"]
    ].rename(columns={
        "team_abbr": "TeamID",
        "team_name": "Team",
        "team_conf": "Conference",
        "team_division": "Division",
    })

    df = df.drop(columns=["Team"]).merge(desc_slim, on="TeamID", how="left")

    final_cols = ["TeamID", "Team", "Conference", "Division", "Wins", "Losses", "Ties", "PointsFor", "PointsAgainst"]
    return df[final_cols].sort_values(["Conference", "Division", "Wins"], ascending=[True, True, False]).reset_index(drop=True)


def compute_pfr_games_started(pfr_frames: dict[str, pd.DataFrame], roster: pd.DataFrame) -> pd.Series:
    """Combine games-started (GS) across the PFR rushing/receiving/defense
    advanced-stat tables (the only ones that carry it) and map it from
    PFR's player id back onto nflverse's gsis_id via the roster crosswalk."""
    pfr_to_gsis = roster.dropna(subset=["pfr_id"]).drop_duplicates(subset="pfr_id").set_index("pfr_id")["gsis_id"]

    gs_series = []
    for category in ("rush", "rec", "def"):
        frame = pfr_frames.get(category)
        if frame is None or frame.empty or "gs" not in frame.columns:
            continue
        s = frame.dropna(subset=["pfr_id"]).groupby("pfr_id")["gs"].max()
        gs_series.append(s)

    if not gs_series:
        return pd.Series(dtype="int64")

    combined = pd.concat(gs_series, axis=1).max(axis=1)
    combined.index = combined.index.map(pfr_to_gsis)
    combined = combined[combined.index.notna()]
    combined = combined.groupby(level=0).max()
    combined.index.name = "PlayerID"
    return combined


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    start_time = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    log(f"Building {SEASON} NFL Player Statistics Database for Power BI")
    log("=" * 70)

    try:
        pbp = load_play_by_play()
        roster = load_roster()
        games = load_games()
        team_desc = load_team_desc()

        pfr_frames = {cat: load_pfr_advanced(cat) for cat in ("rush", "rec", "def")}
        pfr_games_started = compute_pfr_games_started(pfr_frames, roster)

        # Wire up the receiver -> pfr_id crosswalk used for the optional Drops column
        global pbp_receiver_to_pfr_id
        crosswalk = roster.dropna(subset=["pfr_id"]).drop_duplicates(subset="gsis_id").set_index("gsis_id")["pfr_id"]
        pbp_receiver_to_pfr_id = lambda _pbp: crosswalk  # noqa: E731

        players_df = build_players_table(pbp, roster, pfr_games_started)
        passing_df = build_passing_table(pbp, pfr_games_started)
        rushing_df = build_rushing_table(pbp)
        receiving_df = build_receiving_table(pbp, pfr_frames.get("rec", pd.DataFrame()))
        defense_df = build_defense_table(pbp)
        kicking_df = build_kicking_table(pbp)
        punting_df = build_punting_table(pbp)
        teams_df = build_teams_table(games, team_desc)

    except RuntimeError as exc:
        log(f"FATAL: {exc}")
        sys.exit(1)

    # Keep every stat table scoped to players who made it into the Players dimension
    valid_ids = set(players_df["PlayerID"])
    passing_df = passing_df[passing_df["PlayerID"].isin(valid_ids)].reset_index(drop=True)
    rushing_df = rushing_df[rushing_df["PlayerID"].isin(valid_ids)].reset_index(drop=True)
    receiving_df = receiving_df[receiving_df["PlayerID"].isin(valid_ids)].reset_index(drop=True)
    defense_df = defense_df[defense_df["PlayerID"].isin(valid_ids)].reset_index(drop=True)
    kicking_df = kicking_df[kicking_df["PlayerID"].isin(valid_ids)].reset_index(drop=True)
    punting_df = punting_df[punting_df["PlayerID"].isin(valid_ids)].reset_index(drop=True)

    log("-" * 70)
    log("Exporting CSV files ...")

    row_counts = {
        "Players.csv": export_csv(players_df, "Players.csv"),
        "PassingStats.csv": export_csv(passing_df, "PassingStats.csv"),
        "RushingStats.csv": export_csv(rushing_df, "RushingStats.csv"),
        "ReceivingStats.csv": export_csv(receiving_df, "ReceivingStats.csv"),
        "DefenseStats.csv": export_csv(defense_df, "DefenseStats.csv"),
        "KickingStats.csv": export_csv(kicking_df, "KickingStats.csv"),
        "PuntingStats.csv": export_csv(punting_df, "PuntingStats.csv"),
        "Teams.csv": export_csv(teams_df, "Teams.csv"),
    }

    elapsed = time.time() - start_time

    log("=" * 70)
    log("SUMMARY")
    log(f"  Players exported : {len(players_df):,}")
    log(f"  Teams exported   : {len(teams_df):,}")
    for name, count in row_counts.items():
        log(f"  {name:<20} {count:,} rows")
    log(f"  Total runtime    : {elapsed:.1f} seconds")
    log("Done. CSV files are ready in the data/ folder for Power BI import.")


if __name__ == "__main__":
    main()
