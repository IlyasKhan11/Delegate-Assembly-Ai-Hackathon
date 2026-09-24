"""Small local SQLite store for the single-process hackathon server."""
import json
import os
import sqlite3
from pathlib import Path
from threading import RLock


class CallStore:
    def __init__(self, path):
        if path != ':memory:':
            directory = Path(path).parent
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.connection.commit()
        if path != ':memory:':
            os.chmod(path, 0o600)

    def save(self, session):
        payload = json.dumps(session, ensure_ascii=False)
        with self.lock, self.connection:
            self.connection.execute('INSERT INTO calls(id, payload) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (session['session_id'], payload))

    def load_all(self):
        with self.lock:
            return {sid: json.loads(payload) for sid, payload in self.connection.execute('SELECT id, payload FROM calls')}

    def delete(self, session_id):
        with self.lock, self.connection:
            self.connection.execute('DELETE FROM calls WHERE id = ?', (session_id,))

    def close(self):
        with self.lock:
            self.connection.close()
