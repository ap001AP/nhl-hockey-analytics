import argparse
import json
from datetime import date, timedelta

from config import API_STATS, API_WEB, FINAL_STATES, GAME_TYPES, RAW_DIR, SEASONS
from nhl_client import NotFound, get_json


def is_ongoing(season: str) -> bool:
    """True until July 1 after the season's end year (covers the playoffs too)."""
    return date.today() < date(int(season[4:]), 7, 1)


def standings_url(season: str) -> tuple[str, str]:
    """(url, cache file name) for a standings snapshot inside the season."""
    if is_ongoing(season):
        return f"{API_WEB}/standings/now", "standings_now.json"
    mid = f"{season[:4]}-12-01"
    return f"{API_WEB}/standings/{mid}", f"standings_{mid}.json"


def team_list(season: str) -> list[str]:
    """The 32 team abbreviations for a season, taken from a standings snapshot."""
    url, name = standings_url(season)
    data = get_json(url, RAW_DIR / season / name, refresh=is_ongoing(season))
    teams = sorted(row["teamAbbrev"]["default"] for row in data.get("standings", []))
    if teams:
        return teams
    # Early in a season the snapshot can come back empty. Teams don't change
    # mid-season, so fall back to the previous season's list.
    previous = f"{int(season[:4]) - 1}{season[:4]}"
    print(f"  no standings yet for {season}; using {previous}'s team list")
    return team_list(previous)


def season_games(season: str, teams: list[str], refresh: bool) -> list[dict]:
    """Every finished game of the chosen types, de-duplicated across the 32 schedules."""
    games = {}
    for abbr in teams:
        sched = get_json(
            f"{API_WEB}/club-schedule-season/{abbr}/{season}",
            RAW_DIR / season / "schedules" / f"{abbr}.json",
            refresh=refresh,
        )
        for g in sched.get("games", []):
            if g["gameType"] not in GAME_TYPES or g.get("gameState") not in FINAL_STATES:
                continue
            games[g["id"]] = {
                "game_id": g["id"],
                "season": str(g["season"]),
                "game_type": g["gameType"],
                "game_date": g["gameDate"],
                "home": g["homeTeam"]["abbrev"],
                "away": g["awayTeam"]["abbrev"],
                "home_score": g["homeTeam"].get("score"),
                "away_score": g["awayTeam"].get("score"),
                "last_period_type": (g.get("gameOutcome") or {}).get("lastPeriodType", "REG"),
            }
    return sorted(games.values(), key=lambda g: (g["game_date"], g["game_id"]))


def fetch_game(season: str, game_id: int) -> None:
    folder = RAW_DIR / season / "games" / str(game_id)
    get_json(f"{API_WEB}/gamecenter/{game_id}/boxscore", folder / "boxscore.json")
    get_json(f"{API_WEB}/gamecenter/{game_id}/play-by-play", folder / "play-by-play.json")
    try:
        get_json(f"{API_STATS}/shiftcharts?cayenneExp=gameId={game_id}", folder / "shifts.json")
    except NotFound:
        print(f"    no shift chart for {game_id}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", help="only fetch games involving this team, e.g. EDM")
    ap.add_argument("--limit", type=int, help="only fetch the first N games (testing)")
    ap.add_argument("--refresh", action="store_true", help="re-download schedules, rosters, standings")
    args = ap.parse_args()

    for season in SEASONS:
        # A season still in progress always re-downloads schedules, rosters and
        # standings, so new games show up. Finished games stay cached either way.
        refresh = args.refresh or is_ongoing(season)
        print(f"== Season {season}{' (in progress)' if is_ongoing(season) else ''} ==")
        teams = team_list(season)
        games = season_games(season, teams, refresh)
        print(f"  {len(games)} finished games across {len(teams)} teams")

        # Standings as of the day after the last regular-season game played
        # (or today, for a season still in progress).
        reg_dates = [g["game_date"] for g in games if g["game_type"] == 2]
        if reg_dates:
            if is_ongoing(season):
                url = f"{API_WEB}/standings/now"
            else:
                day = (date.fromisoformat(max(reg_dates)) + timedelta(days=1)).isoformat()
                url = f"{API_WEB}/standings/{day}"
            get_json(url, RAW_DIR / season / "standings_final.json", refresh=refresh)

        # Rosters give bio details and headshot URLs.
        for abbr in teams:
            try:
                get_json(f"{API_WEB}/roster/{abbr}/{season}",
                         RAW_DIR / season / "rosters" / f"{abbr}.json", refresh=refresh)
            except NotFound:
                print(f"    no roster for {abbr}")

        if args.team:
            games = [g for g in games if args.team.upper() in (g["home"], g["away"])]
        if args.limit:
            games = games[: args.limit]

        index_path = RAW_DIR / season / "games_index.json"
        index_path.write_text(json.dumps(games, indent=1), encoding="utf-8")

        failed = []
        for i, g in enumerate(games, 1):
            try:
                fetch_game(season, g["game_id"])
            except Exception as err:   # keep going; report at the end
                failed.append((g["game_id"], str(err)))
            if i % 25 == 0 or i == len(games):
                print(f"  {i}/{len(games)} games downloaded")

        if failed:
            print(f"  {len(failed)} games failed (rerun the script to retry them):")
            for gid, err in failed[:10]:
                print(f"    {gid}: {err}")


if __name__ == "__main__":
    main()