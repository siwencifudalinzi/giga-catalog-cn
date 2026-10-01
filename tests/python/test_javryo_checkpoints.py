import json
import tempfile
import unittest
from pathlib import Path

from src.giga_catalog.javryo_checkpoints import load_checkpoint, save_checkpoint


class DurableCheckpointTests(unittest.TestCase):
    def test_deleted_workspace_resumes_from_durable_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            primary, backup = Path(directory) / "local.json", Path(directory) / "durable.json"
            state = {"schemaVersion": 1, "updatedAt": "2026-10-01T00:00:00Z",
                     "results": {"ATHB-16": {"playbackStatus": "verified", "attempts": 1}}}
            save_checkpoint(primary, backup, state)
            primary.unlink()
            self.assertEqual(load_checkpoint(primary, backup), state)
            self.assertEqual(json.loads(primary.read_text(encoding="utf-8")), state)

    def test_corrupt_or_stale_workspace_uses_latest_valid_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            primary, backup = Path(directory) / "local.json", Path(directory) / "durable.json"
            latest = {"schemaVersion": 1, "updatedAt": "2026-10-01T00:00:01Z", "results": {"A": {}}}
            save_checkpoint(primary, backup, latest)
            primary.write_text('{"results":', encoding="utf-8")
            self.assertEqual(load_checkpoint(primary, backup), latest)
            primary.write_text(json.dumps({"schemaVersion": 1, "updatedAt": "2026-09-29T00:00:00Z",
                                           "results": {}}), encoding="utf-8")
            self.assertEqual(load_checkpoint(primary, backup), latest)
            self.assertFalse(list(Path(directory).glob("*.tmp")))
