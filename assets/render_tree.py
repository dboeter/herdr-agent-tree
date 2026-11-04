"""Draw assets/tree.png: a herdr window with the tree docked left. The tree pane comes
from agent_tree.py's own build()/paint()/summary(); the window around it is a mockup.

    python3 assets/render_tree.py /tmp/tree.html
    chrome --headless --hide-scrollbars --force-device-scale-factor=2 \
        --window-size=1468,618 --screenshot=assets/tree.png file:///tmp/tree.html

Chrome needs JetBrainsMono Nerd Font installed, or the icons and carets go missing."""

import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agent_tree as at

COLS, ROWS = 150, 27
TREE_W = at.WIDTH  # outer width of the tree pane, border included
BG = at.BASE_BG
FG = (192, 202, 245)
DIM = at.DIM
CYAN = (125, 207, 255)
GREY = (86, 95, 137)

grid = [[[" ", None, None, False] for _ in range(COLS)] for _ in range(ROWS)]


def put(r, c, text, fg=None, bg=None, bold=False):
    for ch in text:
        if 0 <= c < COLS:
            grid[r][c] = [ch, fg, bg, bold]
        c += 1
    return c


def put_ansi(r, c, s):
    """Write a string with the SGR codes paint() emits (38;2 / 48;2 / 1 / 22 / 39 / 49)."""
    fg = bg = None
    bold = False
    for part in re.split(r"(\x1b\[[0-9;]*m)", s):
        if not part.startswith("\x1b["):
            c = put(r, c, part, fg, bg, bold)
            continue
        p = [int(x) for x in part[2:-1].split(";") if x]
        i = 0
        while i < len(p):
            if p[i] == 38:
                fg, i = tuple(p[i + 2 : i + 5]), i + 5
            elif p[i] == 48:
                bg, i = tuple(p[i + 2 : i + 5]), i + 5
            else:
                bold = {1: True, 22: False}.get(p[i], bold)
                fg = None if p[i] == 39 else fg
                bg = None if p[i] == 49 else bg
                i += 1
    return c


def box(r0, c0, r1, c1, colour):
    put(r0, c0, "┌" + "─" * (c1 - c0 - 1) + "┐", colour)
    put(r1, c0, "└" + "─" * (c1 - c0 - 1) + "┘", colour)
    for r in range(r0 + 1, r1):
        put(r, c0, "│", colour)
        put(r, c1, "│", colour)


# --- the session ------------------------------------------------------------------
W = [
    ("w1", "herdr-agent-tree"),
    ("w2", "daily-diff"),
    ("w3", "dagent"),
    ("w4", "homelab"),
]
T = [  # tab_id, workspace, label
    ("t1", "w1", "README Banner"),
    ("t2", "w1", "Narrow Footer"),
    ("t3", "w1", "Release"),
    ("t4", "w2", "Weekly Trends"),
    ("t5", "w2", "Digest"),
    ("t6", "w3", "ASCII DAG"),
    ("t7", "w3", "Validate Output"),
    ("t8", "w4", "Router"),
    ("t9", "w4", "Backups"),
]
A = [  # pane_id, tab_id, status, session title
    ("p1", "t1", "working", ""),
    ("p2", "t2", "done", ""),
    ("p4", "t4", "blocked", ""),
    ("p5", "t5", "idle", "spotted check"),
    ("p6", "t5", "working", "prompt fixes"),
    ("p7", "t6", "working", ""),
    ("p8", "t7", "working", ""),
    ("p9", "t8", "done", ""),
    ("p10", "t9", "idle", ""),
]
ws_of = {t: w for t, w, _ in T}
snap = {
    "workspaces": [
        {"workspace_id": w, "number": i + 1, "label": l} for i, (w, l) in enumerate(W)
    ],
    "tabs": [
        {"tab_id": t, "workspace_id": w, "label": l, "number": i + 1}
        for i, (t, w, l) in enumerate(T)
    ],
    "agents": [
        {
            "pane_id": p,
            "tab_id": t,
            "workspace_id": ws_of[t],
            "agent_status": s,
            "terminal_title_stripped": title,
        }
        for p, t, s, title in A
    ],
}
snap["panes"] = [dict(a) for a in snap["agents"]]
snap["panes"].insert(
    2,
    {
        "pane_id": "p3",
        "tab_id": "t3",
        "workspace_id": "w1",
        "cwd": "/home/me/herdr-agent-tree",
    },
)
rows = at.build(snap, {"w3", "w4"}, "t1", {}, None, True)

# --- herdr chrome: tab bar, tree pane on the left, the focused agent pane ----------
c = put(0, 1, " README Banner ", BG, CYAN, True)
c = put(0, c + 2, "Narrow Footer", FG)
c = put(0, c + 3, "Release", FG)
put(0, c + 3, "+", DIM)
right = "herdr-agent-tree · 09:52 "
put(0, COLS - len(right), right, DIM)

top, bottom = 1, ROWS - 1
box(top, 0, bottom, TREE_W - 1, GREY)
inner = TREE_W - 2
for i, (key, kind, item, segs, flex) in enumerate(rows):
    here = kind != "ws" and item["tab_id"] == "t1"
    back = at.blend(at.BASE_BG, segs[0][1], at.HERE_TINT) if here else None
    put_ansi(
        top + 1 + i, 1, at.paint(segs, inner, back, flex, segs[0][1] if here else None)
    )
put(bottom - 2, 1, "─" * inner, at.blend(DIM, BG, 0.5))
footer = at.paint(at.fit_summary(at.summary(snap), inner - 5), inner - 5)
put_ansi(bottom - 1, 1, footer + f"\x1b[{at.fg(DIM)}m{at.PLUS}  {at.COG}\x1b[39m ")

L = TREE_W
box(top, L, bottom, COLS - 1, CYAN)
GREEN, PURPLE, YEL = (158, 206, 106), (187, 154, 247), (224, 175, 104)
x = L + 2


def say(r, *lines):
    put(r, x, "●", FG)
    for i, line in enumerate(lines):
        put(r + i, x + 2, line, FG)
    return r + len(lines) + 1


def tool(r, name, arg, result):
    put(r, x, "●", GREEN)
    put(r, put(r, x + 2, name, FG, None, True), f"({arg})", FG)
    put(r + 1, x + 2, "⎿  " + result, DIM)
    return r + 3


r = top + 2
put(r, x, ">", DIM)
put(
    r,
    x + 2,
    'the banner still has an "Agent Tree" title on the pane border, but the tree',
    FG,
)
put(r + 1, x + 2, "doesn't draw one anymore. can you drop it?", FG)
r += 3
r = tool(r, "Read", "assets/banner.svg", "Read 52 lines")
r = tool(r, "Update", "assets/banner.svg", "Removed 2 lines")
r = tool(
    r,
    "Bash",
    "chrome --headless --screenshot=assets/banner.png assets/banner.svg",
    "130914 bytes written to file assets/banner.png",
)
r = say(
    r,
    "Done: the title is gone and banner.png is re-rendered. That leaves an empty",
    "band above the first workspace, so I'm moving the top border down to close it.",
)
put(r, x, "✻", YEL)
put(r, x + 2, "Moving the border… (9s · esc to interrupt)", YEL)
put(bottom - 5, x, "─" * (COLS - L - 4), GREY)
put(bottom - 4, x, ">", FG)
put(bottom - 3, x, "─" * (COLS - L - 4), GREY)
c = put(bottom - 1, x, "[Opus 5.5]", PURPLE)
c = put(bottom - 1, c + 1, "herdr-agent-tree", FG)
put(bottom - 1, c + 1, "git:(readme-banner)", PURPLE)


def hexc(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def cell(ch):
    # Glyphs the font lacks fall back to another font with a different advance width,
    # which shifts the rest of the line (the right border). Every character gets the same
    # one-cell box, so all rows round alike and the borders line up to the pixel.
    return f"<i>{html.escape(ch)}</i>"


out = []
for row in grid:
    line, run, style = [], "", None
    for ch, fg, bg, bold in row + [[None, None, None, None]]:
        s = (fg, bg, bold)
        if ch is None or s != style:
            if run:
                css = f"color:{hexc(style[0] or FG)}"
                css += f";background:{hexc(style[1])}" if style[1] else ""
                css += ";font-weight:bold" if style[2] else ""
                line.append(f'<span style="{css}">{"".join(map(cell, run))}</span>')
            run, style = "", s
        if ch is not None:
            run += ch
    out.append("".join(line))

page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;background:{hexc(BG)}}}
pre{{margin:0;padding:12px 14px;font:16px/22px 'JetBrainsMono Nerd Font Mono',monospace;
white-space:pre;color:{hexc(FG)}}}
i{{font-style:normal;display:inline-block;width:1ch;overflow:visible;vertical-align:top}}
</style></head><body><pre>{chr(10).join(out)}</pre></body></html>"""
with open(sys.argv[1], "w") as f:
    f.write(page)
