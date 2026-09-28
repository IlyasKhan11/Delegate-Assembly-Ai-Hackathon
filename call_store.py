"""Small local SQLite store for the single-process hackathon server."""
import json
import os
import sqlite3
from pathlib import Path
from threading import RLock


def ensure_writable_directory(directory):
    """Fail with a clear message instead of SQLite's vague 'unable to open database file'."""
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    except PermissionError:
        pass  # Reported below with the same message as an unwritable folder.
    if not os.access(directory, os.W_OK | os.X_OK):
        raise PermissionError(
            f"Database folder {directory} is not writable by user id {os.getuid()}. "
            f"Give that user ownership of the folder (e.g. chown {os.getuid()}:{os.getgid()} {directory}) "
            "or point DELEGATE_DATABASE at a writable persistent path."
        )


class CallStore:
    def __init__(self, path):
        if path != ':memory:':
            ensure_writable_directory(Path(path).parent)
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
