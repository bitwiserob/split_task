"""SQLite domain model. Durations are seconds; budgets belong to tasks."""
import csv
import sqlite3
import time
from pathlib import Path


def duration(value):
    """Accept minutes or HH:MM:SS, including decimal minutes."""
    import math
    text = str(value).strip()
    try:
        if ':' in text:
            parts = text.split(':')
            if len(parts) != 3 or any(not p.isdigit() for p in parts):
                raise ValueError
            h, m, s = map(int, parts)
            if m >= 60 or s >= 60:
                raise ValueError
            result = h * 3600 + m * 60 + s
        else:
            result = float(text) * 60
        if not math.isfinite(result) or result < 0 or result > 359999999:
            raise ValueError
        return round(result)
    except (ValueError, OverflowError):
        raise ValueError('Use minutes (e.g. 25 or 1.5) or HH:MM:SS; no negative times.') from None


def clock(seconds):
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{h:02}:{m:02}:{s:02}'


REPORT_COLUMNS = ['Date', 'Category', 'Project', 'Task', 'Sessions', 'Tracked Time']


def report_csv(rows, file):
    """Write report() rows to an open text file, matching the on-screen table."""
    writer = csv.writer(file)
    writer.writerow(REPORT_COLUMNS)
    for r in rows:
        writer.writerow([r['day'], r['category'], r['project'], r['task'], r['sessions'], clock(r['tracked'])])


class Store:
    def __init__(self, path, now=time.monotonic):
        self.now = now
        self.active = None
        self.last = now()
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS categories(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE);
        CREATE TABLE IF NOT EXISTS projects(
          id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL REFERENCES categories(id),
          name TEXT NOT NULL COLLATE NOCASE, UNIQUE(category_id,name));
        CREATE TABLE IF NOT EXISTS tasks(
          id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
          name TEXT NOT NULL COLLATE NOCASE, budget INTEGER NOT NULL CHECK(budget>=0),
          UNIQUE(project_id,name));
        CREATE TABLE IF NOT EXISTS sessions(
          id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),
          allocated INTEGER NOT NULL CHECK(allocated>=0), elapsed REAL NOT NULL DEFAULT 0,
          state TEXT NOT NULL CHECK(state IN ('running','paused','stopped')),
          created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
        UPDATE sessions SET state='paused' WHERE state='running';
        INSERT OR IGNORE INTO categories(name) VALUES ('Main');
        ''')
        self.project('Main', '')

    def categories(self):
        return self.db.execute('SELECT * FROM categories ORDER BY name').fetchall()

    def projects(self, category=None):
        return self.db.execute('''SELECT p.*,c.name category FROM projects p
            JOIN categories c ON c.id=p.category_id
            WHERE (? IS NULL OR c.name=? COLLATE NOCASE) ORDER BY c.name,p.name''',
            (category, category)).fetchall()

    def project(self, category, project):
        category, project = category.strip() or 'Main', project.strip() or 'General'
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO categories(name) VALUES (?)', (category,))
            cid = self.db.execute('SELECT id FROM categories WHERE name=? COLLATE NOCASE', (category,)).fetchone()[0]
            self.db.execute('INSERT OR IGNORE INTO projects(category_id,name) VALUES (?,?)', (cid, project))
            return self.db.execute('SELECT id FROM projects WHERE category_id=? AND name=? COLLATE NOCASE', (cid, project)).fetchone()[0]

    def add(self, category, project, name, budget, allocated, start=True):
        if not name.strip():
            raise ValueError('Give the task a name.')
        pid = self.project(category, project)
        with self.db:
            row = self.db.execute('SELECT id FROM tasks WHERE project_id=? AND name=? COLLATE NOCASE', (pid, name.strip())).fetchone()
            if row:
                tid = row[0]  # An existing task retains its shared budget.
            else:
                tid = self.db.execute('INSERT INTO tasks(project_id,name,budget) VALUES (?,?,?)', (pid, name.strip(), budget)).lastrowid
        self.new_session(tid, allocated, start)
        return tid

    def checkpoint(self):
        now = self.now()
        if self.active is not None:
            with self.db:
                self.db.execute('UPDATE sessions SET elapsed=elapsed+? WHERE id=?', (max(0, now-self.last), self.active))
        self.last = now

    def transition(self, sid, state):
        self.checkpoint()
        row = self.db.execute('SELECT state FROM sessions WHERE id=?', (sid,)).fetchone()
        if not row or row[0] == 'stopped':
            return
        with self.db:
            if state == 'running':
                self.db.execute("UPDATE sessions SET state='paused' WHERE state='running'")
                self.active = sid
            elif self.active == sid:
                self.active = None
            self.db.execute('UPDATE sessions SET state=? WHERE id=?', (state, sid))

    def new_session(self, tid, allocated, start=True):
        self.checkpoint()
        with self.db:
            previous = self.db.execute('SELECT id FROM sessions WHERE task_id=? ORDER BY id DESC LIMIT 1', (tid,)).fetchone()
            if previous:
                self.db.execute("UPDATE sessions SET state='stopped' WHERE id=?", (previous[0],))
                if self.active == previous[0]:
                    self.active = None
            sid = self.db.execute("INSERT INTO sessions(task_id,allocated,state) VALUES (?,?,'paused')", (tid, allocated)).lastrowid
        if start:
            self.transition(sid, 'running')
        return sid

    def tasks(self):
        return self.db.execute('''SELECT t.*,p.name project,c.name category,
          s.id sid,s.allocated,s.elapsed,s.state,
          (SELECT COALESCE(SUM(elapsed),0) FROM sessions WHERE task_id=t.id) total,
          (SELECT COUNT(*) FROM sessions WHERE task_id=t.id) session_count
          FROM tasks t JOIN projects p ON p.id=t.project_id
          JOIN categories c ON c.id=p.category_id
          JOIN sessions s ON s.id=(SELECT MAX(id) FROM sessions WHERE task_id=t.id)
          ORDER BY t.id''').fetchall()

    def edit(self, tid, category, project, name, budget, allocated=None):
        """Allocation, when given, applies to the latest session; use correct() for others."""
        if not name.strip():
            raise ValueError('Give the task a name.')
        pid = self.project(category, project)
        try:
            with self.db:
                self.db.execute('UPDATE tasks SET project_id=?,name=?,budget=? WHERE id=?', (pid,name.strip(),budget,tid))
                if allocated is not None:
                    self.db.execute('UPDATE sessions SET allocated=? WHERE id=(SELECT MAX(id) FROM sessions WHERE task_id=?)', (allocated,tid))
        except sqlite3.IntegrityError:
            raise ValueError('That project already has a task with this name. Choose another name.') from None

    def sessions(self, tid):
        """Sessions oldest first; `started` is the local start time."""
        return self.db.execute('''SELECT *,datetime(created,'localtime') started
            FROM sessions WHERE task_id=? ORDER BY id''', (tid,)).fetchall()

    def correct(self, sid, elapsed, allocated):
        """Overwrite a session's elapsed time and allocation, pausing it first if running."""
        if elapsed < 0 or allocated < 0:
            raise ValueError('Times cannot be negative.')
        if self.active == sid:
            self.transition(sid, 'paused')
        with self.db:
            self.db.execute('UPDATE sessions SET elapsed=?,allocated=? WHERE id=?', (elapsed, allocated, sid))

    def report(self, start, end):
        """Per-task daily totals for local ISO dates start..end inclusive.
        Each session counts wholly on the local day it started."""
        return self.db.execute('''SELECT date(s.created,'localtime') day,
          c.name category,p.name project,t.name task,
          COUNT(*) sessions,SUM(s.elapsed) tracked
          FROM sessions s JOIN tasks t ON t.id=s.task_id
          JOIN projects p ON p.id=t.project_id JOIN categories c ON c.id=p.category_id
          WHERE date(s.created,'localtime') BETWEEN ? AND ?
          GROUP BY day,t.id ORDER BY day,c.name,p.name,t.name''', (start, end)).fetchall()

    def rename(self, kind, ident, name):
        if kind not in ('categories', 'projects') or not name.strip():
            raise ValueError('Enter a name.')
        with self.db:
            self.db.execute(f'UPDATE {kind} SET name=? WHERE id=?', (name.strip(), ident))

    def delete_empty(self, kind, ident):
        if kind not in ('categories', 'projects'):
            raise ValueError('Choose a category or project.')
        with self.db:
            self.db.execute(f'DELETE FROM {kind} WHERE id=?', (ident,))

    def close(self):
        self.checkpoint()
        with self.db:
            self.db.execute("UPDATE sessions SET state='paused' WHERE state='running'")
        self.active = None
        self.db.close()
