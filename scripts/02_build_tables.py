import math
from collections import defaultdict
from datetime import date

import pandas as pd

from common import is_power_play, load, mmss, parse_situation, strength_label, text
from config import CLEAN_DIR, RAW_DIR, SEASONS

SHOT_EVENTS = {"goal", "shot-on-goal", "missed-shot", "blocked-shot"}
POS_GROUP = {"C": "F", "L": "F", "R": "F", "D": "D", "G": "G"}
NET_X = 89   # goal line is 89 ft from centre ice


# ---------------------------------------------------------------- boxscore
def parse_boxscore(g, box, out, seen_players):
    """Per-player game lines for skaters and goalies. Returns team totals."""
    gid = g["game_id"]
    home, away = box["homeTeam"]["abbrev"], box["awayTeam"]["abbrev"]
    totals = {}
    for side, team, opp, is_home in (("homeTeam", home, away, 1), ("awayTeam", away, home, 0)):
        stats = box["playerByGameStats"][side]
        base = dict(game_id=gid, season=g["season"], game_date=g["game_date"],
                    team=team, opponent=opp, is_home=is_home)
        hits = blocks = 0

        for group in ("forwards", "defense"):
            for p in stats.get(group, []):
                row = dict(
                    base,
                    player_id=p["playerId"],
                    position=p.get("position"),
                    goals=p.get("goals", 0),
                    assists=p.get("assists", 0),
                    points=p.get("points", 0),
                    plus_minus=p.get("plusMinus", 0),
                    pim=p.get("pim", 0),
                    hits=p.get("hits", 0),
                    pp_goals=p.get("powerPlayGoals", 0),
                    shots=p.get("sog", 0),
                    blocks=p.get("blockedShots", 0),
                    giveaways=p.get("giveaways", 0),
                    takeaways=p.get("takeaways", 0),
                    shifts=p.get("shifts", 0),
                    faceoff_pct=p.get("faceoffWinningPctg"),
                    toi_sec=mmss(p.get("toi")),
                )
                out["skater_game"].append(row)
                hits += row["hits"]
                blocks += row["blocks"]
                remember(seen_players, p, team, g)

        for p in stats.get("goalies", []):
            remember(seen_players, p, team, g)
            toi = mmss(p.get("toi"))
            if toi == 0:          # dressed as backup, never played
                continue
            out["goalie_game"].append(dict(
                base,
                player_id=p["playerId"],
                starter=int(bool(p.get("starter"))),
                decision=p.get("decision"),
                shots_against=p.get("shotsAgainst", 0),
                saves=p.get("saves", 0),
                goals_against=p.get("goalsAgainst", 0),
                toi_sec=toi,
            ))

        totals[team] = dict(sog=box[side].get("sog"), hits=hits, blocks=blocks)
    return totals


def remember(seen, p, team, g):
    """Keep the most recent boxscore identity of every player (fallback for dim_player)."""
    pid = p["playerId"]
    if pid not in seen or g["game_date"] >= seen[pid]["date"]:
        seen[pid] = dict(date=g["game_date"], season=g["season"], short_name=text(p.get("name")),
                         position=p.get("position"), sweater=p.get("sweaterNumber"), team=team)


# ---------------------------------------------------------------- play-by-play
def parse_play_by_play(g, box, pbp, out):
    """Shot-level events plus power-play goals / opportunities per team."""
    gid = g["game_id"]
    home, away = box["homeTeam"]["abbrev"], box["awayTeam"]["abbrev"]
    id_to_abbr = {box["homeTeam"]["id"]: home, box["awayTeam"]["id"]: away}
    player_team = {
        p["playerId"]: team
        for side, team in (("homeTeam", home), ("awayTeam", away))
        for group in ("forwards", "defense", "goalies")
        for p in box["playerByGameStats"][side].get(group, [])
    }

    pp_opps = {home: 0, away: 0}
    pp_goals = {home: 0, away: 0}
    in_pp = {home: False, away: False}

    for play in sorted(pbp.get("plays", []), key=lambda p: p.get("sortOrder", 0)):
        period_info = play.get("periodDescriptor", {})
        if period_info.get("periodType") == "SO":
            continue
        code = play.get("situationCode")

        # A power-play opportunity starts each time a team goes from "not on the
        # PP" to "on the PP". (A 5v4 -> 4v4 -> 5v4 sequence counts twice: a
        # small, known approximation.)
        for team, is_home in ((home, True), (away, False)):
            now = is_power_play(parse_situation(code, is_home))
            if now and not in_pp[team]:
                pp_opps[team] += 1
            in_pp[team] = now

        kind = play.get("typeDescKey")
        if kind not in SHOT_EVENTS:
            continue
        d = play.get("details") or {}
        shooter = d.get("scoringPlayerId") if kind == "goal" else d.get("shootingPlayerId")

        # Shooting team comes from the shooter. Fallback: eventOwnerTeamId, which on
        # a blocked shot is the BLOCKING team, so flip it.
        team = player_team.get(shooter)
        if team is None:
            owner = id_to_abbr.get(d.get("eventOwnerTeamId"))
            if owner is None:
                continue
            team = (away if owner == home else home) if kind == "blocked-shot" else owner
        is_home = team == home
        opp = away if is_home else home
        strength = strength_label(parse_situation(code, is_home))
        is_goal = int(kind == "goal")
        if is_goal and strength == "PP":
            pp_goals[team] += 1

        # Normalise coordinates so every team attacks to the right (+x).
        x, y = d.get("xCoord"), d.get("yCoord")
        x_norm = y_norm = dist = angle = None
        if x is not None and y is not None:
            side = play.get("homeTeamDefendingSide")
            if side in ("left", "right"):
                attacks_right = (side == "left") == is_home
            else:
                attacks_right = x >= 0
            x_norm, y_norm = (x, y) if attacks_right else (-x, -y)
            dist = round(math.hypot(NET_X - x_norm, y_norm), 1)
            angle = round(math.degrees(math.atan2(abs(y_norm), NET_X - x_norm)), 1)

        period = period_info.get("number", 0)
        out["shot"].append(dict(
            shot_key=f"{gid}-{play.get('eventId')}",
            game_id=gid, season=g["season"], game_date=g["game_date"],
            event_id=play.get("eventId"),
            period=period,
            period_label={1: "1st", 2: "2nd", 3: "3rd"}.get(period, "OT"),
            game_sec=(period - 1) * 1200 + mmss(play.get("timeInPeriod")),
            team=team, opponent=opp, is_home=int(is_home),
            shooter_id=shooter, goalie_id=d.get("goalieInNetId"),
            event_type=kind,
            shot_type=d.get("shotType"),
            is_goal=is_goal,
            on_target=int(kind in ("goal", "shot-on-goal")),
            strength=strength,
            x=x, y=y, x_norm=x_norm, y_norm=y_norm,
            distance_ft=dist, angle_deg=angle,
            assist1_id=d.get("assist1PlayerId"), assist2_id=d.get("assist2PlayerId"),
            zone=d.get("zoneCode"),
        ))

    return pp_goals, pp_opps


def team_game_rows(g, totals, pp_goals, pp_opps, out):
    home, away = g["home"], g["away"]
    for team, opp, is_home, gf, ga in ((home, away, 1, g["home_score"], g["away_score"]),
                                       (away, home, 0, g["away_score"], g["home_score"])):
        extra_time = g["last_period_type"] in ("OT", "SO") and g["game_type"] == 2
        result = "W" if gf > ga else ("OTL" if extra_time else "L")
        out["team_game"].append(dict(
            game_id=g["game_id"], season=g["season"], game_date=g["game_date"],
            game_type=g["game_type"], team=team, opponent=opp, is_home=is_home,
            venue="Home" if is_home else "Away",
            goals_for=gf, goals_against=ga,
            shots_for=totals[team]["sog"], shots_against=totals[opp]["sog"],
            hits=totals[team]["hits"], blocks=totals[team]["blocks"],
            pp_goals=pp_goals[team], pp_opps=pp_opps[team],
            pp_goals_against=pp_goals[opp], times_shorthanded=pp_opps[opp],
            result=result, points={"W": 2, "OTL": 1, "L": 0}[result],
            decided_in=g["last_period_type"],
        ))


# ---------------------------------------------------------------- dimensions
def build_dim_player(seen):
    today = date.today()
    roster_rows = {}
    for season in sorted(SEASONS):   # later seasons overwrite earlier ones
        for f in sorted((RAW_DIR / season / "rosters").glob("*.json")):
            team = f.stem
            data = load(f)
            for group in ("forwards", "defensemen", "goalies"):
                for p in data.get(group, []):
                    roster_rows[p["id"]] = dict(
                        first_name=text(p.get("firstName")), last_name=text(p.get("lastName")),
                        sweater_number=p.get("sweaterNumber"), position=p.get("positionCode"),
                        shoots=p.get("shootsCatches"), height_in=p.get("heightInInches"),
                        weight_lb=p.get("weightInPounds"), birth_date=p.get("birthDate"),
                        birth_country=p.get("birthCountry"), headshot_url=p.get("headshot"),
                        roster_team=team,
                    )

    rows = []
    for pid, s in seen.items():
        r = roster_rows.get(pid, {})
        short = s["short_name"] or ""
        last = r.get("last_name") or short.split(". ", 1)[-1]
        first = r.get("first_name") or ""
        position = r.get("position") or s["position"]
        height = r.get("height_in")
        birth = r.get("birth_date")
        age = None
        if birth:
            b = date.fromisoformat(birth)
            age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
        full_name = f"{first} {last}".strip() if first else short
        rows.append(dict(
            player_id=pid,
            full_name=full_name,
            # unique label for dropdowns: two players can share a name (e.g. Sebastian Aho)
            player_label=f"{full_name} ({s['team']}, #{r.get('sweater_number') or s['sweater']})",
            first_name=first or None, last_name=last, short_name=short,
            position=position, position_group=POS_GROUP.get(position, "F"),
            sweater_number=r.get("sweater_number") or s["sweater"],
            shoots=r.get("shoots"),
            height_in=height,
            height_label=f"{height // 12}'{height % 12}\"" if height else None,
            weight_lb=r.get("weight_lb"),
            birth_date=birth, age=age, birth_country=r.get("birth_country"),
            last_team=s["team"],
            headshot_url=r.get("headshot_url")
            or f"https://assets.nhle.com/mugs/nhl/{s['season']}/{s['team']}/{pid}.png",
        ))
    return pd.DataFrame(rows).sort_values("player_id")


def build_standings_and_teams():
    stand_rows, team_rows = [], {}
    for season in sorted(SEASONS):
        path = RAW_DIR / season / "standings_final.json"
        if not path.exists():
            continue
        for s in load(path)["standings"]:
            abbr = text(s["teamAbbrev"])
            stand_rows.append(dict(
                season=season, team_abbrev=abbr, team_name=text(s.get("teamName")),
                conference=s.get("conferenceName"), division=s.get("divisionName"),
                gp=s.get("gamesPlayed"), wins=s.get("wins"), losses=s.get("losses"),
                ot_losses=s.get("otLosses"), points=s.get("points"),
                point_pct=s.get("pointPctg"), goals_for=s.get("goalFor"),
                goals_against=s.get("goalAgainst"), goal_diff=s.get("goalDifferential"),
                league_rank=s.get("leagueSequence"), division_rank=s.get("divisionSequence"),
                last10=f"{s.get('l10Wins', 0)}-{s.get('l10Losses', 0)}-{s.get('l10OtLosses', 0)}",
                streak=f"{s.get('streakCode', '')}{s.get('streakCount', '')}",
            ))
            team_rows[abbr] = dict(   # latest season wins: current name / division
                team_abbrev=abbr, team_name=text(s.get("teamName")),
                common_name=text(s.get("teamCommonName")),
                conference=s.get("conferenceName"), division=s.get("divisionName"),
                logo_url=s.get("teamLogo"),
            )
    return pd.DataFrame(stand_rows), pd.DataFrame(team_rows.values())


# ---------------------------------------------------------------- main
INT_COLS = ["player_id", "shooter_id", "goalie_id", "assist1_id", "assist2_id", "event_id",
            "sweater_number", "height_in", "weight_lb", "age"]


def save(df: pd.DataFrame, name: str) -> None:
    for col in INT_COLS:
        if col in df.columns:
            df[col] = df[col].astype("Int64")   # keep ids as whole numbers, not 8477409.0
    df.to_csv(CLEAN_DIR / f"{name}.csv", index=False, encoding="utf-8")
    print(f"  {name:<18} {len(df):>8,} rows")


def main() -> None:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    out = defaultdict(list)
    seen_players = {}
    all_games = []

    for season in SEASONS:
        index = RAW_DIR / season / "games_index.json"
        if not index.exists():
            print(f"No {index} - run 01_fetch.py first.")
            continue
        games = load(index)
        print(f"Season {season}: parsing {len(games)} games")
        for g in games:
            folder = RAW_DIR / season / "games" / str(g["game_id"])
            box_path, pbp_path = folder / "boxscore.json", folder / "play-by-play.json"
            if not (box_path.exists() and pbp_path.exists()):
                print(f"  skipping {g['game_id']} (not downloaded)")
                continue
            box, pbp = load(box_path), load(pbp_path)
            totals = parse_boxscore(g, box, out, seen_players)
            pp_goals, pp_opps = parse_play_by_play(g, box, pbp, out)
            team_game_rows(g, totals, pp_goals, pp_opps, out)
            all_games.append(g)

    if not all_games:
        return

    team_game = pd.DataFrame(out["team_game"]).sort_values(["season", "team", "game_date", "game_id"])
    team_game["team_game_number"] = team_game.groupby(["season", "team"]).cumcount() + 1
    game_no = team_game[["game_id", "team", "team_game_number"]]

    skater = pd.DataFrame(out["skater_game"]).merge(game_no, on=["game_id", "team"], how="left")
    skater = skater.sort_values(["season", "player_id", "game_date", "game_id"])
    skater["player_game_number"] = skater.groupby(["season", "player_id"]).cumcount() + 1

    goalie = pd.DataFrame(out["goalie_game"]).merge(game_no, on=["game_id", "team"], how="left")
    shot = pd.DataFrame(out["shot"]).merge(game_no, on=["game_id", "team"], how="left")

    dim_game = pd.DataFrame(all_games).rename(columns={"last_period_type": "decided_in"})
    dim_game["matchup"] = dim_game["away"] + " @ " + dim_game["home"]
    dim_game["month"] = pd.to_datetime(dim_game["game_date"]).dt.strftime("%Y-%m")

    dim_season = pd.DataFrame({"season": SEASONS})
    dim_season["season_label"] = dim_season["season"].str[:4] + "-" + dim_season["season"].str[6:]

    standings, dim_team = build_standings_and_teams()

    print("Writing data/clean/")
    save(dim_season, "dim_season")
    save(dim_team, "dim_team")
    save(build_dim_player(seen_players), "dim_player")
    save(dim_game, "dim_game")
    save(standings, "standings")
    save(team_game, "fact_team_game")
    save(skater, "fact_skater_game")
    save(goalie, "fact_goalie_game")
    save(shot, "fact_shot")


if __name__ == "__main__":
    main()
