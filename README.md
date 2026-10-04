# Agent Tree

A [herdr](https://herdr.dev) plugin that docks a narrow pane on the right edge of every
tab, showing your workspaces as groups and their agents as a live tree:

```
1  dotagents (3) ◐ 1 ✓ 1
▶ ├ ◐ Improve Agent Log 
  ├ ✓ Review Prompt Skill 
  └ ○ Merge Agent Log Icons 
2  homelab (1)
  └ ● Resolve Auth Error 
4 agents  ● 1  ◐ 1  ✓ 1  ○ 1
```

- Status per agent: `●` blocked, `◐` working, `✓` done, `○` idle. A row flashes when its
  agent changes state; a collapsed workspace flashes on its agents' behalf.
- `▶` marks the agent in the tab you are looking at.
- An agent is named after its tab, or after its session title when the tab has
  several agents or only a number for a label.
- The footer counts all agents by status.

## Requirements

- herdr 0.9.0 or newer, on Linux
- `python3` on `PATH` (stdlib only, nothing to install)
- A [Nerd Font](https://www.nerdfonts.com) 3.5.0 or newer for the carets and the
  Claude/Codex logos; older fonts show boxes in their place

## Install

```sh
herdr plugin install dboeter/herdr-agent-tree
```

Or work on a local clone:

```sh
git clone https://github.com/dboeter/herdr-agent-tree
herdr plugin link herdr-agent-tree
```

Re-run `herdr plugin link` after editing `herdr-plugin.toml` so herdr reloads it. Edits
to `agent_tree.py` apply to trees opened afterwards.

## Usage

A tree docks itself into a tab whenever the tab is created or focused, as long as the
tab's right-edge pane is at least 68 columns wide and the tab is not zoomed.

| Key | Action |
|---|---|
| `j` `k` / `↓` `↑` / wheel | Move |
| `g` `G` | First / last row |
| `1`–`9` | Jump to workspace |
| `Enter` / `Space` / click | Focus the agent, or fold the workspace |
| `h` `l` / `←` `→` | Fold / unfold; `h` on an agent jumps to its workspace |
| `r` | Rename the agent's tab (`Enter` saves, `Esc` cancels) |
| `c` `C` / right-click | Cycle the workspace colour |
| `q` | Close the tree and keep this tab free of it |

Plugin actions, available from herdr's action menu:

- **Toggle agent tree in this tab**: close the tree here and keep it closed, or bring it back.
- **Toggle agent tree everywhere**: turn auto-docking off (closing every tree) or back on.

Folded workspaces, colours and per-tab opt-outs are shared by all trees and persist in
`$HERDR_PLUGIN_STATE_DIR` (`~/.local/state/herdr/plugins/local.agent-tree`).

## How it works

`agent_tree.py` is one script with several modes. Herdr runs `--ensure` on tab and
workspace events; it docks a tree into the focused tab unless a live one is already
there. Each tree is its own `--ui` process. It subscribes to herdr's event stream,
re-reads the session snapshot at most every 0.4 s on events (and every 3 s regardless),
and writes a heartbeat to its pane metadata every 5 s. A tree whose heartbeat is older
than 20 s, such as one restored from a saved session without its process, is replaced
on the next focus.
