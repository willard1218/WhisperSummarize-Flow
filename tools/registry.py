import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

class Registry:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_tasks (
                    id TEXT PRIMARY KEY,
                    source_type TEXT,
                    source_url TEXT,
                    title TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def is_processed(self, task_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM processed_tasks WHERE id = ?", (task_id,))
            return cursor.fetchone() is not None

    def mark_processed(self, task_id: str, source_type: str = None, source_url: str = None, title: str = None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO processed_tasks (id, source_type, source_url, title, processed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (task_id, source_type, source_url, title, datetime.now().isoformat()))
            conn.commit()

    def migrate_from_text(self, text_path: Path):
        if not text_path.exists():
            return
        
        with open(text_path, "r") as f:
            for line in f:
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    source_type, task_id = parts
                    self.mark_processed(task_id, source_type=source_type)
                elif len(parts) == 1:
                    self.mark_processed(parts[0])
