"""Durable plan history, completion links and bounded conversations."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

from app.db import Database

CHAT_LIMIT = 2 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_versions (
 id INTEGER PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('active','archived','proposal')),
 analysis_json TEXT NOT NULL, source TEXT NOT NULL, model TEXT,
 revision TEXT NOT NULL, base_plan_id INTEGER, created_at TEXT NOT NULL,
 adjustments_json TEXT NOT NULL DEFAULT '[]', generation_note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_plan ON plan_versions(status) WHERE status='active';
CREATE TABLE IF NOT EXISTS plan_sessions (
 id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES plan_versions(id),
 position INTEGER NOT NULL, week INTEGER NOT NULL, content_json TEXT NOT NULL,
 UNIQUE(plan_id,position)
);
CREATE TABLE IF NOT EXISTS completion_links (
 session_id INTEGER PRIMARY KEY REFERENCES plan_sessions(id),
 activity_id INTEGER NOT NULL UNIQUE REFERENCES activities(id), confirmed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_events (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coach_messages (
 id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 role TEXT NOT NULL, content TEXT NOT NULL, proposal_id INTEGER REFERENCES plan_versions(id),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_requests (
 request_id TEXT PRIMARY KEY, kind TEXT NOT NULL, result_id INTEGER,
 created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Workflow:
    def __init__(self, database: Database):
        self.db = database

    def initialize(self) -> None:
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(plan_versions)')}
            if 'generation_note' not in columns:
                conn.execute('ALTER TABLE plan_versions ADD COLUMN generation_note TEXT')

    def revision(self, conn=None) -> str:
        if conn is None:
            with self.db.connect() as connection:
                return self.revision(connection)
        contents = {}
        for table in ('activities', 'health_daily', 'activity_health', 'activity_checkins', 'app_preferences', 'completion_links'):
            rows = [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
            for row in rows:
                for key in ('updated_at', 'synced_at', 'confirmed_at'):
                    row.pop(key, None)
                for key, value in list(row.items()):
                    if key.endswith('_json') or (table == 'app_preferences' and key == 'value'):
                        if value:
                            try:
                                row[key] = json.loads(value)
                            except (TypeError, ValueError):
                                pass
            contents[table] = rows
        return hashlib.sha256(json.dumps(contents, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    @staticmethod
    def _plan(row):
        if not row:
            return None
        return {**dict(row), 'analysis': json.loads(row['analysis_json']), 'adjustments': json.loads(row['adjustments_json'])}

    def active(self):
        with self.db.connect() as conn:
            return self._plan(conn.execute("SELECT * FROM plan_versions WHERE status='active'").fetchone())

    def plan(self, plan_id: int):
        with self.db.connect() as conn:
            return self._plan(conn.execute('SELECT * FROM plan_versions WHERE id=?', (plan_id,)).fetchone())

    def proposals(self):
        with self.db.connect() as conn:
            return [self._plan(row) for row in conn.execute(
                "SELECT * FROM plan_versions WHERE status='proposal' ORDER BY id DESC LIMIT 10")]

    def _insert(self, conn, analysis, source, model, revision, status, base_id, adjustments):
        cursor = conn.execute('INSERT INTO plan_versions(status,analysis_json,source,model,revision,base_plan_id,created_at,adjustments_json) VALUES(?,?,?,?,?,?,?,?)',
                              (status, json.dumps(analysis), source, model, revision, base_id, now(), json.dumps(adjustments)))
        plan_id = cursor.lastrowid
        if status == 'active':
            self._sessions(conn, plan_id, analysis)
        return plan_id

    @staticmethod
    def _sessions(conn, plan_id, analysis):
        position = 0
        for week in analysis['four_week_plan']:
            for session in week['sessions']:
                position += 1
                conn.execute('INSERT INTO plan_sessions(plan_id,position,week,content_json) VALUES(?,?,?,?)',
                             (plan_id, position, week['week'], json.dumps(session)))

    def ensure_active(self, analysis: dict, source='deterministic', model=None):
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT * FROM plan_versions WHERE status='active'").fetchone()
            if not row:
                plan_id = self._insert(conn, analysis, source, model, self.revision(conn), 'active', None, [])
                row = conn.execute('SELECT * FROM plan_versions WHERE id=?', (plan_id,)).fetchone()
            return self._plan(row)

    def propose(self, analysis: dict, source: str, model, revision: str, base_id, adjustments=(), note=None):
        with self.db.connect() as conn:
            proposal_id = self._insert(conn, analysis, source, model, revision, 'proposal', base_id, list(adjustments))
            conn.execute('UPDATE plan_versions SET generation_note=? WHERE id=?', (note, proposal_id))
            return proposal_id

    def is_stale(self, plan, active_id=None):
        expired = datetime.now(timezone.utc) - datetime.fromisoformat(plan['created_at']) >= timedelta(days=7)
        return (plan['revision'] != self.revision() or expired or
                (plan['status'] == 'proposal' and plan['base_plan_id'] != active_id))

    def accept(self, proposal_id: int):
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            proposal = conn.execute("SELECT * FROM plan_versions WHERE id=? AND status='proposal'", (proposal_id,)).fetchone()
            active = conn.execute("SELECT id FROM plan_versions WHERE status='active'").fetchone()
            if not proposal:
                raise ValueError('This proposal is no longer available.')
            created = datetime.fromisoformat(proposal['created_at'])
            if (proposal['revision'] != self.revision(conn) or proposal['base_plan_id'] != (active['id'] if active else None)
                    or (datetime.now(timezone.utc) - created).days >= 7):
                raise ValueError('Your data or active plan changed. Generate a fresh proposal before accepting.')
            conn.execute("UPDATE plan_versions SET status='archived' WHERE status='active'")
            conn.execute("UPDATE plan_versions SET status='active' WHERE id=?", (proposal_id,))
            self._sessions(conn, proposal_id, json.loads(proposal['analysis_json']))

    def sessions(self, plan_id=None, completed_only=False):
        query = 'SELECT s.*, l.activity_id, l.confirmed_at FROM plan_sessions s LEFT JOIN completion_links l ON s.id=l.session_id'
        if completed_only:
            query += ' WHERE l.activity_id IS NOT NULL'
            params = ()
        else:
            query += ' WHERE s.plan_id=?'
            params = (plan_id,)
        with self.db.connect() as conn:
            rows = conn.execute(query + ' ORDER BY s.plan_id,s.position', params).fetchall()
        return [{**dict(row), **json.loads(row['content_json'])} for row in rows]

    def link(self, session_id: int, activity_id: int):
        try:
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute("SELECT s.id FROM plan_sessions s JOIN plan_versions p ON p.id=s.plan_id WHERE s.id=? AND p.status='active'", (session_id,)).fetchone()
                if not row:
                    raise ValueError('Select a session in the active plan.')
                if not conn.execute('SELECT id FROM activities WHERE id=?', (activity_id,)).fetchone():
                    raise ValueError('Select an imported run.')
                conn.execute('INSERT INTO completion_links VALUES(?,?,?)', (session_id, activity_id, now()))
        except sqlite3.IntegrityError as exc:
            raise ValueError('That session or run is already linked. Undo the old link first.') from exc

    def unlink(self, session_id: int):
        with self.db.connect() as conn:
            conn.execute('DELETE FROM completion_links WHERE session_id=?', (session_id,))

    def imported(self, source: str):
        with self.db.connect() as conn:
            conn.execute('INSERT INTO import_events(source,created_at) VALUES(?,?)', (source, now()))

    def latest_import(self):
        with self.db.connect() as conn:
            row = conn.execute("SELECT created_at FROM import_events WHERE source='strava' ORDER BY id DESC LIMIT 1").fetchone()
        return row['created_at'] if row else None

    def claim(self, request_id: str, kind: str):
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Bound operational metadata without changing saved conversations.
            conn.execute("DELETE FROM generation_requests WHERE created_at < ?", ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
            pending = conn.execute('SELECT request_id FROM generation_requests WHERE result_id IS NULL AND created_at>?', ((datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),)).fetchone()
            if pending:
                return False
            try:
                conn.execute('INSERT INTO generation_requests VALUES(?,?,NULL,?)', (request_id, kind, now()))
            except sqlite3.IntegrityError:
                return False
        return True

    def finish(self, request_id: str, result_id):
        with self.db.connect() as conn:
            conn.execute('UPDATE generation_requests SET result_id=? WHERE request_id=?', (result_id, request_id))

    def new_conversation(self):
        with self.db.connect() as conn:
            cursor = conn.execute('INSERT INTO conversations(created_at) VALUES(?)', (now(),))
            self._prune(conn, cursor.lastrowid)
            return cursor.lastrowid

    def conversations(self):
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM conversations ORDER BY id DESC')]

    def messages(self, conversation_id: int):
        with self.db.connect() as conn:
            rows = conn.execute('SELECT * FROM coach_messages WHERE conversation_id=? ORDER BY id', (conversation_id,)).fetchall()
        return [dict(row) for row in rows]

    def append_message(self, conversation_id, role, content, proposal_id=None):
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO coach_messages(conversation_id,role,content,proposal_id,created_at) VALUES(?,?,?,?,?)',
                         (conversation_id, role, content, proposal_id, now()))
            self._prune(conn, conversation_id)

    @staticmethod
    def _prune(conn, current):
        def size():
            return conn.execute('SELECT COALESCE(SUM(length(CAST(content AS BLOB))),0) FROM coach_messages').fetchone()[0]
        while size() > CHAT_LIMIT:
            old = conn.execute('SELECT id FROM conversations WHERE id<>? ORDER BY id LIMIT 1', (current,)).fetchone()
            if old:
                conn.execute('DELETE FROM conversations WHERE id=?', (old['id'],))
            else:
                first = conn.execute('SELECT id FROM coach_messages WHERE conversation_id=? ORDER BY id LIMIT 1', (current,)).fetchone()
                if not first:
                    break
                conn.execute('DELETE FROM coach_messages WHERE id=?', (first['id'],))
        conn.execute('DELETE FROM conversations WHERE id<>? AND id NOT IN (SELECT conversation_id FROM coach_messages)', (current,))
        # Retain at most 100 conversation headers, independent of message size.
        conn.execute('DELETE FROM conversations WHERE id<>? AND id NOT IN (SELECT id FROM conversations ORDER BY id DESC LIMIT 100)', (current,))

    def delete_conversation(self, conversation_id):
        with self.db.connect() as conn:
            conn.execute('DELETE FROM conversations WHERE id=?', (conversation_id,))
