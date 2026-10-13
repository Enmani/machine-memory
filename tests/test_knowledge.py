import os
import argparse
import contextlib
import io
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from memolib import store as S
from memolib.knowledge import Catalog
from memolib.review import review
from memolib.semantic import search, SearchError


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memo-knowledge-test-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = os.path.join(self.tmp.name, "memory")
        os.makedirs(os.path.join(self.directory, "TREE"))
        open(S.log_path(self.directory), "wb").close()

    def catalog(self, texts):
        S.log_append(self.directory, [("2026-01-01", text) for text in texts])
        catalog = Catalog(self.directory)
        self.addCleanup(catalog.db.close)
        with catalog.transaction():
            pass
        return catalog

    def test_cross_thread_exact_duplicates_keep_all_sources_without_another_log_entry(self):
        c = self.catalog(["[p] 决定采用 SQLite [cx:aaaaaaaa]"])
        before = open(S.log_path(self.directory), "rb").read()
        self.assertEqual(c.append([("2026-02-01", "[p] 决定采用 SQLite [cc:bbbbbbbb]")]), [(0, False)])
        self.assertEqual(open(S.log_path(self.directory), "rb").read(), before)
        self.assertEqual({s["text"] for s in c.sources(c.note(0)["text"])},
                         {"[p] 决定采用 SQLite [cx:aaaaaaaa]", "[p] 决定采用 SQLite [cc:bbbbbbbb]"})
        self.assertTrue(os.path.exists(self.directory + ".knowledge.sqlite3"))
        self.assertEqual(c.append([("2026-02-01", "[p] 决定采用 SQLite [cc:bbbbbbbb]")]), [(0, False)])
        self.assertEqual(len(c.sources(c.note(0)["text"])), 2)

    def test_different_projects_paths_numbers_and_outcomes_are_not_exact_duplicates(self):
        c = self.catalog(["[p] 通过 7 项，保存 docs/A.md"])
        for text in ("[q] 通过 7 项，保存 docs/A.md", "[p] 通过 8 项，保存 docs/A.md",
                     "[p] 通过 7 项，保存 docs/B.md", "[p] 失败 7 项，保存 docs/A.md"):
            self.assertTrue(c.append([("2026-02-01", text)])[0][1])
        self.assertEqual(S.log_len(self.directory), 5)

    def test_two_writers_deduplicate_under_the_same_memory_lock(self):
        results, errors = [], []
        def write():
            try:
                with Catalog(self.directory) as c:
                    results.extend(c.append([("2026-01-01", "[p] same fact")]))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=write) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [(0, False), (0, True)])
        self.assertEqual(S.log_len(self.directory), 1)

    def test_replacement_chain_and_history_leave_log_and_tree_unchanged(self):
        c = self.catalog(["[p] 决定用 PostgreSQL", "[p] 决定改用 SQLite", "[p] 决定改用 DuckDB"])
        S.tree_put(self.directory, 0, 2, "数据库决定")
        before = [open(p, "rb").read() for p in (S.log_path(self.directory), S.tree_path(self.directory, 2))]
        with c.transaction():
            c.relate(1, 0, "replaces", "明确采用新方案")
            c.relate(2, 1, "replaces", "第二次调整")
        self.assertEqual(c.resolve(c.note(0)["text"]), c.note(2)["text"])
        self.assertEqual(c.resolve(c.note(0)["text"], history=True), c.note(0)["text"])
        self.assertEqual(before, [open(p, "rb").read() for p in (S.log_path(self.directory), S.tree_path(self.directory, 2))])

    def test_cycle_and_cross_project_replacement_are_rejected(self):
        c = self.catalog(["[p] choose A", "[p] choose B", "[q] choose C"])
        with c.transaction():
            c.relate(1, 0, "duplicate", "same fact")
            with self.assertRaisesRegex(ValueError, "cycle"):
                c.relate(1, 0, "replaces", "would produce a cycle")
            self.assertEqual(len(c.edges()), 1)
            with self.assertRaisesRegex(ValueError, "same project"):
                c.relate(2, 0, "replaces", "different project")

    def test_duplicate_components_merge_and_share_the_current_replacement(self):
        c = self.catalog(["[p] first wording", "[p] second wording", "[p] third wording", "[p] adopted new choice"])
        with c.transaction():
            c.relate(2, 0, "duplicate", "same fact")
            c.relate(2, 1, "duplicate", "same fact")
            c.relate(3, 1, "replaces", "adopted change")
        for old in range(3):
            self.assertEqual(c.resolve(c.note(old)["text"], True), c.note(0)["text"])
            self.assertEqual(c.resolve(c.note(old)["text"]), c.note(3)["text"])
        self.assertEqual(len(c.sources(c.note(0)["text"])), 3)
        self.assertIn("replaces by #3", " ".join(c.annotations(c.note(0)["text"])))

    def test_late_observation_keeps_source_date_while_the_log_stays_chronological(self):
        c = self.catalog(["[p] existing fact"])
        c.append([("2025-01-01", "[p] discovered late [cx:aaaaaaaa]")])
        self.assertEqual(c.note(1)["date"], "2026-01-01")
        self.assertIn("2025-01-01", {r["date"] for r in c.sources(c.note(1)["text"])})

    def test_log_reordering_and_redating_do_not_retarget_relationships(self):
        c = self.catalog(["[p] choose A", "[p] choose B", "[q] other"])
        with c.transaction():
            c.relate(1, 0, "replaces", "adopted B")
        open(S.log_path(self.directory), "wb").close()
        S.log_append(self.directory, [("2026-02-01", "[q] other"), ("2026-02-02", "[p] choose A"),
                                     ("2026-02-03", "[p] choose B")])
        with c.transaction():
            pass
        self.assertEqual(c.resolve(c.note(1)["text"]), c.note(2)["text"])
        self.assertIn("#2", " ".join(c.annotations(c.note(1)["text"])))

    def test_semantic_retrieval_reaches_an_old_branch_without_keyword_overlap(self):
        texts = ["[p] unrelated record %d" % i for i in range(256)]
        texts[3] = "[p] Praat continuous resynthesis"
        c = self.catalog(texts)
        S.tree_put(self.directory, 0, 128, "voice editing")
        S.tree_put(self.directory, 128, 256, "unrelated work")
        answers = iter([{"ids": ["0:128"]}, {"ids": ["0:64"]}, {"ids": ["0:32"]},
                        {"ids": ["0:16"]}, {"ids": ["3"]}])
        class Fake:
            def complete_json(self, prompt, thinking):
                return next(answers)
        # No lexical match supplies id 3. The tree/model path must discover it.
        result = search(c, Fake(), "把中文对白最后一个字拖长", max_calls=6)
        self.assertEqual([r["id"] for r in result["hits"]], [3])
        self.assertEqual(result["calls"], 5)
        self.assertFalse(result["limited"])

    def test_unknown_semantic_ids_and_partial_traversal_are_explicit(self):
        c = self.catalog(["[p] item %d" % i for i in range(128)])
        class Wrong:
            def complete_json(self, prompt, thinking):
                return {"ids": ["invented"]}
        with self.assertRaises(SearchError):
            search(c, Wrong(), "anything")
        class Budget:
            def complete_json(self, prompt, thinking):
                return {"ids": ["0:128"]}
        result = search(c, Budget(), "没有关键词", max_calls=2)
        self.assertTrue(result["limited"])
        self.assertEqual(result["hits"], [])

    def test_review_uses_evidence_and_persists_semantic_duplicates(self):
        c = self.catalog(["[p] 选择 SQLite，因为不用部署数据库服务", "[p] 数据库存本地文件，已决定用 SQLite"])
        class Fake:
            def complete_json(self, prompt, thinking):
                return {"relations": [{"newer": 1, "older": 0, "kind": "duplicate", "confidence": 0.99,
                                       "new_evidence": "已决定用 SQLite", "old_evidence": "选择 SQLite",
                                       "reason": "同一项目的同一决定，后句未增加新结果"}]}
        preview = review(c, Fake(), ids=[1])
        self.assertEqual(len(preview["relations"]), 1)
        self.assertEqual(c.edges(), [])
        review(c, Fake(), ids=[1], apply=True)
        self.assertEqual(c.resolve(c.note(1)["text"]), c.note(0)["text"])
        self.assertEqual(len(c.sources(c.note(0)["text"])), 2)
        class Forged:
            def complete_json(self, prompt, thinking):
                return {"relations": [{"newer": 1, "older": 0, "kind": "replaces", "confidence": 1,
                                       "new_evidence": "adopted PostgreSQL", "old_evidence": "选择 SQLite", "reason": "fabricated"}]}
        with self.assertRaisesRegex(ValueError, "quotes evidence"):
            review(c, Forged(), ids=[1], apply=True)
        self.assertEqual(len(c.edges()), 1)

    def test_model_failure_does_not_mark_pending_review_complete(self):
        c = self.catalog(["[p] choose A"])
        c.append([("2026-02-01", "[p] adopted B")])
        class Unavailable:
            def complete_json(self, prompt, thinking):
                raise RuntimeError("model unavailable")
        with self.assertRaisesRegex(RuntimeError, "model unavailable"):
            review(c, Unavailable(), apply=True)
        self.assertEqual([r["id"] for r in c.rows() if not r["reviewed"]], [1])

    def test_project_search_uses_current_decision_and_keeps_actual_file_checks(self):
        from ptree import registry, history
        from ptree.cli import cmd_find
        project = os.path.join(self.tmp.name, "project")
        os.makedirs(os.path.join(project, "docs"))
        with open(os.path.join(project, "docs", "current.md"), "w", encoding="utf-8") as f:
            f.write("# Current database\nSQLite deployment\n")
        # Existing changed-file checks use actual Git commit dates, not mtimes.
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-01-02T00:00:00+00:00",
                   GIT_COMMITTER_DATE="2026-01-02T00:00:00+00:00")
        for words in (("init", "-q"), ("add", "docs/current.md"),
                      ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                       "commit", "-q", "-m", "Changed current file")):
            subprocess.run(["git", *words], cwd=project, env=env, check=True, capture_output=True)
        c = self.catalog(["[demo] 决定使用 PostgreSQL，docs/old.md", "[demo] 正式改用 SQLite，docs/current.md"])
        with c.transaction():
            c.relate(1, 0, "replaces", "采用新的数据库")
        output = io.StringIO()
        with patch.dict(os.environ, MEMORY_DIR=self.directory), \
                patch.object(registry, "HOME", os.path.join(self.tmp.name, "projects")), \
                contextlib.redirect_stdout(output):
            entry = registry.register("demo", project)
            history.route("demo", entry, self.directory)
            cmd_find(argparse.Namespace(project="demo", question="PostgreSQL", notes=8, semantic=False, model=""))
        rendered = output.getvalue()
        self.assertIn("#1 2026-01-01 [demo] 正式改用 SQLite", rendered)
        self.assertIn("replaces #0", rendered)
        self.assertIn("! ", rendered, "current file modification must still be reported")


if __name__ == "__main__":
    unittest.main()
