import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("memory_installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memo-install-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "optmem"
        self.instructions = {}
        self.skills = {}
        for agent in installer.TARGETS:
            directory = self.root / agent
            directory.mkdir()
            self.instructions[agent] = str(directory / "AGENTS.md")
            self.skills[agent] = str(directory / "skills/machine-memory")
        # A shared native discovery directory, as used by Codex and Claude here.
        self.skills["Claude Code"] = self.skills["Codex"]
        self.integration = self.root / "source"
        self.integration.mkdir()
        for name in ("memo", "install.py", "ptree.py", "harvest.py"):
            (self.integration / name).write_bytes((ROOT / name).read_bytes())
        import shutil
        for name in ("memolib", "skills"):
            shutil.copytree(ROOT / name, self.integration / name, ignore=shutil.ignore_patterns("__pycache__"))
        self.patchers = [patch.object(installer, "HERE", str(self.integration)),
                         patch.object(installer, "HOME", str(self.home)),
                         patch.object(installer, "MEMO", str(self.home / "memo")),
                         patch.object(installer, "TARGETS", self.instructions),
                         patch.object(installer, "SKILL_TARGETS", self.skills),
                         patch.dict(os.environ, MEMORY_DIR=str(self.home / "memory"),
                                    PAPER_TREE_CLI=str(self.root / "missing-paper-tree.py"))]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_installed_skill_has_resolvable_references_and_correct_local_paths(self):
        installer.install_tool()
        master = self.home / "skills/machine-memory"
        for folder in (master, *(Path(p) for p in set(self.skills.values()))):
            self.assertEqual((folder / "SKILL.md").read_bytes(), (ROOT / "skills/machine-memory/SKILL.md").read_bytes())
            for reference in ("commands.md", "trees.md", "local-paths.md"):
                self.assertTrue((folder / "references" / reference).is_file())
            paths = (folder / "references/local-paths.md").read_text(encoding="utf-8")
            self.assertIn(installer.forward(self.home / "memo"), paths)
            self.assertIn(installer.forward(self.integration / "ptree.py"), paths)
            self.assertNotIn("missing-paper-tree.py", paths)
        for path in (self.home / "memo", self.integration / "ptree.py", self.integration / "harvest.py"):
            self.assertTrue(path.is_file())

    def test_managed_block_is_small_and_keeps_the_user_instructions(self):
        block = installer.install_tool()
        self.assertLessEqual(len(block.splitlines()), 8)
        self.assertIn(installer.forward(self.home / "skills/machine-memory/SKILL.md"), block)
        outside = "User requirement: preserve every character.\r\n中文要求。\r\n"
        originals = {}
        for path in self.instructions.values():
            original = "prefix\r\n" + installer.BEGIN + "\r\nold large block\r\n" + installer.END + "\r\n" + outside
            Path(path).write_bytes(original.encode("utf-8"))
            originals[path] = original
        installer.edit_instructions(block)
        for path, original in originals.items():
            updated = Path(path).read_bytes().decode("utf-8")
            self.assertEqual(installer.without_block(updated), installer.without_block(original))
            self.assertIn(outside, updated)
            self.assertEqual(updated.count(installer.BEGIN), 1)

    def test_reinstall_does_not_change_memory_or_register_a_task(self):
        block = installer.install_tool()
        memory = self.home / "memory"
        from memolib import store as S
        S.log_append(str(memory), [("2026-01-01", "[p] preserved note"), ("2026-01-02", "[p] another note")])
        S.tree_put(str(memory), 0, 2, "preserved summary")
        before = {p.relative_to(memory): p.read_bytes() for p in memory.rglob("*") if p.is_file()}
        with patch.object(installer, "schedule", side_effect=AssertionError("unexpected task registration")):
            self.assertEqual(installer.install_tool(), block)
        after = {p.relative_to(memory): p.read_bytes() for p in memory.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
