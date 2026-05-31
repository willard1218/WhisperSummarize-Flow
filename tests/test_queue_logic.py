import unittest
import sqlite3
import os
import tempfile
from pathlib import Path
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.registry import Registry

class TestRegistryQueue(unittest.TestCase):
    def setUp(self):
        self.test_db = Path(tempfile.mktemp(suffix=".db"))
        self.registry = Registry(self.test_db)

    def tearDown(self):
        if self.test_db.exists():
            self.test_db.unlink()

    def test_enqueue_and_dequeue(self):
        # Enqueue tasks
        id1 = self.registry.enqueue_task("url", "https://yt1.com", "chat123")
        id2 = self.registry.enqueue_task("url", "https://yt2.com", "chat123")
        
        # Check initial positions
        self.assertEqual(self.registry.get_queue_position(id1), 0)
        self.assertEqual(self.registry.get_queue_position(id2), 1)
        
        # Get next task
        task = self.registry.get_next_pending_task()
        self.assertEqual(task["id"], id1)
        self.assertEqual(task["payload"], "https://yt1.com")
        
        # Update status and check queue again
        self.registry.update_task_status(id1, "processing")
        self.assertEqual(self.registry.get_queue_position(id2), 0)
        
        task2 = self.registry.get_next_pending_task()
        self.assertEqual(task2["id"], id2)

    def test_status_updates(self):
        tid = self.registry.enqueue_task("file", "/path/to/file", "chat456")
        self.registry.update_task_status(tid, "completed")
        
        with sqlite3.connect(self.test_db) as conn:
            status = conn.execute("SELECT status FROM task_queue WHERE id = ?", (tid,)).fetchone()[0]
            self.assertEqual(status, "completed")

if __name__ == "__main__":
    unittest.main()
