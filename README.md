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
- **Grouped list:** the tracker groups tasks under a `Category / Project` heading, with that project's task cards indented beneath it. Groups follow the order their first task was added, and an emptied group disappears. Done tasks are grouped the same way inside the **Done** section.
- Enter durations as minutes (`25`, `1.5`) or `HH:MM:SS` (`01:30:00`). Zero is unlimited. Allocation and budget are independent; overruns turn red and do not stop timers.
- **Task total:** time across every session for this task. **Budget:** total allowed task time. **Session:** elapsed time for the latest session. **Allocated:** its planned duration.
- **Red square:** stop and finish the session. **Pause:** preserve it for later. **Green play:** resume a paused session. **+**: choose an allocation and immediately start a new session with the same task identity, finishing the prior session for that task.
- Only one timer runs at once. Starting or resuming another task pauses the active task. New sessions can also be added paused by unchecking “Start timer immediately.”
- Adding the same task name under the same project/category (case-insensitive for ASCII names) adds a session and preserves the existing task budget. Edit changes that shared budget. Identically named tasks in different projects are separate.
- **Edit:** change task name, category/project, and shared budget. Moving a task keeps all its session history. Renaming onto an existing task is rejected to avoid ambiguous merges.
- **Subtasks & notes:** double-click a task card to open it. The left pane is a checklist of untimed subtasks — type one and press Enter to add it, tick it to complete it, double-click it to rename it, **Delete** to remove it. Subtasks belong to the task and never carry time of their own. The right pane is one note per session: pick a session from the dropdown (the task's current session is selected) and press **Save note**. Closing or switching sessions with unsaved text asks first. Cards show `✓1/2` for checklist progress and `✎` when the current session has a note.
- **Session corrections:** Edit lists every session with its local start time, state, allocation, and elapsed time. Double-click an allocation or elapsed time to correct it; task totals update as soon as you save. A running session must be paused first (**Pause to correct**). Only the times you change are rewritten.
- **Mark done:** in Edit, saves the dialog, finishes the task's open session (running or paused), and moves the task into the collapsible **Done** section at the bottom of the list. Done tasks keep every session and still appear in Config totals and Reports. **Reopen** (Edit on a done task) moves it back; start a new session with **+**. Adding a session to a done task by name also reopens it. **Stop** only finishes the current session — it never marks the task done.
- **New day:** closes the open day and starts a fresh board. The confirmation lists where the day's time went before anything moves. Unfinished tasks carry over with their budget, allocation, and subtasks, but with **TASK TOTAL** and **SESSION** back at zero — so a budget is what you give a task that day. Tasks you finished stay on the day you finished them; to work on one again, quick-add it by the same name and project. The closed day keeps every session, note, and time: it is still in Reports and in Config totals. A day ends only when you press the button — not at midnight, and not when you close the app — so a late night stays on the day you started it. The summary line shows which day the board belongs to.
- **Reports:** a table with one row per task per day — Date, Category, Project, Task, Sessions, Tracked Time. Choose a From/To date range (inclusive, default the last seven days). Dates are local and name the day the session was tracked in, so time keeps its day even past midnight. **Export CSV…** saves exactly the rows shown (UTF-8, Excel-friendly).
- **Config:** create/rename categories and projects, remove empty groups, inspect lifetime category/project/task totals across every day and individual dated sessions, enable always-on-top, or back up the database. Project movement is done per task with Edit. Recorded tasks and sessions are retained; this version does not delete history.

## Storage and timer behavior

Qt chooses the per-user local app data directory (`QStandardPaths.AppLocalDataLocation`); on Windows this is typically under `%LOCALAPPDATA%\SplitTask\SplitTask`. The database is `tracker.sqlite3`. Settings store window geometry and always-on-top preference separately.

Elapsed time uses a monotonic clock rather than counting UI ticks. Active time is checkpointed every second and on state changes. Closing the app saves and pauses timers. Restart after a crash restores the last checkpoint paused; up to roughly one second since the last successful checkpoint may be lost. Closed-app downtime is never added. Pause before putting the computer to sleep; suspend accounting depends on the OS clock. A lock prevents simultaneous instances using the same database.

Use **Config → Back up database** for a consistent SQLite backup. To restore, close the app and replace `tracker.sqlite3` with the backup, keeping a copy of the original first.

## Development and validation

```sh
python -m unittest discover -s tests -v
```

Core tests cover pause/resume/stop, session corrections, daily reports and CSV export, mark done/reopen, schema migration, duplicate task aggregation, one active timer, new sessions, crash recovery, editing/moving history, name collisions, and duration validation. A headless Qt smoke test and layout inspection were run on Linux using PySide6 6.8.3. Windows launcher and executable packaging still need validation on a Windows machine.

Source: `main.py` (UI), `store.py` (database and timing), `tests/test_store.py` (domain tests).

Qt reference: https://doc.qt.io/qtforpython-6/PySide6/QtCore/QStandardPaths.html
