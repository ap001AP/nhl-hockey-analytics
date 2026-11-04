"""Small helpers shared by the build scripts."""
import json
from pathlib import Path

import numpy as np


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def text(value):
    """The API wraps names as {"default": "...", "fr": "..."}; unwrap them."""
    return value.get("default") if isinstance(value, dict) else value


def mmss(value) -> int:
    """'18:47' -> 1127 seconds."""
    if not value:
        return 0
    minutes, seconds = value.split(":")
    return int(minutes) * 60 + int(seconds)


def parse_situation(code, is_home):
    """situationCode '1451' = away goalie, away skaters, home skaters, home goalie.

    Returns (team_skaters, opp_skaters, team_goalie, opp_goalie) from one team's
    point of view, or None if the code is missing.
    """
    if not code or len(code) != 4 or not code.isdigit():
        return None
    ag, ask, hsk, hg = (int(c) for c in code)
    return (hsk, ask, hg, ag) if is_home else (ask, hsk, ag, hg)


def strength_label(sit) -> str:
    """Strength from the shooting / owning team's point of view.

    Order matters: an empty opposing net wins over everything else.
    """
    if sit is None:
        return "UNK"
    t_sk, o_sk, t_g, o_g = sit
    if o_g == 0:
        return "EN"     # opponent's net is empty
    if t_g == 0:
        return "EA"     # own goalie pulled for an extra attacker
    if t_sk == o_sk:
        return "5v5" if t_sk == 5 else "EV"   # 4v4 / 3v3 count as other even strength
    return "PP" if t_sk > o_sk else "SH"


def strength_array(t_sk, o_sk, t_g, o_g):
    """Vectorised strength_label for per-second arrays (used with shift charts)."""
    out = np.full(t_sk.shape, "EV", dtype=object)
    out[(t_sk == o_sk) & (t_sk == 5)] = "5v5"
    out[t_sk > o_sk] = "PP"
    out[t_sk < o_sk] = "SH"
    out[~t_g] = "EA"
    out[~o_g] = "EN"
    out[(t_sk == 0) | (o_sk == 0)] = "NONE"   # gaps in shift data, not real play
    return out


def is_power_play(sit) -> bool:
    return sit is not None and sit[0] > sit[1] and sit[2] == 1 and sit[3] == 1
