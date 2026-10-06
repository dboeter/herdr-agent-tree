#!/usr/bin/env python3
"""agent-tree: herdr workspaces as groups, their agents as a live tree.

Modes:
  --ui          the TUI that runs inside the docked pane
  --ensure      event hook: dock a tree on the left edge of the focused tab
                (--wait blocks on the dock lock instead of skipping when busy)
  --toggle      action: close the tree in this tab and keep it closed, or bring it back
  --toggle-all  action: switch auto-docking off everywhere (closing every tree) or on

Stdlib only. Talks to herdr over its socket API: one JSON request per connection,
except events.subscribe, which keeps the connection open and streams events.
"""

import fcntl
import json
import os
import re
import select
import socket
import sys
import termios
import time
import tty
import unicodedata

PLUGIN_ID = "herdr-agent-tree"
TOKEN = "herdr-agent-tree"  # pane metadata token marking our panes; value = unix-time heartbeat
WIDTH = 41  # columns the docked pane aims for
HEARTBEAT = 8
# A restored session brings panes back with their old tokens but no live process,
# so a token older than this marks a dead tree that must be replaced.
STALE = 20
SCRIPT = os.path.abspath(__file__)
SOCK = os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser(
    "~/.config/herdr/herdr.sock"
)
STATE = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser(
    f"~/.local/state/herdr/plugins/{PLUGIN_ID}"
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
        {"pane_id": pane_id, "source": TOKEN, "tokens": {TOKEN: str(int(time.time()))}},
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
        if TOKEN in (p.get("tokens") or {}) and (tab is None or p["tab_id"] == tab)
    ]


def alive(pane):
    try:
        return time.time() - float(pane["tokens"][TOKEN]) < STALE
    except (TypeError, ValueError):
        return False


def inside(outer, inner):
    return (
        outer["x"] <= inner["x"]
        and outer["y"] <= inner["y"]
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"]
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"]
    )


def fit(pane_id):
    """Shrink the split we just made so the tree is WIDTH columns wide."""
    lay = call("pane.layout", {"pane_id": pane_id})["layout"]
    me = next(p["rect"] for p in lay["panes"] if p["pane_id"] == pane_id)
    around = [s for s in lay["splits"] if inside(s["rect"], me)]
    if not around:
        return
    size = lambda s: s["rect"]["width"] * s["rect"]["height"]
    holder = min(around, key=size)
    # set_split_ratio addresses a split by its path from the tab's root split:
    # one bool per ancestor, true when the next split down sits in the second child.
    path = []
    for s in sorted((s for s in around if s is not holder), key=size, reverse=True):
        axis = "x" if s["direction"] == "right" else "y"
        path.append(holder["rect"][axis] > s["rect"][axis])
    width = holder["rect"]["width"]
    # The ratio sizes the split's first child: the tree itself when it sits on the left.
    share = WIDTH if me["x"] == holder["rect"]["x"] else width - WIDTH
    ratio = min(0.9, max(0.1, share / width))  # herdr clamps ratios to 0.1..0.9
    call("layout.set_split_ratio", {"pane_id": pane_id, "path": path, "ratio": ratio})


def dock(tab):
    # Re-snapshot: a pane list goes stale the moment anything in it closes.
    snap = snapshot()
    anchor = next((p for p in snap["panes"] if p["tab_id"] == tab), None)
    if anchor is None:
        return
    lay = call("pane.layout", {"pane_id": anchor["pane_id"]})["layout"]
    if lay["zoomed"]:
        return
    left = [p for p in lay["panes"] if p["rect"]["x"] == lay["area"]["x"]]
    if not left:
        return
    # Splitting the tallest left-edge pane gives the tallest column herdr allows:
    # panes cannot be inserted at a tab's layout root.
    target = max(left, key=lambda p: p["rect"]["height"])
    if target["rect"]["width"] < 2 * WIDTH:
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
    # herdr only splits right or down, so the tree opens right of its target; swapping
    # moves it to the left edge. The swap focuses the tree, so hand focus back.
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
    dock(tab)


def ensure(blocking):
    if load("disabled.json", False) or not lock(blocking):
        return
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
        dock(tab)


def toggle_all():
    lock(True)
    off = not load("disabled.json", False)
    save("disabled.json", off)
    snap = snapshot()
    if off:
        for p in tree_panes(snap):
            call("pane.close", {"pane_id": p["pane_id"]})
    else:
        ensure_tab(snap)


# --- tree model -------------------------------------------------------------------

STATUS = {  # agent_status -> glyph, colour
    "blocked": ("●", (247, 118, 142)),
    "working": ("◐", (224, 175, 104)),
    "done": ("✓", (158, 206, 106)),
    "idle": ("○", (110, 110, 120)),
    "unknown": ("·", (110, 110, 120)),
}
KIND = {  # brand colours
    "claude": (217, 119, 87),  # Claude orange #D97757
    "codex": (16, 163, 127),  # OpenAI green #10A37F
    "gemini": (71, 150, 227),  # Gemini blue #4796E3
    "opencode": (140, 140, 150),  # monochrome brand; grey reads on dark themes
}
# Logos shown instead of the agent name. Nerd Fonts codicons, added in Nerd Fonts
# 3.5.0: an older patched font renders these as boxes. Unlisted agents keep their name.
ICON = {
    "claude": "",  # cod-claude
    "codex": "",  # cod-openai
}
DIM = (110, 110, 120)
GROUP = (125, 207, 255)
# Workspace colours, cycled with c/C or the right-click menu; index 0 is the default.
PALETTE = [
    GROUP,
    (255, 158, 100),  # orange
    (158, 206, 106),  # green
    (187, 154, 247),  # purple
    (247, 118, 180),  # pink
    (224, 175, 104),  # yellow
    (247, 118, 142),  # red
    (115, 218, 202),  # teal
    (122, 162, 247),  # blue
]
SELECT_BG = (54, 64, 104)
MENU_BG = (36, 40, 59)  # right-click menu, a step above BASE_BG
PAD = 1  # blank columns between the pane border and the text
FLASH = 4.0  # seconds a row stays lit after its agent changes state
FLASH_HOLD = 3.0  # of which it sits at full brightness before fading
# The background a flash fades into. Terminal colours can't be queried from here, so
# this assumes the dark theme the palette is built for; the last step snaps to default.
BASE_BG = (26, 27, 38)


def blend(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def build(snap, collapsed, here, colors):
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
        colour = PALETTE[colors.get(wid, 0) % len(PALETTE)]
        segs = [
            (f"{w['number']} ", DIM, False),
            # fa-caret_down / fa-caret_right: full-height carets; ▼/▶ would clash
            # with the ▶ you-are-here marker on agent rows.
            (" " if open_ else " ", DIM, False),
            (w["label"], colour, True),
        ]
        for status in ("blocked", "working", "done"):
            n = sum(a["agent_status"] == status for a in agents)
            if n:
                glyph, colour = STATUS[status]
                segs.append((f" {glyph} {n}", colour, False))
        rows.append((("ws", wid), "ws", w, segs, 2))
        if not open_:
            continue
        for i, a in enumerate(agents):
            label = tabs.get(a["tab_id"], {}).get("label") or ""
            # One agent per tab: the tab label names it. Otherwise, or when the tab
            # only has a number for a label, the agent's own session title does.
            if per_tab[a["tab_id"]] > 1 or label.isdigit() or not label:
                label = a.get("terminal_title_stripped") or label or a["pane_id"]
            glyph, colour = STATUS.get(a["agent_status"], STATUS["unknown"])
            is_here = a["tab_id"] == here
            segs = [
                ("▶ " if is_here else "  ", colour, False),
                ("└ " if i == len(agents) - 1 else "├ ", DIM, False),
                (f"{glyph} ", colour, False),
                (label, None, is_here),
                (
                    f" {ICON.get(a['agent'], a['agent'])}",
                    KIND.get(a["agent"], DIM),
                    False,
                ),
            ]
            rows.append((("agent", a["pane_id"]), "agent", a, segs, 3))
    return rows


def summary(snap):
    """Footer segments: agent total, then a count per status that has any."""
    agents = snap["agents"]
    segs = [(f"{len(agents)} agent{'' if len(agents) == 1 else 's'}", DIM, False)]
    for status in ("blocked", "working", "done", "idle"):
        n = sum(a["agent_status"] == status for a in agents)
        if n:
            glyph, colour = STATUS[status]
            segs.append((f"  {glyph} {n}", colour, False))
    return segs


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


def paint(segs, width, bg=None, flex=None):
    """flex: index of the segment that gives up columns first, so names shrink, not kinds."""
    # Every line goes through here, so padding here keeps all rows aligned; the pad
    # sits inside the row background so a selected row stays one solid bar.
    segs, width = [(" " * PAD, None, False)] + segs, max(PAD + 1, width)
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
        sgr = (["1"] if bold else []) + (
            [f"38;2;{colour[0]};{colour[1]};{colour[2]}"] if colour else []
        )
        # Reset with 22/39 rather than 0 so a selected row keeps its background.
        out.append(f"\x1b[{';'.join(sgr)}m{text}\x1b[22;39m" if sgr else text)
    line = "".join(out) + " " * (width - used)
    if bg:
        r, g, b = bg
        return f"\x1b[48;2;{r};{g};{b}m{line}\x1b[49m"
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
        self.edit = None  # [tab_id, typed bytes] while renaming a tab
        # Right-click menu while open: the row it belongs to, its (label, action) items,
        # the highlighted item, and its (x, y, width) on screen from the last draw.
        self.menu = None
        self.status = None  # pane_id -> agent_status at the last refresh
        self.flashes = {}  # pane_id -> (start, colour, workspace_id) of a state change
        self.redraw = True  # rows or input changed since the last draw
        self.size = None  # terminal size at the last draw

    def refresh(self):
        snap = snapshot()
        mine = next((p for p in snap["panes"] if p["pane_id"] == self.me), None)
        self.here = mine["tab_id"] if mine else None
        self.labels = {t["tab_id"]: t["label"] for t in snap["tabs"]}
        status = {a["pane_id"]: a["agent_status"] for a in snap["agents"]}
        # None on the first refresh: agents already present at startup didn't change.
        if self.status is not None:
            now = time.monotonic()
            for a in snap["agents"]:
                old = self.status.get(a["pane_id"])
                if old is not None and old != a["agent_status"]:
                    colour = STATUS.get(a["agent_status"], STATUS["unknown"])[1]
                    self.flashes[a["pane_id"]] = (now, colour, a["workspace_id"])
        self.status = status
        self.rows = build(
            snap, set(load("collapsed.json", [])), self.here, load("colors.json", {})
        )
        self.footer = summary(snap)
        keys = [r[0] for r in self.rows]
        focused = snap.get("focused_tab_id") == self.here
        if focused and not self.focused:
            # Every tab runs its own tree with its own cursor, so on arrival the cursor
            # still sits where this tab's tree was last left. Snap it to ▶ instead.
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
        self.key = keys[self.cursor] if keys else None
        # draw() shows a rename on the selected row, so drop it once that row no longer
        # belongs to the tab being renamed (its agent or the tab itself went away).
        row = self.rows[self.cursor] if self.rows else None
        if self.edit and not (
            row and row[1] == "agent" and row[2]["tab_id"] == self.edit[0]
        ):
            self.edit = None
        if self.menu and self.menu["key"] != self.key:
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

    def rename(self):
        if self.rows and self.rows[self.cursor][1] == "agent":
            tab = self.rows[self.cursor][2]["tab_id"]
            self.edit = [tab, self.labels.get(tab, "").encode()]

    def open_menu(self):
        if not self.rows:
            return
        _, kind, item, _, _ = self.rows[self.cursor]
        if kind == "ws":
            shut = item["workspace_id"] in set(load("collapsed.json", []))
            items = [("Unfold" if shut else "Fold", self.activate)]
        else:
            items = [
                ("Focus", self.activate),
                ("Rename tab", self.rename),
                # The first left() climbs to the workspace row, the second folds it.
                ("Fold workspace", lambda: (self.left(), self.left())),
            ]
        items += [
            ("Next colour", lambda: self.recolor(1)),
            ("Previous colour", lambda: self.recolor(-1)),
        ]
        self.menu = {"key": self.key, "items": items, "sel": 0, "box": None}

    def menu_run(self, i):
        action = self.menu["items"][i][1]
        self.menu = None
        action()

    def menu_key(self, tok, m):
        n = len(self.menu["items"])
        if m and m.group(1):  # SGR mouse
            button, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if m.group(4) == b"m":
                return
            if button in (64, 65):
                self.menu["sel"] = (self.menu["sel"] + (1 if button == 65 else -1)) % n
                return
            box = self.menu["box"]
            if button == 0 and box:
                bx, by, bw = box
                # Items sit between the top and bottom border lines.
                if bx <= x < bx + bw and by < y <= by + n:
                    return self.menu_run(y - by - 1)
            self.menu = None  # a click anywhere else dismisses the menu
        elif tok in (b"j", b"\x1b[B"):
            self.menu["sel"] = (self.menu["sel"] + 1) % n
        elif tok in (b"k", b"\x1b[A"):
            self.menu["sel"] = (self.menu["sel"] - 1) % n
        elif tok in (b"\r", b" "):
            self.menu_run(self.menu["sel"])
        elif tok in (b"\x1b", b"q"):
            self.menu = None

    def edit_key(self, tok):
        tab, buf = self.edit
        if tok == b"\r":
            label = buf.decode("utf-8", "ignore").strip()
            self.edit = None
            if label and label != self.labels.get(tab):
                call("tab.rename", {"tab_id": tab, "label": label})
                self.refresh()
        elif tok in (b"\x1b", b"\x03"):
            self.edit = None
        elif tok in (b"\x7f", b"\x08"):
            self.edit[1] = buf.decode("utf-8", "ignore")[:-1].encode()
        elif tok >= b" " and not tok.startswith(b"\x1b"):
            # Bytes, not str: KEYS yields a multi-byte character one byte at a time.
            self.edit[1] = buf + tok

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

    def key_in(self, tok, height):
        if self.edit:
            return self.edit_key(tok)
        m = KEYS.fullmatch(tok)
        if self.menu:
            return self.menu_key(tok, m)
        if m and m.group(1):  # SGR mouse
            button, y, release = int(m.group(1)), int(m.group(3)), m.group(4) == b"m"
            if button == 64:
                self.move(self.cursor - 1)
            elif button == 65:
                self.move(self.cursor + 1)
            elif (
                button in (0, 2)
                and not release
                and y < height  # the last line is the footer
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
            b"r": self.rename,
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
        body = max(1, height - 1)  # the last line holds the summary footer
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
        for i, (key, _, _, segs, flex) in enumerate(
            self.rows[self.top : self.top + body]
        ):
            selected = self.top + i == self.cursor
            bg = SELECT_BG if selected else None
            if key in lit:
                start, colour = lit[key]
                # Hold at peak, then fade out over the remaining FLASH - FLASH_HOLD seconds.
                fade = min(1, (FLASH - (now - start)) / (FLASH - FLASH_HOLD)) ** 2
                bg = blend(bg or BASE_BG, colour, 0.5 * fade)
            if selected and self.edit:
                # The name being typed replaces the label; show its tail when it overflows.
                room = max(1, width - PAD - sum(cols(t) for t, _, _ in segs[:3]) - 1)
                typed = self.edit[1].decode("utf-8", "ignore")
                while cols(typed) > room:
                    typed = typed[1:]
                segs, flex = segs[:3] + [(typed + "▏", None, True)], None
            lines.append(paint(segs, width, bg, flex))
        if not self.rows:
            lines = [paint([("no workspaces", DIM, False)], width)]
        frame = "\x1b[H" + "\r\n".join(lines) + "\x1b[J"
        if height > 1:
            frame += f"\x1b[{height};1H" + paint(self.footer, width)
        if self.menu:
            frame += self.draw_menu(width, body)
        if frame != self.frame:
            self.frame = frame
            out.write(frame)
            out.flush()
        return height

    def draw_menu(self, width, body):
        """The menu as a bordered box under its row (above it when there is no room
        below), painted over the frame with cursor moves."""
        items = self.menu["items"]
        w = min(width, max(cols(label) for label, _ in items) + 4)
        h = len(items) + 2
        x = max(1, min(PAD + 3, width - w + 1))  # 1-based, indented under the row
        row = self.cursor - self.top + 1  # 1-based screen line of the menu's row
        y = row + 1 if row + h <= body else max(1, row - h)
        self.menu["box"] = (x, y, w)
        bg = "\x1b[48;2;{};{};{}m".format(*MENU_BG)
        sel = "\x1b[48;2;{};{};{}m".format(*SELECT_BG)
        edge = "\x1b[38;2;{};{};{}m".format(*DIM)
        lines = [edge + "╭" + "─" * (w - 2) + "╮"]
        for i, (label, _) in enumerate(items):
            label = clip(label, w - 4)
            text = f" {label}{' ' * (w - 4 - cols(label))} "
            hl = sel if i == self.menu["sel"] else ""
            lines.append(f"{edge}│\x1b[39m{hl}{text}{bg}{edge}│")
        lines.append(edge + "╰" + "─" * (w - 2) + "╯")
        return "".join(
            f"\x1b[{y + i};{x}H{bg}{line}\x1b[39;49m" for i, line in enumerate(lines)
        )

    def run(self):
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        out = sys.stdout
        tty.setraw(fd)
        out.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h")
        if self.me:
            # herdr keeps right-clicks for its pane menu unless the pane opts out.
            try:
                call("pane.input.set", {"pane_id": self.me, "right_click": "pane"})
            except (OSError, RuntimeError):
                pass
        try:
            self.loop(fd, out)
        finally:
            out.write("\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[?1049l")
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
                if not chunk:  # server went away; keep polling and resubscribe later
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
    elif mode == "--toggle":
        toggle()
    elif mode == "--toggle-all":
        toggle_all()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
