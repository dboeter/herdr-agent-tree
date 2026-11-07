<div align="center">

<img src="assets/banner.png" alt="Agent Tree: every workspace and agent in herdr, as a live tree beside your work" width="100%">

<a href="https://github.com/dboeter/herdr-agent-tree/releases"><img src="https://img.shields.io/github/v/release/dboeter/herdr-agent-tree?style=flat-square" alt="Latest release"></a>
<a href="https://github.com/dboeter/herdr-agent-tree/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/dboeter/herdr-agent-tree/ci.yml?branch=main&style=flat-square&label=CI" alt="CI"></a>
<img src="https://img.shields.io/badge/python-%E2%89%A5%203.11-blue?style=flat-square" alt="Python 3.11+">
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT"></a>

</div>

---

## What it is

A [herdr](https://herdr.dev) plugin that docks a narrow pane on the left (or right) edge
of every tab, showing your workspaces as groups and their agents as a live tree.

## Why

With agents spread over several workspaces and tabs, the one waiting on you is easy to
miss. The tree keeps all of them in view from whichever tab you are in: what each agent is
doing, which one needs you, and one keypress or click to jump there.

## What you get

<img src="assets/tree.png" alt="Agent Tree docked left of a Claude session: four workspaces with their agents and status marks, two of them folded to their counts, and the footer totals" width="100%">

- Status per agent: `●` blocked, `◐` working, `✓` done, `○` idle. A row flashes when its
  agent becomes blocked or done; a collapsed workspace flashes on its agents' behalf.
- `▍` at the left edge marks the agent in the tab you are looking at, and that workspace's name is
  at full colour; other workspaces are dimmed. The selection bar shows only while the tree has
  focus, or while its menu or a rename is open.
- An agent is named after its tab, so renaming the tab renames the row. Several agents in
  one tab add their session titles (`api · fix tests`); a tab with only a number for a
  label shows the session title alone.
- Panes without an agent are listed too, marked `$`, named after their tab
  (plus their own label or directory when the tab holds several panes). Selecting one
  focuses it. Counts and the footer cover agents only. `panes = false` (or Other panes
  in the cog menu) lists agents only.
- A folded workspace counts its blocked, working and done agents on its own row.
- With `git_branch = true` (or Git branch in the cog menu), a workspace row shows the
  git branch most of its agents and panes are on, at the right. `git_marks = true` (Git
  marks) adds `*` for uncommitted changes and `↑`/`↓` for commits ahead of and behind
  the upstream (`main *↑2`); either works without the other. One in
  another repo or worktree shows its own; with no majority, each shows its own.
  Prefixes shorten to a letter (`feat/login` shows as `f/login`). A folded workspace
  shows its counts instead.
- A tree left as the only pane in its tab closes itself, and herdr closes the tab with it
  (and the workspace, when that was its last tab).
- The footer counts all agents, and how many are blocked, working or done ("all idle"
  when every agent is idle). In a narrow pane the total loses its word ("3  ● 1") first.

## Requirements

- herdr 0.9.0 or newer, on Linux (other platforms untested)
- `python3` 3.11 or newer on `PATH` (stdlib only, nothing to install)
- A [Nerd Font](https://www.nerdfonts.com) for the workspace carets and the footer's plus and settings cog

## Quick start

```sh
herdr plugin install dboeter/herdr-agent-tree
```

That is the whole install: the next tab you create or switch to gets a tree. Nothing
needs a restart.

To show or hide every tree with one key, add this to herdr's `config.toml` (`prefix` is
`ctrl+b` by default; pick any key that is free in your config). Use
`herdr-agent-tree.toggle` instead for the focused tab only:

```toml
[[keys.command]]
key = "prefix+shift+a"
type = "plugin_action"
command = "herdr-agent-tree.toggle-all"
description = "Toggle agent tree everywhere"
```

From a local clone instead of GitHub:

```sh
git clone https://github.com/dboeter/herdr-agent-tree
herdr plugin link ./herdr-agent-tree
```

Re-run `herdr plugin link` after editing `herdr-plugin.toml` so herdr reloads it. Edits
to `agent_tree.py` apply to trees opened afterwards. Run the tests with
`python3 -m unittest discover -s tests`.

### Or hand it to an agent

Paste this at a coding agent and it will do the install:

```text
Install the Agent Tree plugin for herdr on this machine.

1. Check that `python3 --version` is 3.11 or newer. If not, stop and tell me.
2. herdr plugin install dboeter/herdr-agent-tree
3. Check it took: `herdr plugin list` shows herdr-agent-tree as enabled.

Do NOT run `herdr server stop` and do not kill the herdr process: that ends
every program in every pane, including whatever is running you. Nothing here
needs a restart; a tree appears in the next tab I switch to.
```

## Usage

A tree docks itself into a tab whenever the tab is created or focused, as long as the
tab's tallest edge pane is at least 84 columns wide (`auto_dock_min_width`) and the tab
is not zoomed. Set `auto_dock = false` to only open trees with the toggle action.

| Key | Action |
|---|---|
| `j` `k` / `↓` `↑` / wheel | Move |
| `g` `G` | First / last row |
| `1`–`9` | Jump to workspace |
| `Enter` / `Space` / click | Focus the agent, or fold/unfold the workspace |
| click the plus in the footer | Create a workspace in this workspace's directory and switch to it |
| `n` | New menu: a workspace (as the plus), or a new agent or tab in the selected row's workspace |
| `h` `l` / `←` `→` | Fold / unfold; `h` on an agent jumps to its workspace |
| `r` | Rename the workspace, or the agent's tab (`Enter` saves, `Esc` cancels) |
| `c` `C` | Cycle the workspace colour |
| right-click | On an agent: focus, rename tab, fold workspace, cycle colour, close pane. On a workspace: fold/unfold, a new agent from the `New agent ›` submenu (hover, click or `→`), new tab, rename, cycle colour, close workspace. `Esc` or a click elsewhere closes the menu |
| `s` / click the cog in the footer | Settings menu: key hints, flash, other panes, git branch, git marks, default colour, auto-dock, dock side |
| `q` | Close the tree and keep this tab free of it |
| `Ctrl-C` | Quit the tree process only; auto-dock brings it back within about 20 s |

Plugin actions, available from herdr's action menu:

- **Toggle agent tree in this tab**: close the tree here and keep it closed, or bring it back.
- **Toggle agent tree everywhere**: flip `auto_dock`, the cog menu's Auto-dock. Off closes every tree; on docks one here and lifts every `q`.

Folded workspaces, colours and per-tab opt-outs are shared by all trees and persist in
`$HERDR_PLUGIN_STATE_DIR` (`~/.local/state/herdr/plugins/herdr-agent-tree`).

## Configuration

Settings live in `config.toml` in the plugin's config directory
(`herdr plugin config-dir herdr-agent-tree` prints it). Change them from the cog menu
(`s`) or edit the file. Every key is optional; these are the defaults:

```toml
side = "left"                # dock on the "left" or "right" edge
auto_dock = true             # false: trees only open with the toggle action
auto_dock_min_width = 84     # edge pane width (columns) a tab needs before a tree docks itself
workspace_color = "#7dcfff"  # default workspace colour; c/C still cycles the palette
flash = true                 # false stops rows flashing when an agent becomes blocked or done
hints = false                # true shows the tree's keys above the footer
panes = true                 # false lists agents only, without rows for other panes
agents = ["claude", "codex"]  # agents a workspace's right-click menu can start in a new tab
git_branch = false           # true shows git branches at the right of rows
git_marks = false            # true shows * (uncommitted) and ↑/↓ (ahead/behind) there
```

[`config.example.toml`](config.example.toml) explains each key, with the palette's
colours. The cog menu rewrites the file with its keys only, so comments in it are dropped.
Open trees pick up `workspace_color`, `flash`, `hints`, `panes`, `agents`, `git_branch` and `git_marks` within a few seconds (up to 10 s in a hidden tab);
`auto_dock` and `auto_dock_min_width` apply from the next tab switch. `side`
applies to new trees; to move an open tree, toggle it off and on again.

## How it works

`agent_tree.py` is one script with several modes. Herdr runs `--ensure` on tab and
workspace events; it docks a tree into the focused tab unless a live one is already
there. Each tree is its own `--ui` process. It subscribes to herdr's event stream,
re-reads the session snapshot at most every 0.4 s on events (and every 3 s regardless),
and writes a heartbeat to its pane metadata every 8 s, with a 20 s time-to-live. A tree
in a hidden tab ignores pane updates and re-reads every 10 s instead. A tree whose
heartbeat has expired is replaced on the next focus. A restored session brings trees
back without their processes; herdr runs `--startup` once the session is up, which
closes those in every tab and docks a fresh tree in the focused one.

With `git_branch` or `git_marks` on, a thread in the focused tree runs `git status` on the repos of the panes
listed (an agent's starting directory, a plain pane's current one) every 5 s, and the
tree redraws when a result changes. It never takes git's index lock. A repo whose
status takes over 2 s, as on a Windows drive under WSL, shows its branch only for a
minute before it is tried again.

## Troubleshooting

**No tree appears in a tab.** A tree only docks itself when the tab's tallest edge pane is
at least `auto_dock_min_width` (84) columns wide and the tab is not zoomed. It also stays
away from tabs you closed it in with `q`, and from every tab while `auto_dock = false`
(which **Toggle agent tree everywhere** sets). Run **Toggle agent tree in this tab** to dock one
by hand. If that fails too, read the hook logs:

```sh
herdr plugin log list --plugin herdr-agent-tree --limit 5
```

A `ModuleNotFoundError: No module named 'tomllib'` there means `python3` is older than 3.11.

**Boxes instead of the carets, plus and cog.** The terminal font is not a
[Nerd Font](https://www.nerdfonts.com). Install one and select it in your terminal.

**A narrow pane shows a plain shell where a tree was.** herdr restored a saved session, which
brings back the pane but not its process. The startup hook closes these when herdr starts;
one that slips through is replaced the next time you focus that tab.

**Colours look off on a light theme.** The palette and the flash fade are tuned for a dark
terminal background.

## Uninstall

Close every tree first with **Toggle agent tree everywhere**, then:

```sh
herdr plugin uninstall herdr-agent-tree
```

Settings and state stay behind in `$(herdr plugin config-dir herdr-agent-tree)` and
`~/.local/state/herdr/plugins/herdr-agent-tree`; delete those directories to remove them.

## License

[MIT](LICENSE). Changes per version are in [CHANGELOG.md](CHANGELOG.md).
