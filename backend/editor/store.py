"""SQLite storage: media library, projects, and a version per change.

Every change is a new version row, so undo/redo is moving a pointer
(`head`) and nothing is ever overwritten in place.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .models import Media, Project

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, head INTEGER NOT NULL, latest INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS versions (
    project_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL,
    actor TEXT, command TEXT, created_at REAL NOT NULL,
    PRIMARY KEY (project_id, version));
CREATE TABLE IF NOT EXISTS locks (
    project_id TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL);
"""


class Store:
    def __init__(self, db_path: Path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ---- media -------------------------------------------------------
    def add_media(self, media: Media) -> None:
        with self._lock:
            self._db.execute("INSERT INTO media VALUES (?,?,?)", (media.id, json.dumps(asdict(media)), time.time()))
            self._db.commit()

    def get_media(self, media_id: str) -> Media:
        row = self._db.execute("SELECT data FROM media WHERE id=?", (media_id,)).fetchone()
        if not row:
            raise KeyError(f"media not found: {media_id}")
        return Media(**json.loads(row[0]))

    def list_media(self) -> list[Media]:
        rows = self._db.execute("SELECT data FROM media ORDER BY created_at").fetchall()
        return [Media(**json.loads(r[0])) for r in rows]

    # ---- projects and versions ---------------------------------------
    def create_project(self, project: Project, actor: str) -> int:
        with self._lock:
            self._db.execute("INSERT INTO projects VALUES (?,?,?,?)", (project.id, project.name, 1, 1))
            self._insert_version(project, 1, actor, {"type": "create_project"})
            self._db.commit()
        return 1

    def _insert_version(self, project: Project, version: int, actor: str, command: dict) -> None:
        self._db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?)",
                         (project.id, version, json.dumps(project.to_dict()), actor, json.dumps(command), time.time()))

    def head(self, project_id: str) -> tuple[int, int]:
        row = self._db.execute("SELECT head, latest FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError(f"project not found: {project_id}")
        return row[0], row[1]

    def load(self, project_id: str, version: int | None = None) -> tuple[Project, int]:
        head, _ = self.head(project_id)
        v = version or head
        row = self._db.execute("SELECT data FROM versions WHERE project_id=? AND version=?", (project_id, v)).fetchone()
        return Project.from_dict(json.loads(row[0])), v

    def commit(self, project: Project, actor: str, command: dict) -> int:
        """Save a new version after head; anything that was 'redo-able' is dropped."""
        with self._lock:
            head, _ = self.head(project.id)
            self._db.execute("DELETE FROM versions WHERE project_id=? AND version>?", (project.id, head))
            new_v = head + 1
            self._insert_version(project, new_v, actor, command)
            self._db.execute("UPDATE projects SET head=?, latest=?, name=? WHERE id=?",
                             (new_v, new_v, project.name, project.id))
            self._db.commit()
            return new_v

    def move_head(self, project_id: str, version: int) -> None:
        with self._lock:
            self._db.execute("UPDATE projects SET head=? WHERE id=?", (version, project_id))
            self._db.commit()

    def history(self, project_id: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT version, actor, command, created_at FROM versions WHERE project_id=? ORDER BY version",
            (project_id,)).fetchall()
        return [{"version": r[0], "actor": r[1], "command": json.loads(r[2]), "at": r[3]} for r in rows]

    def list_projects(self) -> list[dict]:
        rows = self._db.execute("SELECT id, name, head FROM projects ORDER BY name").fetchall()
        return [{"id": r[0], "name": r[1], "version": r[2]} for r in rows]

    # ---- edit locks (one agent writes at a time) ----------------------
    def acquire_lock(self, project_id: str, owner: str, ttl: float) -> bool:
        with self._lock:
            now = time.time()
            row = self._db.execute("SELECT owner, expires_at FROM locks WHERE project_id=?", (project_id,)).fetchone()
            if row and row[0] != owner and row[1] > now:
                return False
            self._db.execute("INSERT OR REPLACE INTO locks VALUES (?,?,?)", (project_id, owner, now + ttl))
            self._db.commit()
            return True

    def lock_owner(self, project_id: str) -> str | None:
        row = self._db.execute("SELECT owner, expires_at FROM locks WHERE project_id=?", (project_id,)).fetchone()
        if row and row[1] > time.time():
            return row[0]
        return None

    def release_lock(self, project_id: str, owner: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM locks WHERE project_id=? AND owner=?", (project_id, owner))
            self._db.commit()
