#!/usr/bin/env python3
"""agent-tree: herdr workspaces as groups, their agents as a live tree.

Modes:
  --ui          the TUI that runs inside the docked pane
  --ensure      event hook: dock a tree on the configured edge of the focused tab
                (--wait blocks on the dock lock instead of skipping when busy)
  --startup     startup hook: close the dead trees a restored session left in every
                tab, then dock one in the focused tab
  --toggle      action: close the tree in this tab and keep it closed, or bring it back
  --toggle-all  action: switch auto-docking off everywhere (closing every tree) or on

Stdlib only. Talks to herdr over its socket API: one JSON request per connection,
except events.subscribe, which keeps the connection open and streams events.
"""

import dataclasses
import fcntl
import json
import os
import re
import select
import socket
import sys
import termios
import time
import tomllib
import tty
import unicodedata

PLUGIN_ID = "herdr-agent-tree"
# Pane metadata token marking our panes; value = unix-time heartbeat. It is reported
# with a TTL of STALE, so herdr drops it from a tree whose process stopped stamping.
TOKEN = "herdr-agent-tree"
# Columns the docked pane aims for. herdr's pane width includes its border, so the
# text area is 2 columns narrower than this.
WIDTH = 42
HEARTBEAT = 8
# Seconds a heartbeat token lives; a tree without one has died and is replaced.
STALE = 20
# herdr restores a session's plugin panes as plain shells: the manifest pane title
# survives as the label, the tokens and the tree process do not. Must equal the
# [[panes]] title in herdr-plugin.toml.
TITLE = "Agent Tree"
SCRIPT = os.path.abspath(__file__)
SOCK = os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser(
    "~/.config/herdr/herdr.sock"
)
STATE = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser(
    f"~/.local/state/herdr/plugins/{PLUGIN_ID}"
)
CONFIG = os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or os.path.expanduser(
    f"~/.config/herdr/plugins/config/{PLUGIN_ID}"
)


def config():
    """config.toml as a dict; empty when missing or unreadable, so every key falls back."""
    try:
        with open(os.path.join(CONFIG, "config.toml"), "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def set_config(key, value):
    """Write one key into config.toml, keeping the others. The file is rewritten flat,
    so comments in it are lost; every setting is a top-level scalar or string array."""
    cfg = config()
    cfg[key] = value
    lines = []
    for k, v in cfg.items():
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, str) or (
            isinstance(v, list) and all(isinstance(x, str) for x in v)
        ):
            # JSON strings and string arrays are valid TOML as they are.
            v = json.dumps(v)
        elif not isinstance(v, (int, float)):
            continue  # tables and other arrays are not settings this plugin writes
        lines.append(f"{k} = {v}")
    os.makedirs(CONFIG, exist_ok=True)
    tmp = os.path.join(CONFIG, "config.toml.tmp")
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, os.path.join(CONFIG, "config.toml"))


def hex_rgb(value):
    """`"#rrggbb"` as an (r, g, b) tuple, or None when it isn't one."""
    if isinstance(value, str) and len(value) == 7 and value[0] == "#":
        try:
            return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass
    return None


@dataclasses.dataclass
class Settings:
    """config.toml, parsed, with the defaults config.example.toml documents."""

    side: str = "left"  # the edge trees dock on: "left" or "right"
    auto_dock: bool = True  # false stops the event hooks; toggle still docks by hand
    # How wide a tab's edge pane must be before a tree docks into it on its own.
    auto_dock_min_width: int = 2 * WIDTH
    workspace_color: tuple | None = None  # (r, g, b) overriding PALETTE[0]
    flash: bool = True
    hints: bool = False
    # Agent kinds a workspace's right-click menu offers to start in a new tab; each is
    # also the command that starts it.
    agents: tuple = ("claude", "codex")


def settings():
    """Settings from config.toml. A key that is missing or of the wrong type keeps its
    default: only false turns auto_dock or flash off, only true turns hints on."""
    cfg = config()
    width = cfg.get("auto_dock_min_width")
    agents = cfg.get("agents")
    if not (isinstance(agents, list) and all(isinstance(a, str) and a for a in agents)):
        agents = Settings.agents
    return Settings(
        side="right" if cfg.get("side") == "right" else "left",
        auto_dock=cfg.get("auto_dock") is not False,
        # The default leaves the split pane at least as wide as the tree; never below
        # WIDTH + 1, or nothing would be left of it.
        auto_dock_min_width=2 * WIDTH
        if isinstance(width, bool) or not isinstance(width, int)
        else max(WIDTH + 1, width),
        workspace_color=hex_rgb(cfg.get("workspace_color")),
        flash=cfg.get("flash") is not False,
        hints=cfg.get("hints") is True,
        agents=tuple(agents),
    )


# --- socket API -----------------------------------------------------------------


def call(method, params=None):
    with socket.socket(socket.AF_UNIX) as s:
        # A hung herdr would otherwise freeze the TUI, or park a hook on dock.lock forever.
        s.settimeout(5)
        s.connect(SOCK)
        s.sendall(
            json.dumps({"id": TOKEN, "method": method, "params": params or {}}).encode()
            + b"\n"
        )
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
    resp = json.loads(buf)
    if "error" in resp:
        raise RuntimeError(f"{method}: {resp['error']}")
    return resp["result"]


def snapshot():
    return call("session.snapshot")["snapshot"]


def stamp(pane_id):
    call(
        "pane.report_metadata",
        {
            "pane_id": pane_id,
            "source": TOKEN,
            "tokens": {TOKEN: str(int(time.time()))},
            "ttl_ms": STALE * 1000,
        },
    )


# --- state files ------------------------------------------------------------------


def load(name, default):
    try:
        with open(os.path.join(STATE, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(name, value):
    os.makedirs(STATE, exist_ok=True)
    tmp = os.path.join(STATE, name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(value, f)
    os.replace(tmp, os.path.join(STATE, name))


def lock(blocking):
    # Focus events arrive in bursts and hooks run concurrently; without this one
    # tab switch docks several trees. The kernel drops the lock when we exit.
    os.makedirs(STATE, exist_ok=True)
    fd = os.open(os.path.join(STATE, "dock.lock"), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        os.close(fd)
        return False
    return True


# --- docking ----------------------------------------------------------------------


def tree_panes(snap, tab=None):
    return [
        p
        for p in snap["panes"]
        if (TOKEN in (p.get("tokens") or {}) or p.get("label") == TITLE)
        and (tab is None or p["tab_id"] == tab)
    ]


def alive(pane):
    # Dead trees keep their "Agent Tree" label, which is how tree_panes still finds them.
    return TOKEN in (pane.get("tokens") or {})


def inside(outer, inner):
    return (
        outer["x"] <= inner["x"]
        and outer["y"] <= inner["y"]
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"]
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"]
    )


def split_path(around, holder):
    """The path layout.set_split_ratio addresses holder by, given every split around
    it: one bool per ancestor from the tab's root split down, true when the next split
    down sits in that ancestor's second child. Compare each split with its parent, not
    holder with every ancestor: two nested splits along one axis would get it wrong."""
    size = lambda s: s["rect"]["width"] * s["rect"]["height"]
    chain = sorted((s for s in around if s is not holder), key=size, reverse=True)
    path = []
    for parent, child in zip(chain, chain[1:] + [holder]):
        axis = "x" if parent["direction"] == "right" else "y"
        path.append(child["rect"][axis] > parent["rect"][axis])
    return path


def fit(pane_id):
    """Shrink the split we just made so the tree is WIDTH columns wide."""
    lay = call("pane.layout", {"pane_id": pane_id})["layout"]
    me = next(p["rect"] for p in lay["panes"] if p["pane_id"] == pane_id)
    around = [s for s in lay["splits"] if inside(s["rect"], me)]
    if not around:
        return
    holder = min(around, key=lambda s: s["rect"]["width"] * s["rect"]["height"])
    path = split_path(around, holder)
    width = holder["rect"]["width"]
    # The ratio sizes the split's first child: the tree itself when it sits on the left.
    share = WIDTH if me["x"] == holder["rect"]["x"] else width - WIDTH
    ratio = min(0.9, max(0.1, share / width))  # herdr clamps ratios to 0.1..0.9
    call("layout.set_split_ratio", {"pane_id": pane_id, "path": path, "ratio": ratio})


def dock(tab, min_width):
    # Re-snapshot: a pane list goes stale the moment anything in it closes.
    snap = snapshot()
    anchor = next((p for p in snap["panes"] if p["tab_id"] == tab), None)
    if anchor is None:
        return
    lay = call("pane.layout", {"pane_id": anchor["pane_id"]})["layout"]
    if lay["zoomed"]:
        return
    on_left = settings().side == "left"
    area = lay["area"]

    def right_x(rect):
        return rect["x"] + rect["width"]

    edge = [
        p
        for p in lay["panes"]
        if (
            p["rect"]["x"] == area["x"]
            if on_left
            else right_x(p["rect"]) == right_x(area)
        )
    ]
    if not edge:
        return
    # Splitting the tallest edge pane gives the tallest column herdr allows:
    # panes cannot be inserted at a tab's layout root.
    target = max(edge, key=lambda p: p["rect"]["height"])
    if target["rect"]["width"] < min_width:
        return
    res = call(
        "plugin.pane.open",
        {
            "plugin_id": PLUGIN_ID,
            "entrypoint": "tree",
            "placement": "split",
            "target_pane_id": target["pane_id"],
            "direction": "right",
            "focus": False,
            "env": {"HERDR_SOCKET_PATH": SOCK, "HERDR_PLUGIN_STATE_DIR": STATE},
        },
    )
    new = res["plugin_pane"]["pane"]["pane_id"]
    # Stamp while still holding the lock, so a hook queued behind us sees a live tree.
    stamp(new)
    # herdr only splits right or down, so the tree opens right of its target: already
    # on the right edge, or swapped onto the left one. The swap focuses the tree, so
    # hand focus back.
    if on_left:
        call("pane.swap", {"source_pane_id": new, "target_pane_id": target["pane_id"]})
        if lay.get("focused_pane_id"):
            call("pane.focus", {"pane_id": lay["focused_pane_id"]})
    fit(new)


def ensure_tab(snap):
    tab = snap.get("focused_tab_id")
    if not tab or tab in load("snoozed.json", []):
        return
    mine = tree_panes(snap, tab)
    if any(alive(p) for p in mine):
        return
    for p in mine:
        call("pane.close", {"pane_id": p["pane_id"]})
    dock(tab, settings().auto_dock_min_width)


def ensure(blocking):
    if not settings().auto_dock or not lock(blocking):
        return
    ensure_tab(snapshot())


def startup():
    """A restored session brings every tree back as a plain shell; clear them all at
    once instead of one by one as each tab is focused."""
    lock(True)
    for p in tree_panes(snapshot()):
        if not alive(p):
            call("pane.close", {"pane_id": p["pane_id"]})
    if settings().auto_dock:
        ensure_tab(snapshot())


def toggle():
    lock(True)
    snap = snapshot()
    tab = snap.get("focused_tab_id")
    if not tab:  # tree_panes(snap, None) would match the trees of every tab
        return
    live_tabs = {t["tab_id"] for t in snap["tabs"]}
    snoozed = [t for t in load("snoozed.json", []) if t != tab and t in live_tabs]
    mine = tree_panes(snap, tab)
    for p in mine:
        call("pane.close", {"pane_id": p["pane_id"]})
    if mine:
        snoozed.append(tab)
    save("snoozed.json", snoozed)
    if not mine:
        # By hand the tree docks wherever auto-docking would, and never needs more.
        dock(tab, min(2 * WIDTH, settings().auto_dock_min_width))


def toggle_all():
    """Flip auto_dock, the same switch as the cog menu's Auto-dock, and act on it now."""
    lock(True)
    off = settings().auto_dock
    set_config("auto_dock", not off)
    snap = snapshot()
    if off:
        for p in tree_panes(snap):
            call("pane.close", {"pane_id": p["pane_id"]})
    else:
        # "Everywhere" includes tabs closed with q, or the focused one stays empty.
        save("snoozed.json", [])
        ensure_tab(snap)


# --- tree model -------------------------------------------------------------------

STATUS = {  # agent_status -> glyph, colour
    "blocked": ("●", (247, 118, 142)),
    "working": ("◐", (224, 175, 104)),
    "done": ("✓", (158, 206, 106)),
    "idle": ("○", (110, 110, 120)),
    "unknown": ("·", (110, 110, 120)),
}
DIM = (110, 110, 120)
GROUP = (125, 207, 255)
# Workspace colours, cycled with c/C or the right-click menu; index 0 is the default.
# config.example.toml lists the same colours in hex; keep the two in step.
SWATCHES = [
    ("sky", GROUP),
    ("orange", (255, 158, 100)),
    ("green", (158, 206, 106)),
    ("purple", (187, 154, 247)),
    ("pink", (247, 118, 180)),
    ("yellow", (224, 175, 104)),
    ("red", (247, 118, 142)),
    ("teal", (115, 218, 202)),
    ("blue", (122, 162, 247)),
]
PALETTE = [rgb for _, rgb in SWATCHES]
COG = "\uf013"  # fa-cog
PLUS = "\uf067"  # fa-plus: new workspace, left of the cog
# Key hints as (key, action) groups, one line each in a wide pane, with focus and
# colour last so they share a column. hint_lines lays them out in aligned columns,
# wrapping a group when the pane is too narrow.
HINTS = [
    [("j/k", "move"), ("h/l", "fold")],
    [("n", "new"), ("r", "rename"), ("⏎", "focus")],
    [("s", "settings"), ("q", "close"), ("c", "colour")],
]
SELECT_BG = (40, 45, 70)  # selected row, halfway between BASE_BG and MENU_SELECT
# The you-are-here row's background: BASE_BG washed with this much of its workspace
# colour, so it reads as one highlight with the mark at its edge.
HERE_TINT = 0.12
MENU_SELECT = (54, 64, 104)  # highlighted menu item; must stand out from MENU_BG
MENU_BG = (36, 40, 59)  # right-click menu, a step above BASE_BG
PAD = 1  # blank columns between the pane border and the text
FLASH = 4.0  # seconds a row stays lit after its agent changes state
FLASH_HOLD = 3.0  # of which it sits at full brightness before fading
# The background a flash fades into. Terminal colours can't be queried from here, so
# this assumes the dark theme the palette is built for; the last step snaps to default.
BASE_BG = (26, 27, 38)


def blend(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def fg(rgb):
    """SGR parameters for a truecolour foreground; bg() is the background twin."""
    r, g, b = rgb
    return f"38;2;{r};{g};{b}"


def bg(rgb):
    r, g, b = rgb
    return f"48;2;{r};{g};{b}"


def status_counts(agents, sep):
    """A segment per state that wants attention, sep before each: e.g. " ● 2"."""
    segs = []
    for status in ("blocked", "working", "done"):
        n = sum(a["agent_status"] == status for a in agents)
        if n:
            glyph, colour = STATUS[status]
            segs.append((f"{sep}{glyph} {n}", colour, False))
    return segs


def palette(default=None):
    """PALETTE with workspace_color (config.toml) in the default slot 0. A default that
    is one of the swatches trades places with sky, so c/C never repeats a colour and
    sky stays reachable."""
    colours = list(PALETTE)
    if default:
        if default in colours:
            colours[colours.index(default)] = colours[0]
        colours[0] = default
    return colours


def build(snap, collapsed, here, colors, default=None):
    """Rows as (key, kind, payload, segments, flex); segments are (text, colour, bold)."""
    tabs = {t["tab_id"]: t for t in snap["tabs"]}
    per_tab = {}
    for a in snap["agents"]:
        per_tab[a["tab_id"]] = per_tab.get(a["tab_id"], 0) + 1
    # Pane ids are opaque counters; the snapshot lists a tab's panes in layout order.
    order = {p["pane_id"]: i for i, p in enumerate(snap["panes"])}
    rows = []
    for w in sorted(snap["workspaces"], key=lambda w: w["number"]):
        wid = w["workspace_id"]
        agents = sorted(
            (a for a in snap["agents"] if a["workspace_id"] == wid),
            key=lambda a: (
                tabs.get(a["tab_id"], {}).get("number", 0),
                order.get(a["pane_id"], 0),
            ),
        )
        open_ = wid not in collapsed
        ws_colour = palette(default)[colors.get(wid, 0) % len(PALETTE)]
        # The workspace you are looking at is at full colour; the others are dimmed,
        # so the mark survives folding the workspace and hiding its marked row.
        is_here = tabs.get(here, {}).get("workspace_id") == wid
        segs = [
            (f"{w['number']} ", DIM, False),
            # fa-caret_down / fa-caret_right: full-height carets.
            (" " if open_ else " ", DIM, False),
            (
                w["label"],
                ws_colour if is_here else blend(ws_colour, BASE_BG, 0.3),
                False,
            ),
        ]
        rows.append((("ws", wid), "ws", w, segs, 2))
        if not open_:
            # Counts stand in for the hidden agent rows, so only a folded workspace shows them.
            segs += status_counts(agents, " ")
            continue
        for i, a in enumerate(agents):
            tab_label = tabs.get(a["tab_id"], {}).get("label") or ""
            title = a.get("terminal_title_stripped") or ""
            # A tab label names the agent, so a rename (r renames the tab) always shows;
            # several agents in one tab add their session titles to tell them apart.
            # A tab with only a number for a label leaves the session title alone.
            if not tab_label or tab_label.isdigit():
                label = title or tab_label or a["pane_id"]
            elif per_tab[a["tab_id"]] > 1 and title:
                label = f"{tab_label} · {title}"
            else:
                label = tab_label
            glyph, colour = STATUS.get(a["agent_status"], STATUS["unknown"])
            segs = [
                # Blank; its colour is the workspace's, which the draw uses for the
                # you-are-here mark it puts in the pad column (see paint's mark).
                ("  ", ws_colour, False),
                ("└ " if i == len(agents) - 1 else "├ ", DIM, False),
                (f"{glyph} ", colour, False),
                (label, None, False),
            ]
            rows.append((("agent", a["pane_id"]), "agent", a, segs, 3))
    return rows


def summary(snap):
    """Footer segments: agent total, then a count per state that wants attention.
    Idle is left out (with everything idle it would just repeat the total), so an
    all-idle footer says so instead. Agents of unknown status are not idle."""
    agents = snap["agents"]
    if not agents:
        return [("no agents", DIM, False)]
    segs = [(f"{len(agents)} agent{'' if len(agents) == 1 else 's'}", DIM, False)]
    segs += status_counts(agents, "  ")
    if all(a["agent_status"] == "idle" for a in agents):
        segs.append((" · idle" if len(agents) == 1 else " · all idle", DIM, False))
    return segs


def hint_lines(width):
    """HINTS as paint segments that fit width (PAD included): keys in the default colour
    so they stand out, actions dimmed. Each group wraps every k hints, and all lines
    share one set of aligned columns. Picks the fewest lines that fit, preferring
    columns two apart over one apart, and as a last resort one hint per line."""
    room = width - PAD

    def size(hint):
        return cols(f"{hint[0]} {hint[1]}")

    best = None
    for k in range(max(len(g) for g in HINTS), 0, -1):
        rows = [g[i : i + k] for g in HINTS for i in range(0, len(g), k)]
        widths = [max(size(r[i]) for r in rows if i < len(r)) for i in range(k)]
        for space in (2, 1):
            fits = all(
                sum(widths[: len(r)]) + space * (len(r) - 1) <= room for r in rows
            )
            if k == 1 or fits:
                score = (len(rows), -space)
                if best is None or score < best[0]:
                    best = (score, rows, widths, space)
                break
    _, rows, widths, space = best
    lines = []
    for row in rows:
        segs = []
        for i, (key, action) in enumerate(row):
            gap = 0 if i == len(row) - 1 else widths[i] - size((key, action)) + space
            segs += [(key, None, False), (f" {action}{' ' * gap}", DIM, False)]
        lines.append(segs)
    return lines


def cols(text):
    """Terminal columns: wide East Asian characters and emoji take two, marks none.
    Nerd Font glyphs are ambiguous-width and counted as one, as the font draws them."""
    return sum(
        0
        if unicodedata.category(c) in ("Mn", "Me", "Cf")
        else 2
        if unicodedata.east_asian_width(c) in "WF"
        else 1
        for c in text
    )


def clip(text, n):
    """The longest prefix of text that fits in n columns."""
    used = 0
    for i, c in enumerate(text):
        used += cols(c)
        if used > n:
            return text[:i]
    return text


def paint(segs, width, back=None, flex=None, mark=None):
    """flex: index of the segment that gives up columns when the row is too wide.
    mark: colour of a ▍ drawn in the pad column, flush with the pane's left edge."""
    # Every line goes through here, so padding here keeps all rows aligned; the pad
    # sits inside the row background so a selected row stays one solid bar.
    # The you-are-here mark is an accent stripe at the edge, not an arrow: an arrow
    # reads as a cursor, and the cursor is the selection bar.
    lead = ("▍" + " " * (PAD - 1), mark, False) if mark else (" " * PAD, None, False)
    segs, width = [lead] + segs, max(PAD + 1, width)
    flex = None if flex is None else flex + 1
    over = sum(cols(t) for t, _, _ in segs) - width
    if flex is not None and over > 0:
        text, colour, bold = segs[flex]
        keep = max(1, cols(text) - over - 1)
        segs = segs[:flex] + [(clip(text, keep) + "…", colour, bold)] + segs[flex + 1 :]
    out, used = [], 0
    for text, colour, bold in segs:
        if used >= width:
            break
        text = clip(text, width - used)
        used += cols(text)
        sgr = (["1"] if bold else []) + ([fg(colour)] if colour else [])
        # Reset with 22/39 rather than 0 so a selected row keeps its background.
        out.append(f"\x1b[{';'.join(sgr)}m{text}\x1b[22;39m" if sgr else text)
    line = "".join(out) + " " * (width - used)
    if back:
        return f"\x1b[{bg(back)}m{line}\x1b[49m"
    return line


# --- TUI --------------------------------------------------------------------------

EVENTS = [
    "workspace.created",
    "workspace.closed",
    "workspace.renamed",
    "workspace.moved",
    "workspace.reordered",
    "workspace.focused",
    "tab.created",
    "tab.closed",
    "tab.renamed",
    "tab.moved",
    "tab.focused",
    "pane.focused",  # the selection bar shows only while the tree's own pane has focus
    "pane.created",
    "pane.closed",
    "pane.updated",
    "pane.moved",
    "pane.exited",
    "pane.agent_detected",
]
KEYS = re.compile(
    rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b\[[0-9;]*[A-Za-z~]|\x1bO[A-Za-z]|.",
    re.DOTALL,
)
# An escape sequence cut off by the end of a read. A bare ESC is not held: it is the
# Escape key far more often than a split sequence.
PARTIAL = re.compile(rb"\x1b(\[[<0-9;]*|O)\Z")


def mouse(m):
    """(button, x, y, release) of an SGR mouse report matched by KEYS, else None."""
    if not (m and m.group(1)):
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) == b"m"


MIN_GAP = 0.4  # seconds between event-driven refreshes; agent titles churn constantly
POLL = 3.0  # refresh anyway; status changes are not all covered by subscribable events
# A tree in a hidden tab only shows on arrival, so it refreshes this rarely and skips
# pane updates; changes it catches on arrival still flash, at most this late.
HIDDEN_POLL = 10.0


def subscribe():
    try:
        s = socket.socket(socket.AF_UNIX)
        s.connect(SOCK)
        s.sendall(
            json.dumps(
                {
                    "id": TOKEN,
                    "method": "events.subscribe",
                    "params": {"subscriptions": [{"type": t} for t in EVENTS]},
                }
            ).encode()
            + b"\n"
        )
        return s
    except OSError:
        return None


def noise(line, focused):
    """Heartbeat stamps from tree and sidebar panes would wake every tree every few
    seconds; a tree in a hidden tab also ignores agent status and title churn."""
    try:
        event = json.loads(line)
    except ValueError:
        return False
    if not focused and event.get("event") == "pane_updated":
        return True
    pane = event.get("data", {}).get("pane") or {}
    tokens = pane.get("tokens") or {}
    return any(k == TOKEN or k.startswith("herdr-sidebar") for k in tokens)


@dataclasses.dataclass
class Menu:
    key: tuple  # the row it belongs to
    items: list  # (label, action); an action that is a list of items opens a submenu
    sel: int = 0  # the highlighted item
    box: tuple | None = None  # (x, y, width) on screen from the last draw
    settings: bool = False  # the cog menu rather than a row's right-click menu
    sub: "Menu | None" = None  # the submenu open beside the highlighted item

    def hit(self, x, y):
        """Index of the item at screen cell (x, y), or None."""
        if self.box:
            bx, by, bw = self.box
            # Items sit between the top and bottom border lines.
            if bx <= x < bx + bw and by < y <= by + len(self.items):
                return y - by - 1
        return None


SUBMENU = "›"  # marks an item that opens a submenu, at the right of its label


def menu_size(menu, width):
    """(width, height) of a menu's bordered box, at most width columns wide."""
    label_cols = max(
        cols(label) + (2 if isinstance(action, list) else 0)
        for label, action in menu.items
    )
    return min(width, label_cols + 4), len(menu.items) + 2


def draw_box(menu, x, y, w):
    """A menu as a bordered box at (x, y), w wide; records it as menu.box for clicks."""
    menu.box = (x, y, w)
    fill = f"\x1b[{bg(MENU_BG)}m"
    sel = f"\x1b[{bg(MENU_SELECT)}m"
    edge = f"\x1b[{fg(DIM)}m"
    lines = [edge + "╭" + "─" * (w - 2) + "╮"]
    for i, (label, action) in enumerate(menu.items):
        mark = SUBMENU if isinstance(action, list) else ""
        label = clip(label, w - 4 - (2 if mark else 0))
        text = f" {label}{' ' * (w - 4 - cols(label) - cols(mark))}{mark} "
        hl = sel if i == menu.sel else ""
        lines.append(f"{edge}│\x1b[39m{hl}{text}{fill}{edge}│")
    lines.append(edge + "╰" + "─" * (w - 2) + "╯")
    return "".join(
        f"\x1b[{y + i};{x}H{fill}{line}\x1b[39;49m" for i, line in enumerate(lines)
    )


class Tree:
    def __init__(self):
        self.me = os.environ.get("HERDR_PANE_ID")
        self.rows, self.cursor, self.top = [], 0, 0
        self.footer = []  # summary segments, pinned to the last line
        self.key = None
        self.here = None
        self.focused = False  # whether our tab was the focused one at the last refresh
        self.frame = ""
        self.labels = {}  # tab_id -> label
        # [("ws", workspace_id) or ("tab", tab_id), old label, typed bytes] while renaming
        self.edit = None
        self.menu = None  # a Menu while one is open
        self.status = None  # pane_id -> agent_status at the last refresh
        self.flashes = {}  # pane_id -> (start, colour, workspace_id) of a state change
        self.redraw = True  # rows or input changed since the last draw
        self.size = None  # terminal size at the last draw
        self.active = False  # whether this tree's own pane has focus; shows the bar
        self.motion = False  # whether the terminal reports mouse motion (?1003h)
        self.settings = Settings()  # config.toml at the last refresh

    def refresh(self):
        snap = snapshot()
        self.settings = settings()
        mine = next((p for p in snap["panes"] if p["pane_id"] == self.me), None)
        self.here = mine["tab_id"] if mine else None
        self.labels = {t["tab_id"]: t["label"] for t in snap["tabs"]}
        status = {a["pane_id"]: a["agent_status"] for a in snap["agents"]}
        # None on the first refresh: agents already present at startup didn't change.
        if self.status is not None:
            now = time.monotonic()
            for a in snap["agents"]:
                old = self.status.get(a["pane_id"])
                # Only states that want you flash: blocked and done, never working/idle.
                if (
                    self.settings.flash
                    and old is not None
                    and old != a["agent_status"]
                    and a["agent_status"] in ("blocked", "done")
                ):
                    colour = STATUS.get(a["agent_status"], STATUS["unknown"])[1]
                    self.flashes[a["pane_id"]] = (now, colour, a["workspace_id"])
        self.status = status
        collapsed, colors = load("collapsed.json", []), load("colors.json", {})
        # Forget closed workspaces, so an id herdr hands out again starts unfolded and in
        # the default colour. An empty list is more likely a hiccup than no workspaces.
        live = {w["workspace_id"] for w in snap["workspaces"]}
        if live:
            if any(w not in live for w in collapsed):
                collapsed = [w for w in collapsed if w in live]
                save("collapsed.json", collapsed)
            if any(w not in live for w in colors):
                colors = {w: i for w, i in colors.items() if w in live}
                save("colors.json", colors)
        self.rows = build(
            snap, set(collapsed), self.here, colors, self.settings.workspace_color
        )
        self.footer = summary(snap)
        keys = [r[0] for r in self.rows]
        focused = snap.get("focused_tab_id") == self.here
        self.active = self.me is not None and snap.get("focused_pane_id") == self.me
        if focused and not self.focused:
            # Every tab runs its own tree with its own cursor, so on arrival the cursor
            # still sits where this tab's tree was last left. Snap it to the you-are-here row instead.
            tab = next((t for t in snap["tabs"] if t["tab_id"] == self.here), {})
            home = [
                r[0]
                for r in self.rows
                if r[1] == "agent" and r[2]["tab_id"] == self.here
            ]
            self.key = home[0] if home else ("ws", tab.get("workspace_id"))
        self.focused = focused
        if self.key in keys:
            self.cursor = keys.index(self.key)
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
        # herdr drops an agent for a few seconds while it restarts, though its pane
        # stays. Keep the key meanwhile rather than adopt the row that slid into its
        # place, so the cursor goes back to the agent when it reappears.
        panes = {p["pane_id"] for p in snap["panes"]}
        if not (self.key and self.key[0] == "agent" and self.key[1] in panes):
            self.key = keys[self.cursor] if keys else None
        # draw() shows a rename on the selected row, so drop it once that row no longer
        # belongs to what is being renamed (it, or its agent, went away).
        if self.edit and self.edit[0] != self.edit_target():
            self.edit = None
        if self.menu and self.menu.key != self.key:
            self.menu = None
        self.redraw = True

    def move(self, to):
        if self.rows:
            self.cursor = max(0, min(to, len(self.rows) - 1))
            self.key = self.rows[self.cursor][0]

    def set_collapsed(self, wid, value):
        collapsed = set(load("collapsed.json", []))
        (collapsed.add if value else collapsed.discard)(wid)
        save("collapsed.json", sorted(collapsed))
        self.refresh()

    def activate(self):
        if not self.rows:
            return
        _, kind, item, _, _ = self.rows[self.cursor]
        if kind == "ws":
            wid = item["workspace_id"]
            self.set_collapsed(wid, wid not in set(load("collapsed.json", [])))
        else:
            # agent.focus, unlike pane.focus, also marks a finished agent as seen,
            # which is what a deliberate jump to it means.
            call("agent.focus", {"target": item["pane_id"]})

    def left(self):
        if not self.rows:
            return
        _, kind, item, _, _ = self.rows[self.cursor]
        if kind == "ws":
            self.set_collapsed(item["workspace_id"], True)
        else:
            self.move(
                next(i for i in range(self.cursor, -1, -1) if self.rows[i][1] == "ws")
            )

    def right(self):
        if self.rows and self.rows[self.cursor][1] == "ws":
            self.set_collapsed(self.rows[self.cursor][2]["workspace_id"], False)
        else:
            self.activate()

    def recolor(self, step):
        """Cycle the colour of the workspace under the cursor (or of the agent's workspace)."""
        if not self.rows:
            return
        wid = self.rows[self.cursor][2]["workspace_id"]
        colors = load("colors.json", {})
        colors[wid] = (colors.get(wid, 0) + step) % len(PALETTE)
        if not colors[wid]:
            del colors[wid]
        save("colors.json", colors)
        self.refresh()

    def new_workspace(self):
        """Create a workspace in this tree's workspace directory and switch to it."""
        snap = snapshot()
        mine = {p["pane_id"] for p in tree_panes(snap)}
        wid = next(
            (t["workspace_id"] for t in snap["tabs"] if t["tab_id"] == self.here), None
        )
        # Pass cwd ourselves rather than source_workspace_id: herdr's follow policy takes
        # the focused pane's cwd, and after a click that is this tree, which runs in the
        # plugin root. This bypasses herdr's new_cwd setting ("home" would be ignored).
        # A pane in this tab first, then any in the workspace.
        panes = sorted(
            (
                p
                for p in snap["panes"]
                if p["workspace_id"] == wid and p["pane_id"] not in mine
            ),
            key=lambda p: p["tab_id"] != self.here,
        )
        params = {"focus": True}
        cwd = next((p.get("foreground_cwd") or p.get("cwd") for p in panes), None)
        if cwd:
            params["cwd"] = cwd
        call("workspace.create", params)

    def add_tab(self, wid, agent=None):
        """Open a tab in workspace wid and switch to it; with agent, start that agent."""
        snap = snapshot()
        mine = {p["pane_id"] for p in tree_panes(snap)}
        params = {"workspace_id": wid, "focus": True}
        # Pass cwd ourselves, as new_workspace does: herdr would otherwise follow the
        # focused pane, which after a click is this tree.
        cwd = next(
            (
                p.get("foreground_cwd") or p.get("cwd")
                for p in snap["panes"]
                if p["workspace_id"] == wid and p["pane_id"] not in mine
            ),
            None,
        )
        if cwd:
            params["cwd"] = cwd
        if agent:
            params["label"] = agent
        pane = call("tab.create", params)["root_pane"]["pane_id"]
        if agent:
            # Typed ahead: the shell runs it once its prompt is up, and herdr detects
            # the agent from there. agent.start would block until the agent is ready.
            call("pane.send_input", {"pane_id": pane, "text": agent, "keys": ["Enter"]})

    def edit_target(self):
        """What renaming the selected row renames: ("ws", workspace_id) or ("tab", tab_id)."""
        if not self.rows:
            return None
        _, kind, item, _, _ = self.rows[self.cursor]
        if kind == "ws":
            return ("ws", item["workspace_id"])
        return ("tab", item["tab_id"])

    def rename(self):
        target = self.edit_target()
        if not target:
            return
        item = self.rows[self.cursor][2]
        old = item["label"] if target[0] == "ws" else self.labels.get(target[1], "")
        self.edit = [target, old, old.encode()]

    def new_items(self, wid):
        """Menu items that open a new agent or tab in workspace wid."""
        items = []
        agents = [
            (agent, lambda agent=agent: self.add_tab(wid, agent))
            for agent in self.settings.agents
        ]
        if agents:
            items.append(("New agent", agents))
        items.append(("New tab", lambda: self.add_tab(wid)))
        return items

    def new_menu(self):
        """The n menu, for keyboard users: a new workspace first, then a new agent or
        tab in the selected row's workspace."""
        items = [("New workspace", self.new_workspace)]
        if self.rows:
            # An agent row's workspace is the nearest workspace row above it.
            ws = next(
                self.rows[i]
                for i in range(self.cursor, -1, -1)
                if self.rows[i][1] == "ws"
            )
            items += self.new_items(ws[2]["workspace_id"])
        self.menu = Menu(self.key, items)

    def open_menu(self):
        if not self.rows:
            return
        _, kind, item, _, _ = self.rows[self.cursor]
        if kind == "ws":
            wid = item["workspace_id"]
            shut = wid in set(load("collapsed.json", []))
            items = [("Unfold" if shut else "Fold", self.activate)]
            items += self.new_items(wid)
            items.append(("Rename workspace", self.rename))
        else:
            items = [
                ("Focus", self.activate),
                ("Rename tab", self.rename),
                # The first left() climbs to the workspace row, the second folds it.
                ("Fold workspace", lambda: (self.left(), self.left())),
            ]
        items.append(("Cycle colour", lambda: self.recolor(1)))
        # Last, away from the default selection: closing takes everything inside with it.
        if kind == "ws":
            items.append(
                (
                    "Close workspace",
                    lambda: call("workspace.close", {"workspace_id": wid}),
                )
            )
        else:
            pid = item["pane_id"]
            items.append(("Close pane", lambda: call("pane.close", {"pane_id": pid})))
        self.menu = Menu(self.key, items)

    def open_settings(self):
        """The cog menu: each item flips one config.toml setting and reopens the menu,
        so its label shows the new value."""
        s = self.settings
        hints, flash, auto = s.hints, s.flash, s.auto_dock
        right = s.side == "right"
        current = s.workspace_color or GROUP
        index = PALETTE.index(current) if current in PALETTE else -1
        nxt = (index + 1) % len(PALETTE)

        def flip(key, value):
            def run():
                set_config(key, value)
                self.refresh()
                self.open_settings()

            return run

        items = [
            (f"Key hints: {'on' if hints else 'off'}", flip("hints", not hints)),
            (f"Flash: {'on' if flash else 'off'}", flip("flash", not flash)),
            (
                f"Default colour: {SWATCHES[index][0] if index >= 0 else 'custom'}",
                flip("workspace_color", "#{:02x}{:02x}{:02x}".format(*PALETTE[nxt])),
            ),
            (f"Auto-dock: {'on' if auto else 'off'}", flip("auto_dock", not auto)),
            # side only applies to trees docked after the change; open ones stay put.
            (
                f"Dock new trees: {'right' if right else 'left'}",
                flip("side", "left" if right else "right"),
            ),
        ]
        # A flip reopens the menu; keep its highlight on the item just changed.
        sel = self.menu.sel if self.menu and self.menu.settings else 0
        self.menu = Menu(self.key, items, sel, settings=True)

    def menu_run(self, i):
        menu = self.menu
        menu.sel = i
        action = menu.items[i][1]
        if isinstance(action, list):
            menu.sub = Menu(menu.key, action)
            return
        # A settings item reopens the menu itself and keeps the highlight on item i.
        self.menu = dataclasses.replace(menu, sel=i) if menu.settings else None
        action()

    def menu_hover(self, i):
        """Highlight item i of the top menu, opening its submenu or closing another."""
        menu = self.menu
        menu.sel = i
        if not isinstance(menu.items[i][1], list):
            menu.sub = None
        elif not menu.sub:
            self.menu_run(i)

    def menu_key(self, tok, m):
        menu = self.menu
        inner = menu.sub or menu  # keys and the wheel act on the innermost menu
        n = len(inner.items)
        if click := mouse(m):
            button, x, y, release = click
            if release:
                return
            if button in (64, 65):
                inner.sel = (inner.sel + (1 if button == 65 else -1)) % n
                return
            in_sub = menu.sub and menu.sub.hit(x, y)
            in_top = menu.hit(x, y)
            if button >= 32:  # motion (?1003h, on while a menu is open): hover
                if in_sub is not None:
                    menu.sub.sel = in_sub
                elif in_top is not None:
                    self.menu_hover(in_top)
                return
            if button == 0 and in_sub is not None:
                self.menu = None
                return menu.sub.items[in_sub][1]()
            if button == 0 and in_top is not None:
                return self.menu_run(in_top)
            self.menu = None  # a click anywhere else dismisses the menu
        elif tok in (b"j", b"\x1b[B"):
            inner.sel = (inner.sel + 1) % n
        elif tok in (b"k", b"\x1b[A"):
            inner.sel = (inner.sel - 1) % n
        elif tok in (b"\r", b" ", b"l", b"\x1b[C") and menu.sub:
            self.menu = None
            menu.sub.items[menu.sub.sel][1]()
        elif tok in (b"\r", b" "):
            self.menu_run(menu.sel)
        elif tok in (b"l", b"\x1b[C"):
            if isinstance(menu.items[menu.sel][1], list):
                self.menu_run(menu.sel)
        elif tok in (b"h", b"\x1b[D", b"\x1b", b"q") and menu.sub:
            menu.sub = None
        elif tok in (b"\x1b", b"q"):
            self.menu = None

    def edit_key(self, tok):
        (kind, target), old, buf = self.edit
        if tok == b"\r":
            label = buf.decode("utf-8", "ignore").strip()
            self.edit = None
            if label and label != old:
                if kind == "ws":
                    call("workspace.rename", {"workspace_id": target, "label": label})
                else:
                    call("tab.rename", {"tab_id": target, "label": label})
                self.refresh()
        elif tok in (b"\x1b", b"\x03"):
            self.edit = None
        elif tok in (b"\x7f", b"\x08"):
            self.edit[2] = buf.decode("utf-8", "ignore")[:-1].encode()
        elif tok >= b" " and not tok.startswith(b"\x1b"):
            # Bytes, not str: KEYS yields a multi-byte character one byte at a time.
            self.edit[2] = buf + tok

    def close(self):
        """q: keep this tab free of the tree until toggled back."""
        if self.here:
            # Drop closed tabs and duplicates on the way, as toggle() does.
            snoozed = [
                t
                for t in load("snoozed.json", [])
                if t in self.labels and t != self.here
            ]
            save("snoozed.json", snoozed + [self.here])
        if self.me:
            call("pane.close", {"pane_id": self.me})
        raise SystemExit

    def footer_lines(self, height, width=None):
        """Lines below the rows, bottom up: the summary, a divider above it, and the key
        hints above that when they are on. The summary needs one row left above it, the
        others two, so a short pane drops them first. draw() lays them out from this count."""
        n = 0
        width = width or (self.size[0] if self.size else WIDTH)
        hints = len(hint_lines(width)) if self.settings.hints else 0
        for lines, rows in ((1, 1), (1, 2), (hints, 2)):
            if lines and height - n - lines >= rows:
                n += lines
        return n

    def body(self, height):
        """Lines for rows: all but the footer."""
        return max(1, height - self.footer_lines(height))

    def key_in(self, tok, height):
        # Focus reports (?1004h): instant, where the pane.focused refresh lags MIN_GAP.
        if tok in (b"\x1b[I", b"\x1b[O"):
            self.active = tok == b"\x1b[I"
            return
        m = KEYS.fullmatch(tok)
        if self.edit:
            click = mouse(m)
            # A click anywhere saves the rename, as Enter does; the click itself is spent.
            if click and click[0] in (0, 2) and not click[3]:
                tok = b"\r"
            return self.edit_key(tok)
        if self.menu:
            return self.menu_key(tok, m)
        if click := mouse(m):
            button, x, y, release = click
            width = self.size[0] if self.size else 0
            # The plus and cog are only there when the pane has room for the footer.
            footer = button == 0 and not release and y == height
            footer = footer and self.footer_lines(height) > 0
            if footer and x >= width - 1:
                self.open_settings()  # the cog, last cells of the footer
            elif footer and x >= width - 4:
                self.new_workspace()  # the plus, left of the cog
            elif button == 64:
                self.move(self.cursor - 1)
            elif button == 65:
                self.move(self.cursor + 1)
            elif (
                button in (0, 2)
                and not release
                and y <= self.body(height)  # below are the hints, divider and summary
                and self.top + y - 1 < len(self.rows)
            ):
                self.move(self.top + y - 1)
                if button == 0:
                    self.activate()
                else:
                    self.open_menu()
            return
        actions = {
            b"j": lambda: self.move(self.cursor + 1),
            b"\x1b[B": lambda: self.move(self.cursor + 1),
            b"k": lambda: self.move(self.cursor - 1),
            b"\x1b[A": lambda: self.move(self.cursor - 1),
            b"g": lambda: self.move(0),
            b"G": lambda: self.move(len(self.rows) - 1),
            b"\r": self.activate,
            b" ": self.activate,
            b"h": self.left,
            b"\x1b[D": self.left,
            b"l": self.right,
            b"\x1b[C": self.right,
            b"n": self.new_menu,
            b"r": self.rename,
            b"s": self.open_settings,
            b"c": lambda: self.recolor(1),
            b"C": lambda: self.recolor(-1),
            b"q": self.close,
        }
        if tok in actions:
            actions[tok]()
        elif tok.isdigit() and tok != b"0":
            n = int(tok)
            hit = next(
                (
                    i
                    for i, r in enumerate(self.rows)
                    if r[1] == "ws" and r[2]["number"] == n
                ),
                None,
            )
            if hit is not None:
                self.move(hit)

    def draw(self, out):
        size = os.get_terminal_size()
        width, height = size
        # Idle ticks skip the rebuild; a fading flash changes every frame.
        if not (self.redraw or self.flashes or size != self.size):
            return height
        self.redraw, self.size = False, size
        body = self.body(height)
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + body:
            self.top = self.cursor - body + 1
        now = time.monotonic()
        self.flashes = {k: f for k, f in self.flashes.items() if now - f[0] < FLASH}
        # A flash lights its agent's row, or the workspace row when that is collapsed.
        shown = {r[0] for r in self.rows}
        lit = {}
        for pid, (start, colour, wid) in self.flashes.items():
            key = ("agent", pid) if ("agent", pid) in shown else ("ws", wid)
            if key not in lit or start > lit[key][0]:
                lit[key] = (start, colour)
        lines = []
        for i, (key, kind, item, segs, flex) in enumerate(
            self.rows[self.top : self.top + body]
        ):
            selected = self.top + i == self.cursor
            here = kind == "agent" and item["tab_id"] == self.here
            # The bar is the keyboard cursor: only shown while keys reach this tree.
            # The you-are-here row gets a fainter one of its own.
            menu = self.menu and not self.menu.settings
            if selected and (self.active or menu or self.edit):
                back = SELECT_BG
            else:
                back = blend(BASE_BG, segs[0][1], HERE_TINT) if here else None
            if key in lit:
                start, colour = lit[key]
                # Hold at peak, then fade out over the remaining FLASH - FLASH_HOLD seconds.
                fade = min(1, (FLASH - (now - start)) / (FLASH - FLASH_HOLD)) ** 2
                back = blend(back or BASE_BG, colour, 0.5 * fade)
            if selected and self.edit:
                # The name being typed replaces the label (and a folded workspace's
                # counts after it); show its tail when it overflows.
                lead = segs[:2] if kind == "ws" else segs[:3]
                room = max(1, width - PAD - sum(cols(t) for t, _, _ in lead) - 1)
                typed = self.edit[2].decode("utf-8", "ignore")
                while cols(typed) > room:
                    typed = typed[1:]
                segs, flex = lead + [(typed + "▏", None, True)], None
            lines.append(paint(segs, width, back, flex, segs[0][1] if here else None))
        if not self.rows:
            lines = [paint([("no workspaces", DIM, False)], width)]
        frame = "\x1b[H" + "\r\n".join(lines) + "\x1b[J"
        extra = self.footer_lines(height, width)
        # The key hints close off the tree, right above the divider.
        if extra > 2:
            for i, segs in enumerate(hint_lines(width)):
                frame += f"\x1b[{body + 1 + i};1H" + paint(segs, width)
        if extra > 1:
            # A full-width divider, like herdr's own panel rule, a step fainter than DIM.
            rule = f"\x1b[{fg(blend(DIM, BASE_BG, 0.5))}m"
            frame += f"\x1b[{height - 1};1H{rule}{'─' * width}\x1b[39m"
        if extra:
            # Fonts draw the icons two cells wide, spilling into the space after each;
            # the plus gets two spaces so it sits clear of the cog.
            icons = f"\x1b[{fg(DIM)}m{PLUS}  {COG}\x1b[39m "
            frame += f"\x1b[{height};1H" + paint(self.footer, width - 5) + icons
        if self.menu:
            frame += self.draw_menu(width, body, height)
        # Motion reports only while a menu is open, for hover; otherwise every mouse move
        # over the pane would wake the tree.
        if bool(self.menu) != self.motion:
            self.motion = bool(self.menu)
            # Leaving ?1003 switches mouse reporting off altogether, so restore ?1000.
            out.write("\x1b[?1003h" if self.motion else "\x1b[?1003l\x1b[?1000h")
            out.flush()
        if frame != self.frame:
            self.frame = frame
            out.write(frame)
            out.flush()
        return height

    def draw_menu(self, width, body, height):
        """The menu as a bordered box under its row (above it when there is no room
        below), or above the cog for settings, painted over the frame with cursor moves.
        An open submenu sits right of its item, or as far right as the pane allows."""
        menu = self.menu
        w, h = menu_size(menu, width)
        x = max(1, min(PAD + 3, width - w + 1))  # 1-based, indented under the row
        row = self.cursor - self.top + 1  # 1-based screen line of the menu's row
        y = row + 1 if row + h <= body else max(1, row - h)
        if menu.settings:
            x, y = max(1, width - w + 1), max(1, height - h)
        out = draw_box(menu, x, y, w)
        if menu.sub:
            sw, sh = menu_size(menu.sub, width)
            # Its first item level with the item that opened it.
            sx = max(1, min(x + w, width - sw + 1))
            sy = max(1, min(y + menu.sel, height - sh + 1))
            out += draw_box(menu.sub, sx, sy, sw)
        return out

    def run(self):
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        out = sys.stdout
        tty.setraw(fd)
        out.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h\x1b[?1004h")
        if self.me:
            # herdr keeps right-clicks for its pane menu unless the pane opts out.
            try:
                call("pane.input.set", {"pane_id": self.me, "right_click": "pane"})
            except (OSError, RuntimeError):
                pass
        try:
            self.loop(fd, out)
        finally:
            out.write(
                "\x1b[?1004l\x1b[?1006l\x1b[?1003l\x1b[?1000l\x1b[?25h\x1b[?1049l"
            )
            out.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    def loop(self, fd, out):
        events, pending, keys = subscribe(), b"", b""
        last_stamp = last_refresh = last_subscribe = 0.0
        dirty = True
        # API failures are swallowed and retried: an exception here would kill the
        # pane, and the ensure hook would respawn it every STALE seconds in every tab.
        while True:
            now = time.monotonic()
            try:
                if self.me and now - last_stamp >= HEARTBEAT:
                    last_stamp = now
                    stamp(self.me)
                if (dirty and now - last_refresh >= MIN_GAP) or now - last_refresh >= (
                    POLL if self.focused else HIDDEN_POLL
                ):
                    dirty, last_refresh = False, now
                    self.refresh()
            except (OSError, RuntimeError, ValueError):
                pass
            height = self.draw(out)
            watch = [fd] + ([events] if events else [])
            # Redraw at ~20fps while a flash is fading, else idle at 4. A held flash
            # doesn't change, but wake when its fade is due so the fade starts on time.
            wait, now = 0.25, time.monotonic()
            for start, _, _ in self.flashes.values():
                wait = min(wait, max(0.05, start + FLASH_HOLD - now))
            ready, _, _ = select.select(watch, [], [], wait)
            data = os.read(fd, 1024) if fd in ready else b""
            if data or keys:
                keys += data
                # Hold a split sequence's head for the next read, so its digits aren't
                # taken as keypresses; a quiet wait releases it (e.g. a lone Alt+[).
                tail = PARTIAL.search(keys) if data else None
                cut = tail.start() if tail else len(keys)
                todo, keys = keys[:cut], keys[cut:]
                self.redraw = True
                for m in KEYS.finditer(todo):
                    if m.group(0) == b"\x03" and not self.edit:
                        return
                    try:
                        self.key_in(m.group(0), height)
                    except (OSError, RuntimeError, ValueError):
                        pass
            if events in ready:
                try:
                    chunk = events.recv(1 << 16)
                except OSError:  # e.g. connection reset: same as the server going away
                    chunk = b""
                # The server went away, or closed the stream after an events_lost
                # error (whose line already set dirty): poll and resubscribe later.
                if not chunk:
                    events.close()
                    events, pending = None, b""
                    continue
                *lines, pending = (pending + chunk).split(b"\n")
                dirty = dirty or any(
                    not noise(line, self.focused) for line in lines if line
                )
            elif events is None and now - last_subscribe >= POLL:
                last_subscribe = now
                events = subscribe()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--ui":
        Tree().run()
    elif mode == "--ensure":
        ensure(blocking="--wait" in sys.argv)
    elif mode == "--startup":
        startup()
    elif mode == "--toggle":
        toggle()
    elif mode == "--toggle-all":
        toggle_all()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
