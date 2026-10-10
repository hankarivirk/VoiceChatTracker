import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from VCBlogger.database.backup import create_backup
from VCBlogger.database.restore import restore
from VCBlogger.database.mongo import get_db


class TestLocalBackup(unittest.IsolatedAsyncioTestCase):
    async def test_backup_is_valid_gzip_json_and_keeps_private_permissions(self):
        db = await get_db()
        await db["groups"].update_one({"group_id": -100887003}, {"$set": {"group_id": -100887003, "title": "Backup Test"}}, upsert=True)
        with tempfile.TemporaryDirectory() as tmp:
            with patch("VCBlogger.database.backup.BACKUP_DIR", tmp):
                path = Path(await create_backup())
            self.assertTrue(path.exists())
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            self.assertIn("groups", payload["collections"])
            self.assertTrue(any(d.get("group_id") == -100887003 for d in payload["collections"]["groups"]))
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            await db["groups"].delete_one({"group_id": -100887003})
            await restore(path)
            restored = await db["groups"].find_one({"group_id": -100887003})
            self.assertEqual(restored["title"], "Backup Test")


if __name__ == "__main__":
    unittest.main()
