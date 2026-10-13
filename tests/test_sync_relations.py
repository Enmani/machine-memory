import argparse
import os
import tempfile
import unittest
from unittest.mock import patch

from memolib import store as S
from memolib.knowledge import Catalog
from harvest.cli import cmd_sync


class SyncTests(unittest.TestCase):
    def test_failed_relation_review_preserves_queue_and_still_refreshes_projects(self):
        with tempfile.TemporaryDirectory(prefix="memo-sync-test-") as root:
            directory = os.path.join(root, "memory")
            os.makedirs(os.path.join(directory, "TREE"))
            open(S.log_path(directory), "wb").close()
            with Catalog(directory) as catalog:
                catalog.append([("2026-01-01", "[p] new decision")])
            args = argparse.Namespace(log=False, no_nap=True, no_projects=False,
                                      no_relations=False, dry_run=False, quiet=3, workers=1, model="")
            with patch.dict(os.environ, MEMORY_DIR=directory), \
                    patch("harvest.cli.process"), \
                    patch("harvest.cli.import_staged", return_value=(None, 0)), \
                    patch("harvest.cli.make_engine", side_effect=RuntimeError("provider unavailable")), \
                    patch("harvest.cli.ledger_module.SyncLock") as lock, \
                    patch("ptree.cli.sync_all") as projects:
                lock.return_value.acquire.return_value = True
                with self.assertRaisesRegex(SystemExit, "review failed"):
                    cmd_sync(args)
                projects.assert_called_once()
                lock.return_value.release.assert_called_once()
            with Catalog(directory) as catalog:
                with catalog.transaction():
                    pass
                self.assertEqual([r["id"] for r in catalog.rows() if not r["reviewed"]], [0])


if __name__ == "__main__":
    unittest.main()
