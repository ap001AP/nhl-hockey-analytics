"""Step 4 - draw the offensive-zone half rink used behind the Tableau shot map.

The image spans exactly x 0..100 ft and y -42.5..42.5 ft (1000 x 850 px), the
same space as fact_shot[x_norm] / fact_shot[y_norm], so points line up when the
scatter axes are fixed to those ranges.

    python scripts/04_make_rink.py
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, Polygon, Rectangle, Wedge

from config import ASSETS_DIR

ICE = "#0E1D31"
LINE = "#2C4466"
RED = "#8A3F3F"
BLUE = "#2E5C9A"
CREASE = "#1B3A63"

CORNER_R, CORNER_X = 28, 72          # end-board corners: 28 ft radius
GOAL_X, BLUE_X = 89, 25              # goal line 11 ft from boards, blue line 64 ft from goal line


def main() -> None:
    fig = plt.figure(figsize=(10, 8.5), dpi=100)          # -> 1000 x 850 px
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(-42.5, 42.5)
    ax.set_aspect("equal")
    ax.axis("off")

    # Boards: straight sides, rounded corners at the end boards (x = 100)
    bottom = np.linspace(-np.pi / 2, 0, 40)
    top = np.linspace(0, np.pi / 2, 40)
    outline = ([(0, -42.5)]
               + [(CORNER_X + CORNER_R * np.cos(t), -14.5 + CORNER_R * np.sin(t)) for t in bottom]
               + [(CORNER_X + CORNER_R * np.cos(t), 14.5 + CORNER_R * np.sin(t)) for t in top]
               + [(0, 42.5)])
    ax.add_patch(Polygon(outline, closed=True, facecolor=ICE, edgecolor=LINE, linewidth=3))

    # Centre red line (at the left edge) and blue line
    ax.add_patch(Rectangle((0, -42.5), 0.8, 85, color=RED))
    ax.add_patch(Rectangle((BLUE_X - 0.5, -42.5), 1, 85, color=BLUE))

    # Goal line stops where it meets the corner curve
    y_goal = 14.5 + np.sqrt(CORNER_R ** 2 - (GOAL_X - CORNER_X) ** 2)
    ax.plot([GOAL_X, GOAL_X], [-y_goal, y_goal], color=RED, linewidth=1.5)

    # Crease (6 ft semicircle) and net (6 ft wide, 40 in deep)
    ax.add_patch(Wedge((GOAL_X, 0), 6, 90, 270, facecolor=CREASE, edgecolor=RED, linewidth=1.2))
    ax.add_patch(Rectangle((GOAL_X, -3), 3.33, 6, facecolor="none", edgecolor=LINE, linewidth=1.5))

    # End-zone faceoff circles and dots, neutral-zone dots
    for y in (-22, 22):
        ax.add_patch(Circle((69, y), 15, fill=False, edgecolor=RED, linewidth=1.2))
        ax.add_patch(Circle((69, y), 1, color=RED))
        ax.add_patch(Circle((20, y), 1, color=RED))

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    path = ASSETS_DIR / "rink_offensive_zone.png"
    fig.savefig(path, transparent=True)
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
