from collections import defaultdict

import numpy as np
import pandas as pd

from common import load, mmss, strength_array
from config import CLEAN_DIR, RAW_DIR, SEASONS

POS_GROUP = {"C": "F", "L": "F", "R": "F", "D": "D", "G": "G"}
POS_ORDER = {"L": 0, "C": 1, "R": 2, "D": 3}
MIN_PAIR_SEC = 60     # drop linemate pairs with < 1 min together in a game
MIN_COMBO_SEC = 30    # drop line combos with < 30 s together in a game


def on_ice_matrices(g, box, shifts):
    """For each team: player ids, positions, and a bool matrix [player, second] = on ice."""
    home, away = box["homeTeam"]["abbrev"], box["awayTeam"]["abbrev"]
    pos, team_of = {}, {}
    for side, team in (("homeTeam", home), ("awayTeam", away)):
        for group in ("forwards", "defense", "goalies"):
            for p in box["playerByGameStats"][side].get(group, []):
                pos[p["playerId"]] = p.get("position")
                team_of[p["playerId"]] = team

    max_period = 4 if g["game_type"] == 2 else 99    # regular season: drop the shootout
    intervals = defaultdict(list)
    length = 0
    for s in shifts.get("data", []):
        # typeCode 517 = a real shift; 505 rows are goal markers with no duration
        if s.get("typeCode") != 517 or not s.get("duration"):
            continue
        pid, period = s.get("playerId"), s.get("period", 0)
        if pid not in pos or period > max_period:
            continue
        start = (period - 1) * 1200 + mmss(s["startTime"])
        end = (period - 1) * 1200 + mmss(s["endTime"])
        if end > start:
            intervals[pid].append((start, end))
            length = max(length, end)
    if length == 0:
        return None

    teams = {}
    for team in (home, away):
        ids = [p for p in intervals if team_of[p] == team]
        mat = np.zeros((len(ids), length), dtype=bool)
        for i, p in enumerate(ids):
            for a, b in intervals[p]:
                mat[i, a:b] = True      # [start, end): the end second belongs to the next shift
        group = np.array([POS_GROUP.get(pos[p], "F") for p in ids])
        teams[team] = dict(ids=np.array(ids), pos=[pos[p] for p in ids], mat=mat, group=group,
                           skaters=mat[group != "G"].sum(0), goalie=mat[group == "G"].any(0))
    return home, away, teams, length


def combo_stats(sub_mat, ids, pos_list, valid, idx_for, idx_against, goals_for, goals_against):
    """Group seconds by which exact set of players was on ice (encoded as a bitmask)."""
    n = sub_mat.shape[0]
    if n == 0 or n > 62:
        return []
    weights = (1 << np.arange(n)).astype(np.int64)
    codes = weights @ sub_mat.astype(np.int64)          # one integer per second
    codes = np.where(valid, codes, -1)

    toi = pd.Series(codes[codes >= 0]).value_counts()
    def count(idx):
        c = codes[idx]
        return pd.Series(c[c >= 0]).value_counts()
    cf, ca = count(idx_for), count(idx_against)
    gf, ga = count(goals_for), count(goals_against)

    rows = []
    for code, sec in toi.items():
        if sec < MIN_COMBO_SEC:
            continue
        members = [i for i in range(n) if code >> i & 1]
        members.sort(key=lambda i: (POS_ORDER.get(pos_list[i], 9), ids[i]))
        rows.append(dict(members=[int(ids[i]) for i in members], toi=int(sec),
                         cf=int(cf.get(code, 0)), ca=int(ca.get(code, 0)),
                         gf=int(gf.get(code, 0)), ga=int(ga.get(code, 0))))
    return rows


def process_game(g, box, shifts, shots, names, out):
    parsed = on_ice_matrices(g, box, shifts)
    if parsed is None:
        return False
    home, away, teams, length = parsed
    gid, season = g["game_id"], g["season"]

    for team, opp in ((home, away), (away, home)):
        T, O = teams[team], teams[opp]
        labels = strength_array(T["skaters"], O["skaters"], T["goalie"], O["goalie"])
        is_5v5 = labels == "5v5"

        # 📝 Attribute each event to who was on ice the second BEFORE it, because a
        # shift that ends on a goal is recorded as ending at that exact second.
        def event_idx(frame):
            idx = np.clip(frame["game_sec"].to_numpy(dtype=np.int64) - 1, 0, length - 1)
            return idx[is_5v5[idx]]
        idx_for = event_idx(shots[shots["team"] == team])
        idx_against = event_idx(shots[shots["team"] == opp])
        g_for = event_idx(shots[(shots["team"] == team) & (shots["is_goal"] == 1)])
        g_against = event_idx(shots[(shots["team"] == opp) & (shots["is_goal"] == 1)])

        sk = T["group"] != "G"
        ids, mat = T["ids"][sk], T["mat"][sk]
        pos_list = [p for p, keep in zip(T["pos"], sk) if keep]
        base = dict(game_id=gid, season=season, team=team)

        # 1. ice time by strength
        for i, pid in enumerate(ids):
            for label in ("5v5", "EV", "PP", "SH", "EA", "EN"):
                sec = int((mat[i] & (labels == label)).sum())
                if sec:
                    out["toi_strength"].append(dict(base, player_id=int(pid), strength=label, toi_sec=sec))

        # 2. per-player on-ice at 5v5
        on5 = mat & is_5v5
        cf, ca = mat[:, idx_for].sum(1), mat[:, idx_against].sum(1)
        gf, ga = mat[:, g_for].sum(1), mat[:, g_against].sum(1)
        for i, pid in enumerate(ids):
            out["onice"].append(dict(base, player_id=int(pid), toi_sec=int(on5[i].sum()),
                                     cf=int(cf[i]), ca=int(ca[i]), gf=int(gf[i]), ga=int(ga[i])))

        # 3. linemates: matrix products give every pair at once
        #    shared[i, j] = seconds both i and j were on ice at 5v5
        m = on5.astype(np.int32)
        shared = m @ m.T
        def pair(idx):
            e = mat[:, idx].astype(np.int32)
            return e @ e.T
        p_cf, p_ca, p_gf, p_ga = pair(idx_for), pair(idx_against), pair(g_for), pair(g_against)
        for i in range(len(ids)):
            for j in range(len(ids)):
                if i != j and shared[i, j] >= MIN_PAIR_SEC:
                    out["linemate"].append(dict(
                        base, player_id=int(ids[i]), mate_id=int(ids[j]),
                        mate_name=names.get(int(ids[j]), str(ids[j])),
                        toi_5v5_sec=int(shared[i, j]), cf=int(p_cf[i, j]), ca=int(p_ca[i, j]),
                        gf=int(p_gf[i, j]), ga=int(p_ga[i, j])))

        # 4. line combos: exactly 3 forwards / 2 defence on ice at 5v5
        for unit, grp, size in (("F", "F", 3), ("D", "D", 2)):
            keep = T["group"] == grp
            sub = T["mat"][keep]
            valid = is_5v5 & (sub.sum(0) == size)
            sub_pos = [p for p, k in zip(T["pos"], keep) if k]
            for c in combo_stats(sub, T["ids"][keep], sub_pos, valid, idx_for, idx_against, g_for, g_against):
                members = c["members"] + [None] * (3 - len(c["members"]))
                member_names = [names.get(p, str(p)) if p else None for p in members]
                out["combo"].append(dict(
                    base, unit_type=unit,
                    combo_key="-".join(str(p) for p in c["members"]),
                    combo_label=" – ".join(n for n in member_names if n),
                    p1_id=members[0], p2_id=members[1], p3_id=members[2],
                    p1_name=member_names[0], p2_name=member_names[1], p3_name=member_names[2],
                    toi_5v5_sec=c["toi"], cf=c["cf"], ca=c["ca"], gf=c["gf"], ga=c["ga"]))
    return True


def main() -> None:
    shot_path, player_path = CLEAN_DIR / "fact_shot.csv", CLEAN_DIR / "dim_player.csv"
    if not shot_path.exists() or not player_path.exists():
        print("Run 02_build_tables.py first.")
        return
    shots_by_game = {gid: df for gid, df in
                     pd.read_csv(shot_path, usecols=["game_id", "team", "game_sec", "is_goal"]).groupby("game_id")}
    players = pd.read_csv(player_path, usecols=["player_id", "last_name"])
    names = dict(zip(players["player_id"], players["last_name"]))
    empty_shots = pd.DataFrame(columns=["game_id", "team", "game_sec", "is_goal"])

    out = defaultdict(list)
    for season in SEASONS:
        index = RAW_DIR / season / "games_index.json"
        if not index.exists():
            continue
        games = load(index)
        done = missing = 0
        for g in games:
            folder = RAW_DIR / season / "games" / str(g["game_id"])
            if not (folder / "shifts.json").exists() or not (folder / "boxscore.json").exists():
                missing += 1
                continue
            ok = process_game(g, load(folder / "boxscore.json"), load(folder / "shifts.json"),
                              shots_by_game.get(g["game_id"], empty_shots), names, out)
            done += ok
            missing += not ok
        print(f"Season {season}: {done} games processed, {missing} without shift data")

    print("Writing data/clean/")
    for key, name in (("toi_strength", "fact_skater_toi_strength"), ("onice", "fact_skater_onice_5v5"),
                      ("linemate", "fact_linemate"), ("combo", "fact_line_combo")):
        df = pd.DataFrame(out[key])
        for col in ("p1_id", "p2_id", "p3_id"):
            if col in df.columns:
                df[col] = df[col].astype("Int64")
        df.to_csv(CLEAN_DIR / f"{name}.csv", index=False, encoding="utf-8")
        print(f"  {name:<26} {len(df):>9,} rows")


if __name__ == "__main__":
    main()
