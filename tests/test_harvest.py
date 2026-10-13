import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harvest import ledger as ledger_module  # noqa: E402
from harvest.distill import fit_note, parse_notes, distill_thread, segments, chunks, render  # noqa: E402
from memolib.engine import parse_json, EngineError  # noqa: E402
from harvest.links import Links, NodeLinks, PaperLinks  # noqa: E402
from harvest.memory_io import append_notes, import_staged, load_memo, stage  # noqa: E402
from harvest.model import Thread, Turn  # noqa: E402
from harvest.napper import nap_all  # noqa: E402
from harvest.project import project_of  # noqa: E402
from memolib.redact import redact  # noqa: E402
from harvest.sources import ClaudeCodeAdapter, CodexAdapter, OmpAdapter  # noqa: E402


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def tmp():
    return tempfile.mkdtemp(prefix="harvest-test-")


def codex_message(role, text, at, phase=None, kind="input_text"):
    payload = {"type": "message", "id": "m", "role": role, "content": [{"type": kind, "text": text}]}
    if phase:
        payload["phase"] = phase
    return {"timestamp": at, "ordinal": 1, "type": "response_item", "payload": payload}


class AdapterTests(unittest.TestCase):
    def test_codex_keeps_only_what_was_said_and_flags_subagents(self):
        home = tmp()
        meta = lambda tid, extra=None: {"timestamp": "2026-05-31T10:00:00Z", "type": "session_meta", "payload": dict({"id": tid, "cwd": "C:\\Users\\di\\Projects\\Archist", "timestamp": "2026-05-31T10:00:00Z", "source": "vscode"}, **(extra or {}))}
        write_jsonl(os.path.join(home, ".codex", "sessions", "2026", "05", "31", "rollout-2026-05-31T10-00-00-aaaaaaaa-1111.jsonl"), [
            meta("aaaaaaaa-1111"),
            {"timestamp": "t", "type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "system stuff"}]}},
            codex_message("user", "<environment_context>\n<cwd>x</cwd>\n</environment_context>", "2026-05-31T10:00:01Z"),
            codex_message("user", "# AGENTS.md instructions for x\n<INSTRUCTIONS>be nice</INSTRUCTIONS>", "2026-05-31T10:00:01Z"),
            codex_message("user", "帮我给研究地图加一个 PDF 阅读功能", "2026-05-31T10:00:02Z"),
            {"timestamp": "t", "type": "compacted", "payload": {"replacement_history": ["x" * 10]}},
            {"timestamp": "t", "type": "response_item", "payload": {"type": "custom_tool_call_output", "output": "tool noise"}},
            {"timestamp": "t", "type": "response_item", "payload": {"type": "reasoning", "summary": []}},
            codex_message("assistant", "我先看一下结构。", "2026-05-31T10:00:05Z", "commentary", "output_text"),
            codex_message("assistant", "已加好：标签页里用 pdf.js 阅读。", "2026-05-31T10:05:00Z", "final_answer", "output_text"),
        ])
        write_jsonl(os.path.join(home, ".codex", "sessions", "2026", "05", "31", "rollout-2026-05-31T11-00-00-bbbbbbbb-2222.jsonl"), [
            meta("bbbbbbbb-2222", {"parent_thread_id": "aaaaaaaa-1111", "source": {"subagent": {"thread_spawn": {}}}}),
            codex_message("user", "subagent task text", "2026-05-31T11:00:01Z"),
        ])
        adapter = CodexAdapter(home)
        paths = adapter.discover()
        self.assertEqual(len(paths), 2)
        main, sub = adapter.load(paths[0])[0], adapter.load(paths[1])[0]
        self.assertEqual([t.role for t in main.turns], ["user", "assistant", "assistant"])
        self.assertEqual(main.turns[0].text, "帮我给研究地图加一个 PDF 阅读功能")
        self.assertEqual([t.final for t in main.turns], [False, False, True])
        self.assertFalse(main.subagent)
        self.assertTrue(sub.subagent)
        self.assertEqual(sub.parent, "aaaaaaaa-1111")
        self.assertEqual(main.ref, "cx:aaaaaaaa")
        self.assertEqual(main.cwd, "C:\\Users\\di\\Projects\\Archist")

    def test_claude_code_drops_thinking_tools_sidechains_and_resumed_duplicates(self):
        home = tmp()
        entry = lambda uuid, kind, content, **extra: dict({"uuid": uuid, "type": kind, "isSidechain": False, "timestamp": "2026-09-30T20:01:03Z", "cwd": "C:\\work", "message": {"role": kind, "content": content}}, **extra)
        first = os.path.join(home, ".claude", "projects", "p", "s1.jsonl")
        write_jsonl(first, [
            {"type": "custom-title", "customTitle": "Rhino建模链接使用", "sessionId": "s1"},
            entry("u1", "user", [{"type": "text", "text": "<system-reminder>ignore</system-reminder>"}, {"type": "text", "text": "你可以链接并使用rhino建模吗"}]),
            entry("a1", "assistant", [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "可以，用 MCP。"}, {"type": "tool_use", "name": "Bash", "input": {}}]),
            entry("u2", "user", [{"type": "tool_result", "content": "tool output"}]),
            entry("s1", "assistant", [{"type": "text", "text": "subagent words"}], isSidechain=True),
            entry("u3", "user", "上网搜下方法", origin={"kind": "human"}),
            entry("m1", "user", "meta", isMeta=True),
            entry("h1", "user", "from a hook", origin={"kind": "hook"}),
        ])
        second = os.path.join(home, ".claude", "projects", "p", "s2.jsonl")
        write_jsonl(second, [entry("u1", "user", "你可以链接并使用rhino建模吗"), entry("u9", "user", "新的一句话")])
        os.utime(first, (1, 1))
        adapter = ClaudeCodeAdapter(home)
        threads = [t for path in adapter.discover() for t in adapter.load(path)]
        self.assertEqual(len(threads), 2)
        self.assertEqual([t.text for t in threads[0].turns], ["你可以链接并使用rhino建模吗", "可以，用 MCP。", "上网搜下方法"])
        self.assertEqual(threads[0].title, "Rhino建模链接使用")
        self.assertEqual([t.text for t in threads[1].turns], ["新的一句话"], "a resumed session does not repeat what it resumed")

    def test_omp_reads_messages_and_session_header(self):
        home = tmp()
        write_jsonl(os.path.join(home, ".omp", "agent", "sessions", "x", "2026.jsonl"), [
            {"type": "session", "id": "01a1029d", "cwd": "D:\\Documents", "timestamp": "2026-10-03T16:34:11Z", "title": "Update OMP"},
            {"type": "message", "timestamp": "2026-10-03T16:34:21Z", "message": {"role": "user", "content": [{"type": "text", "text": "omp update"}]}},
            {"type": "message", "timestamp": "2026-10-03T16:34:30Z", "message": {"role": "toolResult", "content": [{"type": "text", "text": "noise"}]}},
            {"type": "message", "timestamp": "2026-10-03T16:34:40Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "已更新"}]}},
        ])
        adapter = OmpAdapter(home)
        thread = adapter.load(adapter.discover()[0])[0]
        self.assertEqual((thread.id, thread.title, [t.role for t in thread.turns]), ("01a1029d", "Update OMP", ["user", "assistant"]))


class TextTests(unittest.TestCase):
    def test_secrets_are_scrubbed_before_text_leaves_the_machine(self):
        text = "key sk-abcdefghijklmnopqrstuvwx and ghp_abcdefghijklmnopqrstuvwx, password: hunter2hunter, Authorization: Bearer abcdefghijklmnopqrstuv, " + "a1B2" * 15
        out = redact(text)
        for leaked in ("sk-abcdef", "ghp_abcdef", "hunter2hunter", "abcdefghijklmnopqrstuv", "a1B2" * 15):
            self.assertNotIn(leaked, out)
        self.assertIn("key", out)
        self.assertEqual(redact("docs/research-map/deliverables/library-index.json"), "docs/research-map/deliverables/library-index.json")
        slug = "docs/ai_context/tasks/model3d-provider-capability-inventory-governance/"
        self.assertEqual(redact(slug), slug, "a long folder name is not a secret")

    def test_projects_come_from_the_folder_a_thread_started_in(self):
        self.assertEqual(project_of("C:\\Users\\di\\Projects\\Archist"), "Archist")
        self.assertEqual(project_of("C:\\Users\\di\\Projects\\Archist-work\\keeper-console"), "Archist")
        self.assertEqual(project_of("C:\\Users\\di\\Documents\\Codex\\2026-09-08\\zu-x\\outputs\\求职"), "zu-x")
        self.assertEqual(project_of("C:\\Users\\di\\AppData\\Roaming\\Claude\\scratch-workspaces\\e3\\49\\scratch-2026"), "claude-chat")
        self.assertEqual(project_of(""), "general")

    def test_model_replies_are_parsed_out_of_prose_and_fences(self):
        self.assertEqual(parse_json('ok\n```json\n{"a":{"b":"}"}}\n```'), {"a": {"b": "}"}})
        with self.assertRaises(EngineError):
            parse_json("no json")
        with self.assertRaises(EngineError):
            parse_json('{"a": [1,')


class NoteTests(unittest.TestCase):
    def test_a_note_is_headed_by_a_project_keeps_only_offered_pointers_and_ends_with_its_reference(self):
        line = fit_note("研究地图加了 PDF 阅读，高亮存 JSON paper:12 paper:99 node:K3", "phd-research", "cx:019e7e85", {"paper:12", "node:K3"})
        self.assertTrue(line.startswith("[phd-research] "))
        self.assertIn("paper:12", line)
        self.assertNotIn("paper:99", line)
        self.assertTrue(line.endswith("[cx:019e7e85]"))
        kept = fit_note("[Archist] 已经有项目头的条目", "x", "cc:abcd1234", set())
        self.assertTrue(kept.startswith("[Archist] 已经"))

    def test_an_overlong_note_is_cut_on_a_character_boundary_and_still_fits_the_record(self):
        line = fit_note("[p] " + "界" * 200 + " paper:7", "p", "cx:019e7e85", {"paper:7"})
        self.assertLessEqual(len(line.encode("utf-8")), 280)
        self.assertTrue(line.endswith("paper:7 [cx:019e7e85]"))
        line.encode("utf-8").decode("utf-8")

    def test_dates_are_kept_inside_the_thread_and_duplicates_dropped(self):
        thread = Thread(source="codex", id="aaaaaaaa-1", path="", started="2026-05-31T10:00:00Z", ended="2026-06-02T10:00:00Z")
        notes = parse_notes({"notes": [
            {"date": "2026-01-01", "text": "[p] early"}, {"date": "bogus", "text": "[p] odd"},
            {"date": "2026-06-01", "text": "[p] fine"}, {"date": "2026-06-01", "text": "[p] fine"}, {"date": "2027-01-01", "text": "[p] late"}, {"text": ""}]},
            thread, "p", set(), 10)
        self.assertEqual([d for d, _ in notes], ["2026-05-31", "2026-05-31", "2026-06-01", "2026-06-02"])
        self.assertEqual(len(notes), 4)

    def test_segments_pair_each_prompt_with_the_answer_that_closed_it(self):
        turns = [Turn("user", "做个功能", "2026-05-31T10:00:00Z"), Turn("assistant", "我先看看", "2026-05-31T10:00:01Z"),
                 Turn("assistant", "做好了", "2026-05-31T10:00:09Z", final=True), Turn("assistant", "补充一句", "2026-05-31T10:00:10Z")]
        pairs = segments(turns)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][1].text, "做好了")
        self.assertEqual(len(chunks(["x" * 30] * 5, budget=70)), 3)

    def test_distilling_a_thread_asks_the_model_once_and_validates_what_comes_back(self):
        class Fake:
            calls = 0

            def complete_json(self, prompt, thinking):
                Fake.calls += 1
                assert "paper:12" in prompt and "这是被整理的" not in prompt
                return {"notes": [{"date": "2026-05-31", "text": "[phd-research] 读了 CANTO 并加了阅读标签页 paper:12 paper:77"}]}

        class FakeLinks:
            def candidates(self, text):
                return [("paper:12", "CANTO")]

        thread = Thread(source="codex", id="aaaaaaaa-1", path="", cwd="C:\\Users\\di\\Projects\\phd-research", started="2026-05-31T10:00:00Z", ended="2026-05-31T11:00:00Z")
        turns = [Turn("user", "读一下 CANTO 这篇论文，并给研究地图的论文详情面板加一个可以阅读 PDF 和划重点的标签页，高亮要能保存下来", "2026-05-31T10:00:00Z"), Turn("assistant", "做好了", "2026-05-31T10:30:00Z", final=True)]
        notes = distill_thread(thread, turns, "phd-research", ["phd-research"], Fake(), FakeLinks())
        self.assertEqual(Fake.calls, 1)
        self.assertEqual(len(notes), 1)
        self.assertIn("paper:12", notes[0][1])
        self.assertNotIn("paper:77", notes[0][1])
        self.assertEqual(distill_thread(thread, [Turn("user", "嗯", "x")], "p", [], Fake(), FakeLinks()), [])


class StateTests(unittest.TestCase):
    def test_only_one_sync_runs_at_a_time(self):
        root = tmp()
        first, second = ledger_module.SyncLock(root), ledger_module.SyncLock(root)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire(), "a scheduled run must not start while the last is still working")
        first.release()
        second.release()
        third = ledger_module.SyncLock(root)
        self.assertTrue(third.acquire(), "the lock is free again once the first run ends")
        third.release()

    def test_a_fork_does_not_repeat_the_turns_it_started_from(self):
        root = tmp()
        seen = ledger_module.Seen(root)
        long_prompt = "请帮我把这个项目的构建脚本改成并行执行，并且保留原来的缓存目录结构不变，最后给我一份变更说明。" * 2
        original = Thread(source="codex", id="orig", path="", turns=[Turn("user", long_prompt, "t"), Turn("assistant", "好的" * 40, "t")])
        fork = Thread(source="codex", id="fork", path="", turns=[Turn("user", long_prompt, "t"), Turn("assistant", "好的" * 40, "t"), Turn("user", "再加一个测试" * 20, "t")])
        self.assertEqual(len(seen.fresh(original)), 2)
        self.assertEqual([t.text[:6] for t in seen.fresh(fork)], ["再加一个测试"])
        self.assertEqual(len(seen.fresh(original)), 2, "the original keeps its own turns when it is read again")
        seen.save()
        self.assertEqual(len(ledger_module.Seen(root).owner), len(seen.owner))

    def test_links_find_papers_and_nodes_by_identifier_and_title_never_by_guess(self):
        root = tmp()
        db = os.path.join(root, "index.sqlite")
        conn = sqlite3.connect(db)
        conn.execute("create table identities (key text, paper_id integer)")
        conn.execute("create table papers (id integer, title text, active integer)")
        conn.executemany("insert into identities values (?,?)", [("doi:10.48550/arxiv.2609.36806", 1), ("doi:10.1007/xyz", 2)])
        conn.executemany("insert into papers values (?,?,1)", [(1, "CANTO: CAD-Native Transformer Operator for AI-Aided Engineering"), (2, "A Different Paper About Floor Plan Generation")])
        conn.commit()
        conn.close()
        papers = PaperLinks(db)
        self.assertEqual(papers.find("see arXiv 2609.36806v2 for details"), [1])
        self.assertEqual(papers.find("the paper a different paper about floor plan generation is useful"), [2])
        self.assertEqual(papers.find("version 1234.5678 of nothing"), [])
        page = os.path.join(root, "docs")
        os.makedirs(page)
        open(os.path.join(page, "RESEARCH-MAP.html"), "w", encoding="utf-8").write('x\n    const nodes = [{"id":"K3","title":"空间关系的表示比较"},{"id":"A","title":"短"}];\n    const edges = [];\n')
        nodes = NodeLinks(root)
        self.assertEqual(nodes.find("我们比较了 空间关系的表示比较 的几种做法，空间关系的表示比较 很重要"), ["K3"])
        self.assertEqual(nodes.find("a plain A letter"), [])
        links = Links.__new__(Links)
        links.papers, links.nodes = papers, nodes
        self.assertEqual(links.candidates("arXiv 2609.36806")[0][0], "paper:1")


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.root = tmp()
        os.environ["MEMORY_DIR"] = os.path.join(self.root, "memory")
        os.environ["HARVEST_DIR"] = os.path.join(self.root, "harvest")
        os.makedirs(os.path.join(os.environ["MEMORY_DIR"], "TREE"))
        self.memo = load_memo()

    def test_staged_notes_are_appended_once_in_date_order_and_old_ones_take_the_log_date(self):
        import harvest.memory_io as io
        io.STAGING, io.IMPORTED = os.path.join(self.root, "stg"), os.path.join(self.root, "imp")
        a = Thread(source="codex", id="aaaaaaaa-1", path="", started="2026-02-01T00:00:00Z")
        b = Thread(source="claude", id="bbbbbbbb-2", path="", started="2026-01-01T00:00:00Z")
        stage(a, [("2026-02-03", "[x] later thing")], 4)
        stage(b, [("2026-01-05", "[y] earlier thing")], 2)
        first, count = import_staged(self.memo)
        self.assertEqual((first, count), (0, 2))
        d = self.memo.store()
        self.assertEqual([e[2] for e in self.memo.log_scan(d)], ["[y] earlier thing", "[x] later thing"])
        self.assertEqual(os.listdir(io.STAGING), [])
        stage(a, [("2026-01-01", "[z] discovered late")], 6)
        import_staged(self.memo)
        self.assertEqual(self.memo.log_get(d, 2)[1], "2026-02-03", "the log stays chronological")
        self.assertEqual(append_notes(self.memo, [("2026-03-01", "x" * 400)]), (None, 0), "an oversized note is not written")

    def test_bulk_compression_writes_every_pending_block_in_order(self):
        d = self.memo.store()
        self.memo.log_append(d, [("2026-01-%02d" % (i % 28 + 1), "[p] memory %d about topic %d" % (i, i % 5)) for i in range(21)])

        class Fake:
            def complete_json(self, prompt, thinking):
                import re
                ids = re.findall(r"### #(\d+-\d+)", prompt)
                return {"summaries": [{"id": i, "text": "摘要 " + i} for i in ids]}

        nap_all(self.memo, Fake(), workers=2, batch=3, progress=lambda m: None)
        self.assertEqual(self.memo.pending_count(d, 21), 0)
        self.assertEqual(self.memo.tree_get(d, 0, 2), "摘要 0-1")
        self.assertEqual(self.memo.tree_get(d, 16, 32) if 32 <= 21 else self.memo.tree_get(d, 0, 16), "摘要 0-15")

    def test_a_model_that_forgets_a_block_leaves_a_dense_prefix_and_is_retried(self):
        d = self.memo.store()
        self.memo.log_append(d, [("2026-01-01", "[p] memory %d" % i) for i in range(8)])
        state = {"round": 0}

        class Flaky:
            def complete_json(self, prompt, thinking):
                import re
                state["round"] += 1
                ids = re.findall(r"### #(\d+-\d+)", prompt)
                keep = ids if state["round"] > 1 else ids[1:]  # the first call forgets the first block it was given
                return {"summaries": [{"id": i, "text": "S " + i} for i in keep]}

        nap_all(self.memo, Flaky(), workers=1, batch=8, progress=lambda m: None)
        self.assertEqual(self.memo.pending_count(d, 8), 0)


if __name__ == "__main__":
    unittest.main()
