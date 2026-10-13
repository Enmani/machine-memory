import os
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMO = os.path.join(ROOT, "memo")


def load_memo():
    sys.path.insert(0, ROOT)
    import memolib.store as core
    return core


class Machine:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="memo-test-")
        self.env = dict(os.environ, MEMORY_DIR=os.path.join(self.dir, "memory"), PYTHONIOENCODING="utf-8")

    def run(self, *args, ok=True):
        done = subprocess.run([sys.executable, MEMO, *args], capture_output=True, env=self.env, encoding="utf-8")
        if ok:
            self.assertion = done
            assert done.returncode == 0, (args, done.stdout, done.stderr)
        return done


class CoverTests(unittest.TestCase):
    def setUp(self):
        self.memo = load_memo()

    def test_cover_tiles_the_log_with_aligned_blocks_and_respects_the_budget(self):
        for T in (1, 2, 3, 7, 16, 100, 1000, 12345):
            for budget in (4, 20, 96):
                blocks = self.memo.cover(T, budget)
                self.assertEqual(blocks[0][0], 0)
                self.assertEqual(blocks[-1][1], T)
                for (a, b), (c, d) in zip(blocks, blocks[1:]):
                    self.assertEqual(b, c)
                for lo, hi in blocks:
                    size = hi - lo
                    self.assertEqual(size & (size - 1), 0, "power of two")
                    self.assertEqual(lo % size, 0, "aligned")
                self.assertLessEqual(len(blocks), max(budget, len(self.memo.tile(T))))

    def test_everything_fits_means_nothing_is_compressed_and_recent_memories_are_finest(self):
        self.assertEqual(self.memo.cover(10, 96), [(i, i + 1) for i in range(10)])
        blocks = self.memo.cover(5000, 96)
        sizes = [hi - lo for lo, hi in blocks]
        self.assertEqual(sizes[-1], 1)
        self.assertGreater(max(sizes[:5]), max(sizes[-5:]), "old memories collapse more than recent ones")
        self.assertEqual(self.memo.cover(0, 96), [])

    def test_block_ids_are_validated(self):
        self.assertEqual(self.memo.block_id("16-31"), (16, 32))
        for bad in ("4-5x", "5-6", "0-2", "3-3", "abc"):
            with self.assertRaises(SystemExit):
                self.memo.block_id(bad)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.machine = Machine()
        self.machine.run("init")

    def run_memo(self, *args, ok=True):
        return self.machine.run(*args, ok=ok)

    def test_a_missing_memory_is_an_error_and_init_creates_it_once(self):
        other = Machine()
        done = other.run("wake", ok=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("init", done.stderr)
        self.assertFalse(os.path.exists(other.env["MEMORY_DIR"]))
        self.assertIn("Found", self.run_memo("init").stdout)

    def test_note_stores_fixed_width_records_and_wake_prints_them(self):
        self.run_memo("note", "第一条：项目 phd-research 加了 PDF 阅读")
        self.run_memo("note", "second memory")
        log = os.path.join(self.machine.env["MEMORY_DIR"], "LOG.txt")
        self.assertEqual(os.path.getsize(log), 2 * 320)
        woke = self.run_memo("wake").stdout
        self.assertIn("#0 ", woke)
        self.assertIn("phd-research", woke)
        self.assertIn("You are awake.", woke)
        self.assertIn("#0 ", self.run_memo("recall", "PDF").stdout)

    def test_note_rejects_empty_multiline_and_oversized_lines(self):
        for bad in ("", "two\nlines", "x" * 281, "界" * 94):
            self.assertNotEqual(self.run_memo("note", bad, ok=False).returncode, 0, repr(bad[:10]))
        self.assertEqual(self.run_memo("note", "x" * 280).returncode, 0)
        self.assertEqual(self.run_memo("note", "界" * 93).returncode, 0)

    def test_compressions_arrive_in_order_and_wake_asks_for_one_it_needs(self):
        for i in range(2):
            out = self.run_memo("note", "memory %d" % i).stdout
        self.assertIn("Compress memories #0-1", out)
        wrong = self.run_memo("nap", "2-3", "nope", ok=False)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("saved", self.run_memo("nap", "0-1", "summary of 0 and 1").stdout)
        self.assertIn("Nothing left", self.run_memo("nap").stdout)
        # a wake budget of 2 over 4 memories needs the summaries
        for i in range(2, 6):
            self.run_memo("note", "memory %d" % i)
        self.run_memo("config", "WAKE_LINES=2")
        blocked = self.run_memo("wake", ok=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("Cannot wake", blocked.stdout)
        self.assertIn("nap", blocked.stdout)

    def test_zoom_opens_a_block_into_halves_down_to_raw_memories(self):
        for i in range(4):
            self.run_memo("note", "memory %d" % i)
        self.run_memo("nap", "0-1", "S01")
        self.run_memo("nap", "2-3", "S23")
        self.run_memo("nap", "0-3", "S0123")
        top = self.run_memo("zoom", "0-3").stdout
        self.assertIn("#0-1 S01", top)
        self.assertIn("#2-3 S23", top)
        bottom = self.run_memo("zoom", "0-1").stdout
        self.assertIn("#0 ", bottom)
        self.assertIn("#1 ", bottom)

    def test_forget_drops_a_summary_and_everything_built_on_it(self):
        for i in range(4):
            self.run_memo("note", "memory %d" % i)
        for block, text in (("0-1", "a"), ("2-3", "b"), ("0-3", "c")):
            self.run_memo("nap", block, text)
        done = self.run_memo("forget", "0-1").stdout
        self.assertIn("Forgot 3 summaries", done)
        self.assertIn("not compressed yet", self.run_memo("zoom", "0-3").stdout)
        self.assertEqual(self.run_memo("recall", "memory").stdout.count("#"), 4, "the log is untouched")

    def test_import_is_strict_about_dates_and_loads_history_in_one_go(self):
        path = os.path.join(self.machine.dir, "history.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("2026-01-02 老记忆一\n2026-01-03 old memory two\n")
        self.assertIn("Imported 2 memories", self.run_memo("import", path).stdout)
        with open(path, "w", encoding="utf-8") as f:
            f.write("2026-01-01 earlier than the log\n")
        self.assertNotEqual(self.run_memo("import", path, ok=False).returncode, 0)
        self.assertIn("#0 2026-01-02 老记忆一", self.run_memo("wake").stdout)

    def test_recall_is_a_case_insensitive_regex_over_every_memory(self):
        self.run_memo("note", "Archist 的 keeper 控制台改了")
        self.run_memo("note", "unrelated")
        found = self.run_memo("recall", "archist.*keeper").stdout
        self.assertIn("keeper", found)
        self.assertNotIn("unrelated", found)
        self.assertIn("No match", self.run_memo("recall", "zzz").stdout)
        self.assertNotEqual(self.run_memo("recall", "(", ok=False).returncode, 0)

    def test_a_crash_that_left_a_partial_record_does_not_misalign_later_memories(self):
        self.run_memo("note", "before the crash")
        log = os.path.join(self.machine.env["MEMORY_DIR"], "LOG.txt")
        with open(log, "ab") as f:
            f.write(b"#1 2026-01-01 half a rec")  # never acknowledged
        self.run_memo("note", "after the crash")
        self.assertEqual(os.path.getsize(log), 2 * 320)
        self.assertIn("#1 ", self.run_memo("recall", "after the crash").stdout)

    def test_config_changes_what_wake_prints_without_touching_memories(self):
        for i in range(6):
            self.run_memo("note", "m%d" % i)
        self.run_memo("config", "WAKE_LINES=300")
        self.assertIn("300", self.run_memo("config").stdout)
        self.assertNotEqual(self.run_memo("config", "WAKE_LINES=abc", ok=False).returncode, 0)
        self.assertNotEqual(self.run_memo("config", "ENTRY_CHARS=999", ok=False).returncode, 0)
        self.run_memo("config", "WAKE_LINES=")
        self.assertIn("96", self.run_memo("config").stdout)

    def test_parallel_sessions_never_share_an_id(self):
        errors = []

        def write(n):
            done = self.machine.run("note", "parallel %d" % n, ok=False)
            if done.returncode:
                errors.append(done.stderr)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        module = load_memo()
        os.environ["MEMORY_DIR"] = self.machine.env["MEMORY_DIR"]
        ids = [entry[0] for entry in module.log_scan(module.store())]
        self.assertEqual(ids, list(range(12)))


if __name__ == "__main__":
    unittest.main()
