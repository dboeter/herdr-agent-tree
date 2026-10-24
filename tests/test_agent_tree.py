"""Unit tests for the parts of agent_tree.py that need no running herdr.

Run from the repository root: python3 -m unittest discover -s tests
"""

import os
import re
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import agent_tree as at


class ConfigDir(unittest.TestCase):
    """Points the module at an empty config directory for each test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = at.CONFIG, at.STATE
        at.CONFIG = at.STATE = self.tmp.name

    def tearDown(self):
        at.CONFIG, at.STATE = self.saved
        self.tmp.cleanup()

    def write(self, text):
        with open(os.path.join(at.CONFIG, "config.toml"), "w") as f:
            f.write(text)


class SettingsTest(ConfigDir):
    def test_defaults_without_a_file(self):
        self.assertEqual(at.settings(), at.Settings())

    def test_defaults_on_a_broken_file(self):
        self.write("side = ")
        self.assertEqual(at.settings(), at.Settings())

    def test_values_are_read(self):
        self.write(
            'side = "right"\nauto_dock = false\nauto_dock_min_width = 100\n'
            'workspace_color = "#ff9e64"\nflash = false\nhints = true\n'
            'panes = false\nagents = ["claude"]\n'
        )
        s = at.settings()
        self.assertEqual(s.side, "right")
        self.assertFalse(s.auto_dock)
        self.assertEqual(s.auto_dock_min_width, 100)
        self.assertEqual(s.workspace_color, (255, 158, 100))
        self.assertFalse(s.flash)
        self.assertTrue(s.hints)
        self.assertFalse(s.panes)
        self.assertEqual(s.agents, ("claude",))

    def test_wrong_types_keep_defaults(self):
        self.write(
            'side = "up"\nauto_dock = "no"\nauto_dock_min_width = true\n'
            'workspace_color = "blue"\nflash = 0\nhints = "yes"\npanes = 0\n'
            "agents = [1]\n"
        )
        self.assertEqual(at.settings(), at.Settings())

    def test_min_width_leaves_a_column_beside_the_tree(self):
        self.write("auto_dock_min_width = 10\n")
        self.assertEqual(at.settings().auto_dock_min_width, at.WIDTH + 1)

    def test_set_config_round_trips(self):
        self.write('agents = ["claude", "codex"]\n')
        at.set_config("hints", True)
        at.set_config("workspace_color", "#9ece6a")
        s = at.settings()
        self.assertTrue(s.hints)
        self.assertEqual(s.workspace_color, (158, 206, 106))
        self.assertEqual(s.agents, ("claude", "codex"))


def snap(**over):
    """A session snapshot: workspace 2 listed before workspace 1, one tab each."""
    base = {
        "workspaces": [
            {"workspace_id": "w2", "number": 2, "label": "beta"},
            {"workspace_id": "w1", "number": 1, "label": "alpha"},
        ],
        "tabs": [
            {"tab_id": "t1", "workspace_id": "w1", "number": 1, "label": "api"},
            {"tab_id": "t2", "workspace_id": "w2", "number": 1, "label": "3"},
        ],
        "panes": [{"pane_id": "p1"}, {"pane_id": "p2"}],
        "agents": [
            {
                "pane_id": "p2",
                "tab_id": "t2",
                "workspace_id": "w2",
                "agent_status": "blocked",
                "terminal_title_stripped": "fix login",
            },
            {
                "pane_id": "p1",
                "tab_id": "t1",
                "workspace_id": "w1",
                "agent_status": "working",
            },
        ],
    }
    base.update(over)
    return base


def text(segs):
    return "".join(t for t, _, _ in segs)


class BuildTest(unittest.TestCase):
    def test_workspaces_sort_by_number_with_agents_under_them(self):
        rows = at.build(snap(), set(), "t1", {})
        self.assertEqual(
            [r[0] for r in rows],
            [("ws", "w1"), ("agent", "p1"), ("ws", "w2"), ("agent", "p2")],
        )

    def test_agent_names(self):
        rows = at.build(snap(), set(), "t1", {})
        # Tab label when it names the tab, the session title when the label is a number.
        self.assertTrue(text(rows[1][3]).endswith("api"))
        self.assertTrue(text(rows[3][3]).endswith("fix login"))

    def test_folded_workspace_hides_agents_and_counts_them(self):
        rows = at.build(snap(), {"w2"}, "t1", {})
        self.assertEqual([r[0] for r in rows][2:], [("ws", "w2")])
        self.assertIn("● 1", text(rows[2][3]))

    def test_open_workspace_shows_no_counts(self):
        rows = at.build(snap(), set(), "t1", {})
        self.assertNotIn("●", text(rows[2][3]))

    def test_colours(self):
        rows = at.build(snap(), set(), "t1", {"w1": 1}, default=(1, 2, 3))
        # w1 is here and recoloured: full palette colour. w2 is the default, dimmed.
        self.assertEqual(rows[0][3][2][1], at.PALETTE[1])
        self.assertEqual(rows[2][3][2][1], at.blend((1, 2, 3), at.BASE_BG, 0.3))

    def test_tab_label_names_every_agent_in_its_tab(self):
        s = snap()
        s["agents"].append(
            {
                "pane_id": "p3",
                "tab_id": "t1",
                "workspace_id": "w1",
                "agent_status": "idle",
                "terminal_title_stripped": "fix tests",
            }
        )
        rows = at.build(s, set(), "t1", {})
        # A renamed tab shows on every row; the session title tells two agents apart.
        self.assertTrue(text(rows[1][3]).endswith("api"))
        self.assertTrue(text(rows[2][3]).endswith("api · fix tests"))

    def test_plain_panes_get_rows_beside_agents(self):
        s = snap()
        s["panes"] += [
            {
                "pane_id": "p4",
                "tab_id": "t1",
                "workspace_id": "w1",
                "cwd": "/home/me/notes",
            },
            {
                "pane_id": "p5",
                "tab_id": "t2",
                "workspace_id": "w2",
                "cwd": "/srv",
                "label": "logs",
            },
            {  # a tree: not a row
                "pane_id": "p6",
                "tab_id": "t1",
                "workspace_id": "w1",
                "tokens": {at.TOKEN: "1"},
            },
        ]
        rows = at.build(s, {"w2"}, "t1", {})
        self.assertEqual(
            [r[0] for r in rows],
            [("ws", "w1"), ("agent", "p1"), ("pane", "p4"), ("ws", "w2")],
        )
        # The tab label names both panes in it; the directory tells them apart.
        self.assertTrue(text(rows[2][3]).endswith(f"{at.SHELL} api · notes"))
        # A folded workspace counts agents only.
        self.assertTrue(text(rows[3][3]).endswith("● 1"))
        rows = at.build(s, set(), "t1", {})
        # A numbered tab leaves the pane's own label.
        self.assertTrue(text(rows[-1][3]).endswith("logs"))
        # panes = false lists agents only.
        rows = at.build(s, set(), "t1", {}, panes=False)
        self.assertEqual(
            [r[0] for r in rows],
            [("ws", "w1"), ("agent", "p1"), ("ws", "w2"), ("agent", "p2")],
        )


class PaletteTest(unittest.TestCase):
    def test_default_from_the_palette_trades_places_with_sky(self):
        orange = at.PALETTE[1]
        colours = at.palette(orange)
        self.assertEqual(colours[0], orange)
        self.assertEqual(colours[1], at.PALETTE[0])
        self.assertEqual(sorted(colours), sorted(at.PALETTE))

    def test_custom_default_takes_slot_zero(self):
        self.assertEqual(at.palette((1, 2, 3)), [(1, 2, 3)] + at.PALETTE[1:])
        self.assertEqual(at.palette(), at.PALETTE)


def split(direction, x, y, w, h):
    return {"direction": direction, "rect": {"x": x, "y": y, "width": w, "height": h}}


class SplitPathTest(unittest.TestCase):
    def test_nested_splits_along_one_axis(self):
        # Root splits down (top 80, bottom 20); the top splits down again, and the tree's
        # split is the lower half of that: first child of root, second of the next.
        root = split("down", 0, 0, 100, 100)
        top = split("down", 0, 0, 100, 80)
        holder = split("right", 0, 20, 100, 60)
        self.assertEqual(at.split_path([holder, root, top], holder), [False, True])

    def test_right_edge(self):
        root = split("right", 0, 0, 200, 50)
        right = split("right", 100, 0, 100, 50)
        holder = split("right", 150, 0, 50, 50)
        self.assertEqual(at.split_path([root, right, holder], holder), [True, True])

    def test_holder_is_the_root(self):
        holder = split("right", 0, 0, 100, 50)
        self.assertEqual(at.split_path([holder], holder), [])


class SummaryTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(text(at.summary(snap())), "2 agents  ● 1  ◐ 1")

    def test_idle_and_empty(self):
        idle = snap()
        for a in idle["agents"]:
            a["agent_status"] = "idle"
        self.assertEqual(text(at.summary(idle)), "2 agents · all idle")
        self.assertEqual(text(at.summary(snap(agents=[]))), "no agents")

    def test_unknown_is_not_idle(self):
        s = snap()
        for a in s["agents"]:
            a["agent_status"] = "idle"
        s["agents"][0]["agent_status"] = "unknown"
        self.assertEqual(text(at.summary(s)), "2 agents")


class TextTest(unittest.TestCase):
    def test_cols(self):
        self.assertEqual(at.cols("abc"), 3)
        self.assertEqual(at.cols("日本"), 4)
        self.assertEqual(at.cols("é"), 1)

    def test_clip_never_splits_a_wide_character(self):
        self.assertEqual(at.clip("日本", 3), "日")
        self.assertEqual(at.clip("abc", 5), "abc")

    def test_paint_fills_the_width_exactly(self):
        line = at.paint([("a long label here", None, False)], 10, flex=0)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
        self.assertEqual(at.cols(plain), 10)
        self.assertTrue(plain.rstrip().endswith("…"))

    def test_hints_fit(self):
        for width in (20, 30, at.WIDTH, 80):
            for segs in at.hint_lines(width):
                self.assertLessEqual(at.cols(text(segs)), width - at.PAD)


class StartupTest(ConfigDir):
    def test_closes_dead_trees_everywhere_then_docks(self):
        snap = {
            "panes": [
                {"pane_id": "a", "tab_id": "t1", "label": at.TITLE},  # restored shell
                {"pane_id": "b", "tab_id": "t2", "tokens": {at.TOKEN: "1"}},  # live
                {"pane_id": "c", "tab_id": "t2", "label": "zsh"},  # not ours
            ]
        }
        calls = []
        with (
            mock.patch.object(at, "lock", return_value=True),
            mock.patch.object(at, "snapshot", return_value=snap),
            mock.patch.object(at, "call", lambda m, p=None: calls.append((m, p))),
            mock.patch.object(at, "ensure_tab") as ensure_tab,
        ):
            at.startup()
        self.assertEqual(calls, [("pane.close", {"pane_id": "a"})])
        ensure_tab.assert_called_once()

    def test_finds_restored_trees_by_recorded_id_and_cwd(self):
        at.save("trees.json", {"a": "/plugin", "c": "/plugin"})
        snap = {
            "panes": [
                {"pane_id": "a", "tab_id": "t1", "cwd": "/plugin"},  # restored shell
                {"pane_id": "c", "tab_id": "t2", "cwd": "/home/me"},  # id reused
            ]
        }
        self.assertEqual([p["pane_id"] for p in at.tree_panes(snap)], ["a"])


class DockTest(ConfigDir):
    def test_records_the_new_tree_and_keeps_dead_ones_still_present(self):
        at.save("trees.json", {"dead": "/plugin", "gone": "/plugin"})
        snap = {
            "panes": [
                {"pane_id": "work", "tab_id": "t1"},
                {"pane_id": "dead", "tab_id": "t2", "cwd": "/plugin"},
            ]
        }
        rect = {"x": 0, "y": 0, "width": 200, "height": 50}
        layout = {
            "zoomed": False,
            "area": rect,
            "panes": [{"pane_id": "work", "rect": rect}],
            "splits": [],
        }
        replies = {
            "pane.layout": {"layout": layout},
            "plugin.pane.open": {
                "plugin_pane": {"pane": {"pane_id": "new", "cwd": "/plugin"}}
            },
        }
        calls = []

        def call(method, params=None):
            calls.append((method, params))
            return replies.get(method, {})

        with (
            mock.patch.object(at, "snapshot", return_value=snap),
            mock.patch.object(at, "call", call),
            mock.patch.object(at, "fit"),
        ):
            at.dock("t1", 0)
        self.assertIn(("pane.rename", {"pane_id": "new", "label": None}), calls)
        self.assertEqual(
            at.load("trees.json", None), {"dead": "/plugin", "new": "/plugin"}
        )


class ConsistencyTest(unittest.TestCase):
    """Facts kept in two files, which comments ask to keep in step."""

    def test_pane_title_matches_manifest(self):
        with open(os.path.join(ROOT, "herdr-plugin.toml"), "rb") as f:
            manifest = tomllib.load(f)
        self.assertEqual(manifest["id"], at.PLUGIN_ID)
        self.assertEqual(manifest["panes"][0]["title"], at.TITLE)

    def test_example_config_matches_defaults_and_palette(self):
        path = os.path.join(ROOT, "config.example.toml")
        with open(path, "rb") as f:
            example = tomllib.load(f)
        d = at.Settings()
        self.assertEqual(example["side"], d.side)
        self.assertEqual(example["auto_dock"], d.auto_dock)
        self.assertEqual(example["auto_dock_min_width"], d.auto_dock_min_width)
        self.assertEqual(at.hex_rgb(example["workspace_color"]), at.PALETTE[0])
        self.assertEqual(example["flash"], d.flash)
        self.assertEqual(example["hints"], d.hints)
        self.assertEqual(example["panes"], d.panes)
        self.assertEqual(tuple(example["agents"]), d.agents)
        with open(path) as f:
            listed = dict(re.findall(r"(\w+) (#[0-9a-f]{6})", f.read()))
        self.assertEqual(
            {name: at.hex_rgb(h) for name, h in listed.items()}, dict(at.SWATCHES)
        )


if __name__ == "__main__":
    unittest.main()
