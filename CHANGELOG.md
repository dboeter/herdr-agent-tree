# Changelog

## Unreleased

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
