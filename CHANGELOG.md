# Changelog

## Unreleased

- `git_branch = true` and `git_marks = true` (Git branch and Git marks in the cog menu)
  show the git branch most of a workspace's agents and panes are on, right-aligned on
  its row, and the marks after it (`main *↑2`: `*` for uncommitted changes, `↑`/`↓` for
  commits ahead and behind). Each works alone. One in another repo or
  worktree shows its own; with no majority, each shows its own.
  Branch prefixes shorten to a letter (`f/login`). Both off by default.
- The footer counts agents per state, idle included, and panes without an agent, with
  the tree's marks (`◐ 1  ✓ 1  ○ 4  $ 2`), instead of an agent total and "all idle".
  `panes = false` leaves the pane count out.

## 0.4.0 — 2026-11-03

- A tree left as the only pane in its tab closes itself, so closing a tab's last work pane
  closes the tab (and its workspace, when that was the last tab) instead of leaving the tree.
- A narrow footer drops the word "agents" from the total ("3  ● 1") instead of cutting off
  the state counts.

## 0.3.0 — 2026-10-24

- `panes = false` (Other panes in the cog menu) hides the rows for panes without an
  agent, listing agents only.
- Panes without an agent get rows too, marked `$` and named after their
  tab, their own label or their directory. Selecting one focuses it; counts stay agent-only.
- The tree pane no longer shows an "Agent Tree" title on its border. Docked trees are
  recorded by pane id in `trees.json`, which is how the startup hook still finds the dead
  ones a restored session leaves behind.
- A startup hook closes the dead trees a restored session leaves in every tab and docks
  one in the focused tab, instead of each tab showing a plain shell until it is focused.
- The heartbeat token is reported with a 20 s time-to-live, so herdr drops it when a tree
  dies and liveness is just "token present".
- Fix: a tree docked beside a pane nested in two same-direction splits got the wrong width,
  because the split path compared the tree's split with every ancestor instead of its parent.
- An agent row shows its tab label whenever the tab has a real one (with the session title
  after it when the tab holds several agents), so renaming the tab is visible.
- A click during a rename saves it, as Enter does.
- **Toggle agent tree everywhere** now flips `auto_dock`, so it and the cog's Auto-dock are
  one switch; `disabled.json` is gone.
- A default colour from the palette trades places with sky in the c/C cycle, so the first
  press always changes colour and sky stays reachable.
- Folded and recoloured state of closed workspaces is forgotten.
- The footer says "all idle" only when every agent is idle, not when some are unknown.
- The you-are-here row is washed with its workspace colour and its edge mark is thicker
  (`▍`), so the agent you are looking at is easier to spot.
- The cog menu's dock item reads "Dock new trees: left/right".
- Clicks on the footer's last line do nothing when the pane is too short to draw it.

- Tests and CI: unit tests for settings, tree rows, footer and text layout, plus checks
  that `herdr-plugin.toml` and `config.example.toml` stay in step with the code.
- README: install steps for agents, troubleshooting, uninstall, and a key binding example.

## 0.2.1 — 2026-10-13

First public release.

- The current tab's agent and workspace are no longer bold; the workspace you are in stays
  at full colour while the others are dimmed.
- The new-workspace plus in the footer sits one cell further from the settings cog.
- MIT license.

## 0.1.0

- Workspaces and their agents as a live tree, docked on the left or right of every tab,
  with status marks, flashes on blocked and done, folding, renaming and workspace colours.
- Create workspaces, tabs and agents from the tree.
- Settings in `config.toml`, editable from the cog menu.
