"""Unit tests for the parts of agent_tree.py that need no running herdr.

Run from the repository root: python3 -m unittest discover -s tests
"""

import os
import re
import shutil
import subprocess
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
            'panes = false\nagents = ["claude"]\ngit_branch = true\ngit_marks = true\n'
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
        self.assertTrue(s.git_branch)
        self.assertTrue(s.git_marks)

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


class AloneTest(unittest.TestCase):
    def panes(self, *ids):
        return {"panes": [{"pane_id": p, "tab_id": t} for p, t in ids]}

    def test_only_pane_in_its_tab(self):
        self.assertTrue(at.alone(self.panes(("tree", "t1")), "tree"))
        self.assertTrue(at.alone(self.panes(("tree", "t1"), ("p", "t2")), "tree"))

    def test_another_pane_in_the_tab(self):
        self.assertFalse(at.alone(self.panes(("tree", "t1"), ("p", "t1")), "tree"))

    def test_not_listed(self):
        self.assertFalse(at.alone(self.panes(("p", "t1")), "tree"))


class AutoCloseTest(ConfigDir):
    def refresh(self, tree, panes, close):
        lone = snap(panes=panes, agents=[])
        with (
            mock.patch.object(at, "snapshot", return_value=lone),
            mock.patch.object(at, "call", close),
        ):
            tree.refresh()

    def test_a_refused_close_is_asked_once_until_the_tab_has_company(self):
        closes = []

        def refuse(method, params=None):
            closes.append(params["pane_id"])
            raise RuntimeError("pane.close: last pane")

        tree_pane = {
            "pane_id": "tree",
            "tab_id": "t1",
            "workspace_id": "w1",
            "tokens": {at.TOKEN: "1"},
        }
        other = {"pane_id": "p1", "tab_id": "t1", "workspace_id": "w1"}
        with mock.patch.dict(os.environ, {"HERDR_PANE_ID": "tree"}):
            tree = at.Tree()
        self.refresh(tree, [tree_pane], refuse)
        self.refresh(tree, [tree_pane], refuse)
        self.assertEqual(closes, ["tree"])
        self.assertTrue(tree.rows)  # still drawing after the refusal
        self.refresh(tree, [tree_pane, other], refuse)
        self.refresh(tree, [tree_pane], refuse)
        self.assertEqual(closes, ["tree", "tree"])


def with_panes(s):
    """Two plain panes beside the agents, and a tree, which is not counted."""
    s["panes"] += [
        {"pane_id": "p4", "tab_id": "t1", "workspace_id": "w1", "cwd": "/a"},
        {"pane_id": "p5", "tab_id": "t2", "workspace_id": "w2", "cwd": "/b"},
        {
            "pane_id": "p6",
            "tab_id": "t1",
            "workspace_id": "w1",
            "tokens": {at.TOKEN: "1"},
        },
    ]
    return s


class SummaryTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(text(at.summary(snap())), "◐ 1  ● 1")

    def test_idle_and_panes_use_the_tree_icons(self):
        s = with_panes(snap())
        s["agents"][1]["agent_status"] = "idle"
        self.assertEqual(text(at.summary(s)), f"{at.SHELL} 2  ○ 1  ● 1")
        # panes = false lists agents only, in the tree and in the footer.
        self.assertEqual(text(at.summary(s, panes=False)), "○ 1  ● 1")

    def test_idle_and_empty(self):
        idle = snap()
        for a in idle["agents"]:
            a["agent_status"] = "idle"
        self.assertEqual(text(at.summary(idle)), "○ 2")
        self.assertEqual(text(at.summary(snap(agents=[], panes=[]))), "no agents")
        panes_only = with_panes(snap(agents=[]))
        self.assertEqual(text(at.summary(panes_only)), f"{at.SHELL} 4")

    def test_unknown_has_its_own_mark(self):
        s = snap()
        for a in s["agents"]:
            a["agent_status"] = "idle"
        s["agents"][0]["agent_status"] = "unknown"
        self.assertEqual(text(at.summary(s)), "○ 1  ? 1")
        # A status herdr adds later is unknown to the tree, and counts as such.
        s["agents"][1]["agent_status"] = "thinking"
        self.assertEqual(text(at.summary(s)), "? 2")


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
        self.assertEqual(example["git_branch"], d.git_branch)
        self.assertEqual(example["git_marks"], d.git_marks)
        with open(path) as f:
            listed = dict(re.findall(r"(\w+) (#[0-9a-f]{6})", f.read()))
        self.assertEqual(
            {name: at.hex_rgb(h) for name, h in listed.items()}, dict(at.SWATCHES)
        )


def plain(line):
    return re.sub(r"\x1b\[[0-9;]*m", "", line)


class GitTextTest(unittest.TestCase):
    def test_parse_clean_in_sync(self):
        g = at.parse_status(
            "# branch.oid abcdef1234\n# branch.head main\n"
            "# branch.upstream origin/main\n# branch.ab +0 -0\n"
        )
        self.assertEqual(g, at.GitInfo("main", False, 0, 0))

    def test_parse_dirty_ahead_behind(self):
        g = at.parse_status(
            "# branch.oid abcdef1234\n# branch.head feat/x\n# branch.ab +2 -1\n"
            "1 .M N... 100644 100644 100644 a b file.py\n"
        )
        self.assertEqual(g, at.GitInfo("feat/x", True, 2, 1))

    def test_parse_untracked_counts_as_dirty(self):
        g = at.parse_status("# branch.oid abc1234567\n# branch.head main\n? new.txt\n")
        self.assertTrue(g.dirty)

    def test_parse_detached_shows_short_sha(self):
        g = at.parse_status("# branch.oid abcdef1234\n# branch.head (detached)\n")
        self.assertEqual(g.branch, "abcdef1")

    def test_parse_initial_commit(self):
        g = at.parse_status("# branch.oid (initial)\n# branch.head main\n")
        self.assertEqual(g, at.GitInfo("main", False, 0, 0))

    def test_short_branch_abbreviates_prefixes(self):
        self.assertEqual(at.short_branch("main"), "main")
        self.assertEqual(at.short_branch("feat/git"), "f/git")
        self.assertEqual(at.short_branch("daan/fix/login"), "d/f/login")
        self.assertEqual(at.short_branch("f/a-very-long-branch-name"), "f/a-very-long…")
        self.assertEqual(at.cols(at.short_branch("x" * 40)), at.BRANCH_COLS)

    def test_git_segments(self):
        self.assertEqual(text(at.git_segments(at.GitInfo("main", False))), "main")
        self.assertEqual(
            text(at.git_segments(at.GitInfo("feat/x", True, 2, 1))), "f/x *↑2↓1"
        )
        # Status unknown (a slow repo): branch only.
        self.assertEqual(text(at.git_segments(at.GitInfo("main", None))), "main")

    def test_git_segments_branch_or_marks_alone(self):
        dirty = at.GitInfo("feat/x", True, 2, 1)
        self.assertEqual(text(at.git_segments(dirty, marks=False)), "f/x")
        self.assertEqual(text(at.git_segments(dirty, branch=False)), "*↑2↓1")
        # Marks alone on a clean checkout: nothing to show.
        self.assertEqual(at.git_segments(at.GitInfo("main", False), branch=False), [])

    def test_paint_right_aligns(self):
        line = plain(
            at.paint(
                [("a long label here", None, False)],
                20,
                flex=0,
                right=[("main*", None, False)],
            )
        )
        self.assertEqual(at.cols(line), 20)
        # Flush right, one pad column like the left edge; the label gives way.
        self.assertTrue(line.endswith("main* "))
        self.assertIn("…", line)
        line = plain(at.paint([("ab", None, False)], 20, right=[("main", None, False)]))
        self.assertEqual(line, " ab" + " " * 12 + "main ")

    def test_paint_drops_right_when_it_cannot_fit(self):
        line = plain(
            at.paint(
                [("label", None, False)],
                8,
                flex=0,
                right=[("long-branch", None, False)],
            )
        )
        self.assertNotIn("long", line)
        self.assertEqual(at.cols(line), 8)


def git_snap():
    """w1 holds p1 (repo /r) and p3 (worktree /wt); w2 holds p2 outside any repo."""
    s = snap()
    s["agents"][1]["cwd"] = "/r"
    # foreground_cwd follows a command in another repo; the row keeps cwd's.
    s["agents"][1]["foreground_cwd"] = "/wt"
    s["agents"][0]["cwd"] = "/tmp"
    s["agents"].append(
        {
            "pane_id": "p3",
            "tab_id": "t1",
            "workspace_id": "w1",
            "agent_status": "idle",
            "cwd": "/wt/sub",
        }
    )
    return s


GIT = {
    "/r": ("/r", at.GitInfo("main", True, 1, 0)),
    "/wt/sub": ("/wt", at.GitInfo("feat/x", False)),
    "/wt": ("/wt", at.GitInfo("feat/x", False)),
    "/tmp": None,
}


class GitBuildTest(unittest.TestCase):
    def test_no_git_means_no_right_column(self):
        for r in at.build(git_snap(), set(), "t1", {}):
            self.assertEqual(r[5], [])

    def test_workspace_shows_a_repo_all_its_members_share(self):
        s = git_snap()
        s["agents"][2]["cwd"] = "/r"  # p3 joins p1 in /r
        s["panes"].append(  # a shell outside any repo neither blocks nor counts
            {"pane_id": "p5", "tab_id": "t1", "workspace_id": "w1", "cwd": "/tmp"}
        )
        rows = {r[0]: r for r in at.build(s, set(), "t1", {}, git=GIT)}
        self.assertEqual(text(rows[("ws", "w1")][5]), "main *↑1")
        for key in (("agent", "p1"), ("agent", "p3"), ("pane", "p5")):
            self.assertEqual(rows[key][5], [])
        # Outside any repo: nothing, on the workspace and its agent.
        self.assertEqual(rows[("ws", "w2")][5], [])
        self.assertEqual(rows[("agent", "p2")][5], [])
        # Folded, the counts take the row instead.
        rows = {r[0]: r for r in at.build(s, {"w1"}, "t1", {}, git=GIT)}
        self.assertEqual(rows[("ws", "w1")][5], [])

    def test_one_member_elsewhere_and_every_member_shows_its_own(self):
        s = git_snap()
        s["agents"].append(
            {
                "pane_id": "p4",
                "tab_id": "t1",
                "workspace_id": "w1",
                "agent_status": "idle",
                "cwd": "/r",
            }
        )
        rows = {r[0]: r for r in at.build(s, set(), "t1", {}, git=GIT)}
        # Two of three in /r is not all of them.
        self.assertEqual(rows[("ws", "w1")][5], [])
        self.assertEqual(text(rows[("agent", "p1")][5]), "main *↑1")
        self.assertEqual(text(rows[("agent", "p4")][5]), "main *↑1")
        self.assertEqual(text(rows[("agent", "p3")][5]), "f/x")

    def test_without_a_majority_every_member_shows_its_own(self):
        # Two agents in two repos: neither is the workspace's.
        rows = {r[0]: r for r in at.build(git_snap(), set(), "t1", {}, git=GIT)}
        self.assertEqual(rows[("ws", "w1")][5], [])
        self.assertEqual(text(rows[("agent", "p1")][5]), "main *↑1")
        self.assertEqual(text(rows[("agent", "p3")][5]), "f/x")

    def test_marks_only_follow_the_same_rows(self):
        s = git_snap()
        s["agents"].append(
            {
                "pane_id": "p4",
                "tab_id": "t1",
                "workspace_id": "w1",
                "agent_status": "idle",
                "cwd": "/r",
            }
        )
        rows = {r[0]: r for r in at.build(s, set(), "t1", {}, git=GIT, branch=False)}
        self.assertEqual(rows[("ws", "w1")][5], [])
        self.assertEqual(text(rows[("agent", "p1")][5]), "*↑1")
        # p3's worktree is clean: no marks, so no column.
        self.assertEqual(rows[("agent", "p3")][5], [])

    def test_git_dirs(self):
        self.assertEqual(at.git_dirs(git_snap()), {"/r", "/tmp", "/wt/sub"})


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitWatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.realpath(self.tmp.name)
        run = lambda *a: subprocess.run(
            ["git", "-C", self.repo, *a], check=True, capture_output=True
        )
        run("init", "-q", "-b", "main")
        os.makedirs(os.path.join(self.repo, "sub"))
        self.watch = at.GitWatch(poll=0.1)

    def tearDown(self):
        self.watch.want(set())
        self.tmp.cleanup()

    def wait(self, check):
        for _ in range(50):
            if at.select.select([self.watch.fd], [], [], 0.1)[0]:
                self.watch.drain()
            if check(self.watch.info()):
                return
        self.fail(f"never saw it: {self.watch.info()}")

    def test_reports_repo_and_follows_changes(self):
        sub = os.path.join(self.repo, "sub")
        self.watch.want({sub, "/"})
        self.wait(
            lambda i: (
                i.get(sub) == (self.repo, at.GitInfo("main", False))
                and "/" in i
                and i["/"] is None
            )
        )
        with open(os.path.join(self.repo, "new.txt"), "w") as f:
            f.write("x")
        self.wait(lambda i: i.get(sub) and i[sub][1].dirty)


class RowActionTest(unittest.TestCase):
    """Tree methods that read the selected row, on rows straight from build()."""

    def test_methods_read_build_rows(self):
        t = at.Tree()
        t.rows = at.build(snap(), set(), "t1", {})
        t.cursor = 0
        self.assertEqual(t.edit_target(), ("ws", "w1"))
        t.cursor = 1
        self.assertEqual(t.edit_target(), ("tab", "t1"))
        with mock.patch.object(at, "call") as call:
            t.activate()
            t.open_menu()
        call.assert_called()
        self.assertIsNotNone(t.menu)
        with mock.patch.object(t, "move") as move:
            t.left()
        move.assert_called_with(0)


class GitSlowTest(unittest.TestCase):
    """A repo whose status times out shows its branch only, never stale marks."""

    def fake(self, head=None):
        def run_git(*args, cwd):
            if args[0] == "rev-parse" and args[1] == "--show-toplevel":
                return "/r\n"
            if args[0] == "status":
                raise subprocess.TimeoutExpired("git", 2)
            if args[0] == "symbolic-ref" and head:
                return head + "\n"
            if args[0] == "rev-parse":
                return "abc1234\n"
            raise subprocess.CalledProcessError(1, "git")

        return run_git

    def scan(self, head):
        w = at.GitWatch.__new__(at.GitWatch)
        slow = {}
        with mock.patch.object(at, "run_git", self.fake(head)):
            return w._scan({"/r/sub"}, slow), slow

    def test_timeout_gives_branch_only_and_backs_off(self):
        info, slow = self.scan("feat/x")
        self.assertEqual(info, {"/r/sub": ("/r", at.GitInfo("feat/x", None))})
        self.assertGreater(slow[("root", "/r")], at.time.monotonic())

    def test_branch_only_holds_for_a_dir_that_is_the_root(self):
        # The status backoff for /r must not read as /r failing to find its repo.
        w = at.GitWatch.__new__(at.GitWatch)
        slow = {}
        with mock.patch.object(at, "run_git", self.fake("main")):
            w._scan({"/r"}, slow)
            info = w._scan({"/r"}, slow)
        self.assertEqual(info, {"/r": ("/r", at.GitInfo("main", None))})

    def test_detached_falls_back_to_sha(self):
        info, _ = self.scan(None)
        self.assertEqual(info["/r/sub"][1].branch, "abc1234")

    def test_slow_toplevel_is_not_retried(self):
        w = at.GitWatch.__new__(at.GitWatch)
        slow, calls = {}, []

        def run_git(*args, cwd):
            calls.append(args)
            raise subprocess.TimeoutExpired("git", 2)

        with mock.patch.object(at, "run_git", run_git):
            self.assertEqual(w._scan({"/d"}, slow), {"/d": None})
            w._scan({"/d"}, slow)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
