import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

class Registry:
    def __init__(self, db_path: Path):
        self.db_path = str(db_path.resolve())
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_tasks (
                    id TEXT PRIMARY KEY,
                    source_type TEXT,
                    source_url TEXT,
                    title TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT, -- 'url' or 'file'
                    payload TEXT,    -- url string or file path
                    chat_id TEXT,
                    status TEXT DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def enqueue_task(self, task_type: str, payload: str, chat_id: str) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("""
                INSERT INTO task_queue (task_type, payload, chat_id)
                VALUES (?, ?, ?)
            """, (task_type, payload, chat_id))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            print(f"Database Error in enqueue_task: {e}")
            raise
        finally:
            conn.close()

    def get_next_pending_task(self) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM task_queue 
                WHERE status = 'pending' 
                ORDER BY id ASC LIMIT 1
            """)
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            print(f"Database Error in get_next_pending_task: {e}")
            raise
        finally:
            conn.close()

    def update_task_status(self, task_id: int, status: str, error_message: str = None):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                UPDATE task_queue 
                SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, error_message, task_id))
            conn.commit()
        except sqlite3.Error as e:
            print(f"Database Error in update_task_status: {e}")
            raise
        finally:
            conn.close()

    def get_queue_position(self, task_id: int) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM task_queue 
                WHERE status = 'pending' AND id < ?
            """, (task_id,))
            return cursor.fetchone()[0]
        except sqlite3.Error as e:
            print(f"Database Error in get_queue_position: {e}")
            raise
        finally:
            conn.close()

    def is_processed(self, task_id: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("SELECT 1 FROM processed_tasks WHERE id = ?", (task_id,))
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            print(f"Database Error in is_processed: {e}")
            raise
        finally:
            conn.close()

    def mark_processed(self, task_id: str, source_type: str = None, source_url: str = None, title: str = None):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO processed_tasks (id, source_type, source_url, title, processed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (task_id, source_type, source_url, title, datetime.now().isoformat()))
            conn.commit()
        except sqlite3.Error as e:
            print(f"Database Error in mark_processed: {e}")
            raise
        finally:
            conn.close()

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
