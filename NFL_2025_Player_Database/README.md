# NFL 2025 Player Statistics Database

A self-contained Python project that downloads, cleans, and exports **2025 NFL
regular-season player statistics** into a set of CSV files structured as a
star schema, ready to load straight into Microsoft Power BI.

No statistics are hand-typed anywhere in this project — every number is
pulled at runtime from [nflverse](https://github.com/nflverse/nflverse-data),
the public, open-source NFL data project.

## Requirements

- Python 3.11+
- Internet access (data is downloaded fresh on every run)

## Quick start

```bash
pip install -r requirements.txt
python main.py
```

Running `main.py` downloads the source data, builds every table, and writes
CSV files into the `data/` folder. A progress log and a final summary
(players exported, teams exported, rows written per file, total runtime)
print to the console.

## Where the data comes from

All data is sourced live from nflverse's public GitHub releases and related
public endpoints — nothing is bundled or hardcoded:

| Source | Used for |
|---|---|
| `nflverse-data` release `pbp` (play-by-play, 2025) | Passing, rushing, receiving, defense, kicking, and punting stats — aggregated directly from every regular-season play |
| `nflverse-data` release `rosters` (seasonal roster, 2025) | Player biographical info: name, team, position, jersey number, birth date, height, weight, college |
| `nflverse-data` release `schedules` (`games.parquet`) | Final scores for every 2025 regular-season game, used to build team win/loss records |
| `nflverse-data` release `pfr_advstats` (Pro-Football-Reference advanced stats) | Supplemental games-started counts, and optional receiving "Drops" |
| `nflfastR-data` team reference CSV | Team conference/division metadata |

Because the core stat tables are aggregated directly from play-by-play data
(rather than a pre-built nflverse summary file), the pipeline works for any
season as soon as nflverse publishes that season's play-by-play file — just
change the `SEASON` constant at the top of `main.py`.

## Project structure

```
NFL_2025_Player_Database/
├── main.py            # the entire pipeline: download -> clean -> aggregate -> export
├── requirements.txt
├── README.md
└── data/               # generated on each run
    ├── Players.csv
    ├── PassingStats.csv
    ├── RushingStats.csv
    ├── ReceivingStats.csv
    ├── DefenseStats.csv
    ├── KickingStats.csv
    ├── PuntingStats.csv
    └── Teams.csv
```

## Star schema for Power BI

`Players.csv` is the central dimension table. Every stat table has one row
per player per season and joins back to it on **PlayerID** (the nflverse
`gsis_id`, e.g. `00-0033106`) — a classic star schema:

```
                 PassingStats
                      |
   ReceivingStats  Players.csv  RushingStats
                      |
     KickingStats — DefenseStats — PuntingStats
```

`Teams.csv` is a second, independent dimension table (`TeamID` = team
abbreviation, e.g. `PHI`); `Players.Team` can be related to `Teams.TeamID` to
roll player stats up by team.

**To wire this up in Power BI:** open Power BI Desktop, *Get Data → Text/CSV*
for each file in `data/`, then in *Model view* create a one-to-many
relationship from `Players[PlayerID]` to each stat table's `PlayerID`
column, and from `Teams[TeamID]` to `Players[Team]`.

## Data notes & known limitations

- **Player population**: a player is included in `Players.csv` if he
  recorded at least one individually-tracked statistical play (pass attempt,
  carry, target, tackle, sack, kick, punt, etc.) in a 2025 regular-season
  game. Offensive linemen and any player with zero individually-tracked
  plays are not included as standalone rows, because nflverse does not
  publish individual snap/play data for them.
- **GamesStarted**: sourced from Pro-Football-Reference's advanced season
  tables (the only public source that tracks starts). A small number of
  players who have no PFR advanced-stat record default to 0.
- **Punting stats** are not published by nflverse as a pre-built table, so
  they are derived from punt plays in the play-by-play data. `NetAverage` is
  approximated as gross punt distance minus return yardage per punt.
- **Sacks** in `DefenseStats.csv` can be fractional (e.g. `0.5`) to reflect
  split sacks credited to two defenders on the same play, matching standard
  NFL statistical convention.
- Missing values (e.g. a player with no recorded college, or a rate stat
  with a zero denominator) are left blank rather than defaulting to a
  misleading 0.

## Customizing

To pull a different season, change `SEASON = 2025` near the top of
`main.py`. Nothing else needs to change — all URLs are built from that
constant.
