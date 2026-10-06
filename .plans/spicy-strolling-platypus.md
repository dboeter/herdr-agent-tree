# Agent tree: cut background and idle work

## Context
Review measured 6 trees: each snapshots (2.9 ms, 22 KB) every 3 s and on every
non-noise event (≤ every 0.4 s), and redraws (0.23 ms) 4×/s even when nothing changed.
5 of 6 trees sit in hidden tabs doing the same work as the visible one. Goal: cut that
work. Hidden-tab state changes must still flash; a short delay is fine.

All changes in `agent_tree.py` (+ one README line). Expect ~25 lines.

## 1. Hidden trees refresh slowly
- New constant `HIDDEN_POLL = 10.0` (seconds; a hidden tree's snapshot interval, which
  also bounds how stale its flashes can be on arrival).
- `loop()` poll condition: use `POLL if self.focused else HIDDEN_POLL`.
- Event dirtiness: while `not self.focused`, ignore `pane_updated` events (the
  status/title churn). Focus, tab, workspace and pane create/close events still mark
  dirty, so a tree refreshes promptly when its tab gets focus.
  Implement by having the event check read the `event` key (verified live:
  `{"event": "pane_updated", "data": {...}}`); reuse `noise()` by widening it to take
  the focused flag, or a small `wanted(line, focused)` next to it.
- Flashes: unchanged mechanism. `refresh()` diffs against `self.status` from the last
  refresh, so on arrival a hidden tree flashes changes from the last ≤10 s. The
  visible tree already shows/flashes all agents, including those in hidden tabs, at
  full speed.

## 2. Skip idle redraws
- `self.redraw = True` flag set at end of `refresh()` and after key handling in
  `loop()`; `self.size` stores the last terminal size.
- `draw()`: after `os.get_terminal_size()`, return early when not `self.redraw`, size
  unchanged and no `self.flashes` (check before pruning flashes so the frame after the
  last flash expires still draws, clearing the highlight). Clear the flag after drawing.
- `select` keeps its 0.25 s tick: it drives heartbeat/poll timing and resize detection,
  and a wakeup with no draw costs microseconds. (No SIGWINCH handling needed.)

## 3. Heartbeat
- `HEARTBEAT = 5` → `8` (STALE stays 20, still 2.5× margin). Fewer `pane_updated`
  events fanned out to every tree and the sidebar.
- README "How it works": "every 5 s" → "every 8 s"; mention hidden trees refresh every
  10 s.

## Todo

## Skipped
- Verify: ruff/py_compile, snapshot count per hidden tree, manual check in herdr

## Done
- Hidden trees: HIDDEN_POLL = 10 s and ignore pane_updated events while unfocused
- Skip draw() when nothing changed (redraw flag, size check, active flashes)
- HEARTBEAT 5 → 8 s
- README "How it works" timings updated

## Verification
- `ruff check agent_tree.py` / `python3 -m py_compile`.
- Re-run the earlier benchmark/event counter: with a hidden tree, count snapshots over
  20 s (expect ~2 vs ~13); visible tree unchanged.
- Manual in herdr (new trees pick up the script): open a fresh tab; switch tabs → tree
  snaps cursor to ▶ promptly; change an agent's state in another tab → visible tree
  flashes; resize the pane → redraws within 0.25 s; right-click menu, rename, j/k
  still redraw instantly.
- `ps` on `--ui` processes: CPU lower than 0.1–0.2% baseline.
