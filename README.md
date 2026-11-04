# Split Task

A compact Windows-first task timer in Python, PySide6, and SQLite. Stacked rows are inspired by the supplied LiveSplit-style sketch, with readable dark styling and separate task/session numbers.

## Run on Windows

1. Install 64-bit Python 3.11 or 3.12 with the Python launcher (`py`).
2. Extract this entire folder.
3. Double-click `run_windows.bat`. The first launch downloads PySide6 into a local virtual environment, so it needs internet access. Subsequent launches work offline.

Or run manually:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py
```

To build a desktop executable, double-click `build_windows.bat` on Windows. The result is `dist\SplitTask\SplitTask.exe`; distribute the entire `dist\SplitTask` folder. This source package does not include a compiled Windows executable.

## Workflow

- **Quick add:** category, editable project dropdown, task name, session allocation, and shared task budget. New category/project names create groups automatically. Blank category becomes **Main**; blank project becomes **General**.
- **Shift+A** or **Inline:** inserts an editable form directly in the tracker. Save creates the session; Cancel discards the draft.
- Enter durations as minutes (`25`, `1.5`) or `HH:MM:SS` (`01:30:00`). Zero is unlimited. Allocation and budget are independent; overruns turn red and do not stop timers.
- **Task total:** time across every session for this task. **Budget:** total allowed task time. **Session:** elapsed time for the latest session. **Allocated:** its planned duration.
- **Red square:** stop and finish the session. **Pause:** preserve it for later. **Green play:** resume a paused session. **+**: choose an allocation and immediately start a new session with the same task identity, finishing the prior session for that task.
- Only one timer runs at once. Starting or resuming another task pauses the active task. New sessions can also be added paused by unchecking “Start timer immediately.”
- Adding the same task name under the same project/category (case-insensitive for ASCII names) adds a session and preserves the existing task budget. Edit changes that shared budget. Identically named tasks in different projects are separate.
- **Edit:** change task name, category/project, and shared budget. Moving a task keeps all its session history. Renaming onto an existing task is rejected to avoid ambiguous merges.
- **Session corrections:** Edit lists every session with its local start time, state, allocation, and elapsed time. Double-click an allocation or elapsed time to correct it; task totals update as soon as you save. A running session must be paused first (**Pause to correct**). Only the times you change are rewritten.
- **Config:** create/rename categories and projects, remove empty groups, inspect category/project/task totals and individual dated sessions, enable always-on-top, or back up the database. Project movement is done per task with Edit. Recorded tasks and sessions are retained; this version does not delete history.

## Storage and timer behavior

Qt chooses the per-user local app data directory (`QStandardPaths.AppLocalDataLocation`); on Windows this is typically under `%LOCALAPPDATA%\SplitTask\SplitTask`. The database is `tracker.sqlite3`. Settings store window geometry and always-on-top preference separately.

Elapsed time uses a monotonic clock rather than counting UI ticks. Active time is checkpointed every second and on state changes. Closing the app saves and pauses timers. Restart after a crash restores the last checkpoint paused; up to roughly one second since the last successful checkpoint may be lost. Closed-app downtime is never added. Pause before putting the computer to sleep; suspend accounting depends on the OS clock. A lock prevents simultaneous instances using the same database.

Use **Config → Back up database** for a consistent SQLite backup. To restore, close the app and replace `tracker.sqlite3` with the backup, keeping a copy of the original first.

## Development and validation

```sh
python -m unittest discover -s tests -v
```

Core tests cover pause/resume/stop, duplicate task aggregation, one active timer, new sessions, crash recovery, editing/moving history, name collisions, and duration validation. A headless Qt smoke test and layout inspection were run on Linux using PySide6 6.8.3. Windows launcher and executable packaging still need validation on a Windows machine.

Source: `main.py` (UI), `store.py` (database and timing), `tests/test_store.py` (domain tests).

Qt reference: https://doc.qt.io/qtforpython-6/PySide6/QtCore/QStandardPaths.html
