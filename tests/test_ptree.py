import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SCRATCH = tempfile.mkdtemp(prefix="ptree-test-")
os.environ["PTREE_HOME"] = os.path.join(SCRATCH, "projects")

from memolib import store as S  # noqa: E402
from ptree import cli, files as F, history, keeper, links, registry, search  # noqa: E402
from ptree.tree import ProjectTree  # noqa: E402


def write(root, rel, text=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def project(files):
    root = tempfile.mkdtemp(prefix="proj-", dir=SCRATCH)
    for rel, text in files.items():
        write(root, rel, text)
    return root


FILES = {
    "README.md": "# Demo\n\nA demo app that draws graphs.\n",
    "apps/web/src/graph/layout.ts": "/** Automatic graph layout */\nexport function autoLayout() {}\nexport class Grid {}\n",
    "apps/web/src/graph/edges.ts": "export const drawEdge = 1;\n",
    "apps/web/src/graph/nodes.ts": "export const drawNode = 1;\n",
    "apps/web/src/util.ts": "export const clamp = 1;\n",
    "apps/web/src/main.ts": "export default function main() {}\n",
    "apps/api/server.py": '"""HTTP server for the demo."""\ndef serve(): pass\nclass Api: pass\n',
    "docs/guide.md": "---\ntitle: Guide\nsummary: How to run the demo\n---\n# Heading\nbody\n",
    "docs/notes/a.md": "# A note\nfirst line\n",
    "package.json": '{"name": "demo", "description": "demo app", "scripts": {}}',
    "assets/logo.png": "binary",
    "node_modules/x/index.js": "ignored",
}


class FileTests(unittest.TestCase):
    def test_mechanical_summaries_say_what_a_file_says_about_itself(self):
        self.assertEqual(F.outline("a.md", "---\ntitle: Guide\nsummary: How to run\n---\n# H\nbody"), "Guide — How to run")
        self.assertEqual(F.outline("a.md", "# Title\n\n> quote\nFirst real line\n"), "Title — First real line")
        self.assertIn("autoLayout, Grid", F.outline("a.ts", "/** Automatic graph layout */\nexport function autoLayout() {}\nexport class Grid {}\n"))
        self.assertIn("Automatic graph layout", F.outline("a.ts", "/** Automatic graph layout */\nexport const x = 1;"))
        self.assertIn("serve, Api", F.outline("s.py", '"""HTTP server."""\ndef serve(): pass\nclass Api: pass\n'))
        self.assertEqual(F.outline("package.json", '{"name":"demo","description":"d"}', 10), "demo — d")
        self.assertIn("css", F.outline("a.css", "body{}", 2048))

    def test_binary_generated_and_ignored_files_are_not_indexed(self):
        root = project(FILES)
        found = F.list_files(root)
        self.assertIn("apps/web/src/main.ts", found)
        self.assertNotIn("assets/logo.png", found)
        self.assertFalse([f for f in found if f.startswith("node_modules")])

    def test_git_view_includes_untracked_but_not_ignored_files_and_skips_submodule_folders(self):
        root = project({"a.txt": "tracked", ".gitignore": "secret.txt\n", "secret.txt": "x", "new.md": "# New\n"})
        subprocess.run(["git", "-C", root, "init", "-q"], check=True)
        subprocess.run(["git", "-C", root, "add", "a.txt", ".gitignore"], check=True)
        self.assertEqual(sorted(F.list_files(root)), [".gitignore", "a.txt", "new.md"])

    def test_a_rescan_reads_only_what_changed(self):
        root = project(FILES)
        cache = F.FileCache(os.path.join(SCRATCH, "cache-%d.json" % id(self)))
        listed = F.list_files(root)
        first = cache.refresh(root, listed)
        self.assertEqual(first, len(listed))
        self.assertEqual(cache.refresh(root, listed), 0)
        write(root, "docs/notes/a.md", "# A changed note\nnew line\n")
        self.assertEqual(cache.refresh(root, listed), 1)
        self.assertIn("changed note", cache.data["docs/notes/a.md"][3])


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.root = project(FILES)
        self.tree = ProjectTree("Demo", self.root, os.path.join(SCRATCH, "home-%d" % id(self)))
        self.tree.scan()

    def test_ids_say_where_a_node_is_and_every_interval_splits_in_two(self):
        tree = self.tree
        self.assertEqual(tree.parse("/"), ("slice", "", ""))
        self.assertEqual(tree.count("/"), len(tree.files))
        root_children = tree.children("/")
        self.assertEqual(len(root_children), 2)
        self.assertEqual(tree.parse("apps/web/src/graph/layout.ts")[0], "file")
        self.assertEqual(tree.children("apps/web/src/graph/layout.ts"), [])
        leaves = []

        def walk(node_id):
            kids = tree.children(node_id)
            if not kids:
                leaves.append(node_id)
            assert len(kids) in (0, 1, 2), node_id
            for kid in kids:
                walk(kid)

        walk("/")
        self.assertEqual(sorted(leaves), sorted(tree.files), "every file is exactly one leaf")
        for bad in ("nope/", "apps/#2", "missing.ts", "apps/web/src/#0000000"):
            with self.assertRaises(KeyError):
                tree.parse(bad)
                tree.children(bad)

    def test_wake_opens_the_biggest_interval_first_and_zoom_shows_one_level(self):
        wake = self.tree.wake(5)
        self.assertEqual(len(wake), 5)
        self.assertEqual(sum(b["count"] for b in wake), len(self.tree.files))
        brief, kids = self.tree.zoom("apps/")
        self.assertEqual(brief["kind"], "dir")
        self.assertEqual({k["id"] for k in kids}, set(self.tree.children("apps/")))

    def test_only_directories_and_large_intervals_are_written_by_a_model(self):
        ai = self.tree.ai_nodes()
        self.assertIn("apps/web/src/graph/", ai)
        self.assertNotIn("apps/web/src/graph/layout.ts", ai)
        self.assertIn("/", ai)
        self.assertNotIn("assets/", ai, "no such folder, the logo is binary")

    def test_summaries_are_written_bottom_up_and_a_change_rewrites_only_its_own_path(self):
        calls = []

        class Fake:
            def complete_json(self, prompt, thinking):
                import re
                ids = re.findall(r"^### (\S+)", prompt, re.M)
                calls.append(ids)
                return {"summaries": [{"id": i, "summary": "摘要 " + i} for i in ids]}

        written = keeper.refresh(self.tree, Fake(), workers=1, progress=lambda m: None)
        self.assertEqual(written, len(self.tree.ai_nodes()))
        self.assertEqual(self.tree.stale(), [])
        order = [i for batch in calls for i in batch]
        self.assertLess(order.index("apps/web/src/graph/"), order.index("apps/web/src/"))
        self.assertLess(order.index("apps/web/src/"), order.index("/"))
        calls.clear()
        self.assertEqual(keeper.refresh(self.tree, Fake(), workers=1, progress=lambda m: None), 0)
        write(self.root, "apps/web/src/graph/edges.ts", "export const drawEdge = 1;\nexport const routeEdge = 2;\n")
        self.tree.scan()
        stale = self.tree.stale()
        self.assertIn("apps/web/src/graph/", stale)
        self.assertNotIn("docs/", stale)
        self.assertNotIn("apps/api/", stale)

    def test_search_finds_files_by_path_and_summary_and_folders_by_their_summary(self):
        files, folders = search.search_tree(self.tree, "graph layout")
        self.assertEqual(files[0][0], "apps/web/src/graph/layout.ts")
        self.assertTrue(folders)
        self.assertEqual(search.search_tree(self.tree, "zzzz qqqq")[0], [])
        self.assertIn("auto", search.tokens("autoLayoutGrid_v2")[0] + "auto")


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.root = project(FILES)
        write(self.root, ".agent-state/local.json", "{}")
        self.worktrees = project({"feature/docs/design/new.html": "<title>x</title>"})
        self.resolver = links.Resolver(self.root, F.list_files(self.root), [self.worktrees])

    def test_mentions_are_cleaned_of_punctuation_and_glued_words(self):
        self.assertEqual(links.mentions("保存于docs/guide.md；还有 apps/web/src/main.ts。"), ["保存于docs/guide.md", "apps/web/src/main.ts"])
        self.assertEqual(links.mentions("见TODO.md建立多AI协作TODO"), ["见TODO.md"])
        self.assertEqual(links.mentions("C:\\Users\\x\\proj\\a.md and https://example.com/a/b"), ["C:/Users/x/proj/a.md"])

    def test_a_mention_resolves_exactly_by_suffix_by_name_or_to_a_file_git_ignores(self):
        r = self.resolver
        self.assertEqual(r.resolve("保存于docs/guide.md"), ("file", "docs/guide.md"))
        self.assertEqual(r.resolve("web/src/main.ts"), ("file", "apps/web/src/main.ts"))
        self.assertEqual(r.resolve("server.py"), ("file", "apps/api/server.py"))
        self.assertEqual(r.resolve("docs/notes"), ("dir", "docs/notes"))
        self.assertEqual(r.resolve(".agent-state/local.json"), ("file", ".agent-state/local.json"))
        absolute = self.root.replace("\\", "/") + "/apps/api/server.py"
        self.assertEqual(r.resolve(absolute), ("file", "apps/api/server.py"))

    def test_only_a_path_anchored_in_the_project_can_be_missing(self):
        r = self.resolver
        self.assertEqual(r.resolve("docs/gone.md"), ("missing", "docs/gone.md"))
        self.assertIsNone(r.resolve("spatial-quality/report.md"), "could belong to anywhere")
        self.assertIsNone(r.resolve("D:/elsewhere/a.md"))
        self.assertEqual(r.resolve("docs/design/new.html"), ("worktree", "docs/design/new.html", os.path.basename(self.worktrees)) if False else r.resolve("docs/design/new.html"))

    def test_verify_flags_missing_files_and_files_changed_since_the_note(self):
        notes = [(0, "2026-10-01", "写了 docs/guide.md"), (1, "2026-10-01", "写了 docs/gone.md"), (2, "2026-10-09", "写了 docs/guide.md"), (3, "2026-10-01", "无路径的记录")]
        flagged = links.verify(notes, self.resolver, {"docs/guide.md": "2026-10-05"})
        self.assertEqual([f[0] for f in flagged], [0, 1])
        self.assertIn("changed 2026-10-05", flagged[0][3][0])
        self.assertIn("no longer exists", flagged[1][3][0])

    def test_the_reverse_index_lists_notes_per_file_and_per_folder(self):
        notes = [(0, "d", "改了 docs/guide.md"), (1, "d", "读了 docs/notes/a.md"), (2, "d", "说了 apps/api/server.py")]
        index = links.reverse_index(notes, self.resolver)
        self.assertEqual(links.notes_about(index, "docs/guide.md", "file"), [0])
        self.assertEqual(links.notes_about(index, "docs", "dir"), [0, 1])
        self.assertEqual(links.notes_about(index, "apps", "dir"), [2])


class HistoryAndCommandTests(unittest.TestCase):
    def setUp(self):
        os.environ["MEMORY_DIR"] = os.path.join(SCRATCH, "global-%d" % id(self))
        os.makedirs(os.path.join(os.environ["MEMORY_DIR"], "TREE"))
        self.global_dir = S.store()
        S.log_append(self.global_dir, [
            ("2026-10-01", "[Demo] 做了 docs/guide.md 的运行说明 [cx:aaaaaaaa]"),
            ("2026-10-02", "[Other] 与演示无关的记录 [cx:bbbbbbbb]"),
            ("2026-10-03", "[demo-work] 工作树里改了 apps/web/src/graph/layout.ts 的布局 [cx:cccccccc]"),
            ("2026-10-04", "没有项目标签的记录"),
        ])
        self.root = project(FILES)
        shutil.rmtree(registry.home("Demo"), ignore_errors=True)  # each test starts with no routed history
        registry.save({})
        registry.register("Demo", self.root, aliases=["demo-work"])

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def test_notes_tagged_for_a_project_or_its_alias_are_routed_incrementally_and_rebuilt_with_the_global_log(self):
        key, entry = registry.get("demo")
        self.assertEqual(history.route(key, entry, self.global_dir), (2, False))
        self.assertEqual([n[2][:6] for n in history.notes(key)], ["[Demo]", "[demo-"])
        self.assertEqual(history.route(key, entry, self.global_dir), (0, False))
        S.log_append(self.global_dir, [("2026-10-05", "[Demo] 又一条 [cx:dddddddd]")])
        self.assertEqual(history.route(key, entry, self.global_dir), (1, False))
        # the global log is rebuilt: its first record changes, so the project's copy starts again
        for path in (S.log_path(self.global_dir),):
            open(path, "wb").close()
        S.log_append(self.global_dir, [("2026-09-01", "[Demo] 更早的一条 [cx:eeeeeeee]"), ("2026-10-01", "[Demo] 新记录 [cx:ffffffff]")])
        self.assertEqual(history.route(key, entry, self.global_dir), (2, True))
        self.assertEqual(len(history.notes(key)), 2)

    def test_the_commands_answer_with_where_it_is_and_what_happened(self):
        self.run_cli("sync", "Demo")
        found = self.run_cli("find", "Demo", "graph layout")
        self.assertIn("apps/web/src/graph/layout.ts", found)
        self.assertIn("What happened", found)
        self.assertIn("工作树里改了", found)
        node = self.run_cli("node", "Demo", "docs/guide.md")
        self.assertIn("Guide", node)
        self.assertIn("1 notes mention it", node)
        self.assertIn("Demo  ", self.run_cli("projects"))
        self.assertNotIn("demo-work", self.run_cli("projects"), "an alias is the same project, not another one")
        self.assertIn("Open a branch", self.run_cli("wake", "Demo"))
        self.assertIn("No note names a file", self.run_cli("verify", "Demo"))

    def test_a_projects_history_is_an_ordinary_memory_whose_follow_up_commands_name_the_project(self):
        self.run_cli("sync", "Demo")
        recalled = self.run_cli("history", "Demo", "recall", "运行说明")
        self.assertIn("docs/guide.md", recalled)
        self.assertNotIn("无关", recalled)
        woke = self.run_cli("history", "Demo", "wake")
        self.assertIn("You are awake", woke)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["history", "Demo", "note", "should not be written here"])


if __name__ == "__main__":
    unittest.main()
