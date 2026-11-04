"""Split Task — compact desktop time budgets, backed by SQLite."""
import os
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QStandardPaths, QSettings, QLockFile, QDate
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QDialog, QFormLayout, QLineEdit,
    QComboBox, QDialogButtonBox, QMessageBox, QCheckBox, QTreeWidget,
    QTreeWidgetItem, QInputDialog, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDateEdit,
)
from store import REPORT_COLUMNS, Store, clock, duration, report_csv


STYLE = '''
QWidget { background: #14191f; color: #e9eef3; font-family: 'Segoe UI'; font-size: 12px; }
QLabel#brand { font-size: 20px; font-weight: 700; }
QLabel#muted { color: #91a1af; font-size: 11px; }
QLabel#taskName { font-size: 16px; font-weight: 600; }
QFrame#card { background: #1c232c; border: 1px solid #303b47; border-radius: 8px; }
QFrame#card QLabel { background: transparent; }
QLabel#digits { font-family: Consolas, monospace; font-size: 15px; }
QPushButton { background: #27323d; border: 1px solid #3d4b59; border-radius: 5px; padding: 7px 10px; }
QPushButton:hover { background: #364656; }
QPushButton:disabled { color: #64717e; background: #20262d; border-color: #303640; }
QPushButton#primary { background: #91e0b2; color: #10251a; border: none; font-weight: 600; }
QLineEdit,QComboBox,QDateEdit { background: #10151b; border: 1px solid #435160; border-radius: 4px; padding: 7px; }
QLineEdit:focus,QComboBox:focus,QDateEdit:focus { border-color: #91e0b2; }
QScrollArea { border: none; }
QTreeWidget,QTableWidget { border: 1px solid #36424e; alternate-background-color: #1c232c; gridline-color: #303b47; }
QHeaderView::section { background: #27323d; color: #b4c1cc; border: none; padding: 8px; }
QToolTip { background: #27323d; color: white; border: 1px solid #566879; }
'''


def label(text, name=None):
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        w.setObjectName(name)
    return w


def button(text, callback, tip='', primary=False):
    w = QPushButton(text)
    w.clicked.connect(callback)
    if tip:
        w.setToolTip(tip)
        w.setAccessibleName(tip)
    if primary:
        w.setObjectName('primary')
    return w


class TaskForm(QWidget):
    def __init__(self, store, row=None):
        super().__init__()
        self.store = store
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems([r['name'] for r in store.categories()])
        self.category.setCurrentText(row['category'] if row else 'Main')
        self.project = QComboBox()
        self.project.setEditable(True)
        self.category.currentTextChanged.connect(self.refresh_projects)
        self.refresh_projects()
        self.project.setCurrentText(row['project'] if row else '')
        self.project.lineEdit().setPlaceholderText('General (or type a new project)')
        self.name = QLineEdit(row['name'] if row else '')
        self.name.setPlaceholderText('What are you working on?')
        self.allocated = QLineEdit(clock(row['allocated']) if row else '25')
        self.budget = QLineEdit(clock(row['budget']) if row else '60')
        self.editing = row is not None
        fields = [('Category',self.category), ('Project',self.project), ('Task',self.name)]
        if not self.editing:  # Edit corrects allocations per session instead.
            fields.append(('Session allocation',self.allocated))
        for title, field in fields + [('Total task budget',self.budget)]:
            form.addRow(title, field)
        form.addRow(label('Times: minutes or HH:MM:SS. Zero means no limit.', 'muted'))
        if not row:
            note = label('Existing task name + project adds a session and keeps its budget.', 'muted')
            note.setWordWrap(True)
            form.addRow(note)
        self.start = QCheckBox('Start timer immediately')
        self.start.setChecked(True)
        if not row:
            form.addRow(self.start)

    def refresh_projects(self):
        text = self.project.currentText()
        self.project.clear()
        self.project.addItems([r['name'] for r in self.store.projects(self.category.currentText())])
        self.project.setCurrentText(text)

    def values(self):
        return (self.category.currentText(), self.project.currentText(), self.name.text(),
                duration(self.budget.text()), None if self.editing else duration(self.allocated.text()))


class SessionTable(QTableWidget):
    """Editable allocation and elapsed time for each session of one task."""
    COLUMNS = ['#', 'Started', 'State', 'Allocation', 'Elapsed']

    def __init__(self, store, tid):
        super().__init__(0, len(self.COLUMNS))
        self.store, self.tid = store, tid
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.verticalHeader().hide()
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col in (0, 1):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.sessions = store.sessions(tid)
        self.setRowCount(len(self.sessions))
        for n, s in enumerate(self.sessions):
            self.fill(n, s)

    def fill(self, n, s):
        running = s['state'] == 'running'
        for col, text in enumerate([str(n+1), s['started'], s['state'], clock(s['allocated']), clock(s['elapsed'])]):
            item = QTableWidgetItem(text)
            if col < 3 or running:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(n, col, item)
        if running:
            self.setCellWidget(n, 2, button('Pause to correct', lambda checked=False: self.pause(n),
                                            'Pause this session so its times can be corrected'))

    def pause(self, n):
        self.store.transition(self.sessions[n]['id'], 'paused')
        self.removeCellWidget(n, 2)
        self.sessions[n] = next(s for s in self.store.sessions(self.tid) if s['id'] == self.sessions[n]['id'])
        self.fill(n, self.sessions[n])

    def corrections(self):
        """(sid, elapsed, allocated) for edited rows; untouched rows keep sub-second precision."""
        changes = []
        for n, s in enumerate(self.sessions):
            allocated, elapsed = self.item(n, 3).text(), self.item(n, 4).text()
            if allocated == clock(s['allocated']) and elapsed == clock(s['elapsed']):
                continue
            try:
                changes.append((s['id'], duration(elapsed), duration(allocated)))
            except ValueError as exc:
                raise ValueError(f'Session #{n+1}: {exc}') from None
        return changes


class TaskDialog(QDialog):
    def __init__(self, parent, row=None):
        super().__init__(parent)
        self.setWindowTitle('Edit task' if row else 'Quick add')
        self.resize(*((560, 560) if row else (440, 330)))
        layout = QVBoxLayout(self)
        self.form = TaskForm(parent.store, row)
        layout.addWidget(self.form)
        if row:
            layout.addWidget(label('Sessions', 'taskName'))
            layout.addWidget(label('Double-click an allocation or elapsed time to correct it. Pause a running session first.', 'muted'))
            self.sessions = SessionTable(parent.store, row['id'])
            layout.addWidget(self.sessions, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(lambda: self.save(parent.store, row))
        buttons.rejected.connect(self.reject)
        if row:
            done = not row['done']
            tip = 'Save, finish any open session, and move to Done' if done else 'Save and move back to the active list'
            self.done_button = button('Mark done' if done else 'Reopen', lambda: self.save(parent.store, row, done), tip)
            buttons.addButton(self.done_button, QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)

    def save(self, store, row, done=None):
        try:
            if row:
                values, corrections = self.form.values(), self.sessions.corrections()
                store.edit(row['id'], *values)
                for correction in corrections:
                    store.correct(*correction)
                if done is not None:
                    store.set_done(row['id'], done)
            else:
                store.add(*self.form.values(), start=self.form.start.isChecked())
            self.accept()
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, 'Check task', str(exc))


class TaskRow(QFrame):
    def __init__(self, owner, row):
        super().__init__()
        self.owner, self.row = owner, row
        self.setObjectName('card')
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        self.path = label('', 'muted')
        top.addWidget(self.path, 1)
        self.state = label('', 'muted')
        top.addWidget(self.state)
        box.addLayout(top)
        heading = QHBoxLayout()
        self.title = label('', 'taskName')
        self.title.setWordWrap(True)
        heading.addWidget(self.title, 1)
        heading.addWidget(button('Edit', lambda: owner.edit(self.row)))
        for symbol, color, action, tip in [
            ('■','#ed7281','stopped','Stop — finish this session'),
            ('Ⅱ','#e0c784','paused','Pause this session'),
            ('▶','#91e0b2','running','Resume this session')]:
            b = button(symbol, lambda checked=False, a=action: self.control(a), tip)
            b.setFixedSize(28, 28)
            b.setStyleSheet(f'QPushButton {{ border-radius: 14px; padding: 0; color: {color}; }}')
            setattr(self, action, b)
            heading.addWidget(b)
        self.plus = button('+', lambda: owner.new_session(self.row), 'Start a new session for this task')
        heading.addWidget(self.plus)
        box.addLayout(heading)
        metrics = QHBoxLayout()
        self.metrics = {}
        for key, text in [('total','TASK TOTAL'),('budget','BUDGET'),('elapsed','SESSION'),('allocated','ALLOCATED')]:
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(label(text, 'muted'))
            value = label('', 'digits')
            column.addWidget(value)
            metrics.addLayout(column, 1)
            self.metrics[key] = value
        box.addLayout(metrics)
        self.refresh(row)

    def control(self, state):
        self.owner.store.transition(self.row['sid'], state)
        self.owner.refresh()

    def refresh(self, row):
        self.row = row
        self.path.setText(f"{row['category']}  /  {row['project']}")
        self.title.setText(row['name'])
        self.state.setText(f"{'DONE' if row['done'] else row['state'].upper()} · #{row['session_count']}")
        for control in (self.stopped, self.paused, self.running, self.plus):
            control.setVisible(not row['done'])  # Reopen from Edit to track more time.
        for key, value in self.metrics.items():
            value.setText('—' if key in ('budget','allocated') and row[key] == 0 else clock(row[key]))
        for actual, limit in [('total','budget'), ('elapsed','allocated')]:
            over = row[limit] > 0 and row[actual] > row[limit]
            self.metrics[actual].setStyleSheet(f"color: {'#ff8792' if over else '#91e0b2'};")
        self.running.setEnabled(row['state'] == 'paused')
        self.paused.setEnabled(row['state'] == 'running')
        self.stopped.setEnabled(row['state'] != 'stopped')


class ConfigDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.store = owner, owner.store
        self.setWindowTitle('Projects & totals')
        self.resize(720, 520)
        box = QVBoxLayout(self)
        box.addWidget(label('Projects & totals', 'brand'))
        box.addWidget(label('Expand tasks to see individual sessions. Totals include every session.', 'muted'))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Category / project / task / session', 'Tracked', 'Budget / allocation'])
        self.tree.setColumnWidth(0, 370)
        self.tree.setAlternatingRowColors(True)
        box.addWidget(self.tree)
        bar = QHBoxLayout()
        for text, handler in [('Add category', self.add_category), ('Add project',self.add_project),
                              ('Rename',self.rename),('Delete empty',self.delete),('Refresh',self.populate)]:
            bar.addWidget(button(text,handler))
        box.addLayout(bar)
        self.topmost = QCheckBox('Always on top')
        self.topmost.setChecked(owner.settings.value('topmost',False,type=bool))
        self.topmost.toggled.connect(owner.set_topmost)
        box.addWidget(self.topmost)
        bottom = QHBoxLayout()
        bottom.addWidget(button('Back up database…',self.backup))
        bottom.addStretch()
        bottom.addWidget(button('Done',self.accept,primary=True))
        box.addLayout(bottom)
        self.populate()

    def populate(self):
        self.store.checkpoint()
        self.tree.clear()
        tasks = self.store.tasks()
        for category in self.store.categories():
            group_tasks = [t for t in tasks if t['category'] == category['name']]
            c = QTreeWidgetItem(self.tree, [category['name'], clock(sum(t['total'] for t in group_tasks)),clock(sum(t['budget'] for t in group_tasks))])
            c.setData(0,Qt.ItemDataRole.UserRole,('categories',category['id']))
            for project in self.store.projects(category['name']):
                pt = [t for t in group_tasks if t['project_id'] == project['id']]
                p = QTreeWidgetItem(c,[project['name'],clock(sum(t['total'] for t in pt)),clock(sum(t['budget'] for t in pt))])
                p.setData(0,Qt.ItemDataRole.UserRole,('projects',project['id']))
                for task in pt:
                    t = QTreeWidgetItem(p,[task['name']+(' · done' if task['done'] else ''),clock(task['total']),clock(task['budget']) if task['budget'] else 'No limit'])
                    for n, s in enumerate(self.store.sessions(task['id']),1):
                        QTreeWidgetItem(t,[f"#{n} · {s['started']} · {s['state']}",clock(s['elapsed']),clock(s['allocated']) if s['allocated'] else 'No limit'])
            c.setExpanded(True)
        self.tree.expandToDepth(1)

    def add_category(self):
        name, ok = QInputDialog.getText(self,'New category','Category name')
        if ok and name.strip():
            try:
                with self.store.db:
                    self.store.db.execute('INSERT INTO categories(name) VALUES (?)',(name.strip(),))
                self.populate()
            except sqlite3.IntegrityError:
                QMessageBox.warning(self,'Already exists','That category already exists.')

    def add_project(self):
        category, ok = QInputDialog.getItem(self,'New project','Category (or type a new one)',[c['name'] for c in self.store.categories()],0,True)
        if not ok:
            return
        name, ok = QInputDialog.getText(self,'New project','Project name')
        if ok:
            self.store.project(category,name)
            self.populate()

    def selected(self):
        item = self.tree.currentItem()
        data = item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if not data:
            QMessageBox.information(self,'Select a group','Select a category or project first.')
        return item,data

    def rename(self):
        item,data = self.selected()
        if data:
            name,ok = QInputDialog.getText(self,'Rename','Name',text=item.text(0))
            if ok:
                try:
                    self.store.rename(*data,name)
                    self.populate()
                except (ValueError,sqlite3.IntegrityError):
                    QMessageBox.warning(self,'Cannot rename','Enter a nonempty, unique name.')

    def delete(self):
        item,data = self.selected()
        if data:
            try:
                self.store.delete_empty(*data)
                self.populate()
            except sqlite3.IntegrityError:
                QMessageBox.information(self,'Group is not empty','Only empty groups can be deleted. Your tracked history is kept.')

    def backup(self):
        path,_ = QFileDialog.getSaveFileName(self,'Back up database','split-task-backup.sqlite3','SQLite (*.sqlite3)')
        if path:
            source = self.store.db.execute('PRAGMA database_list').fetchone()[2]
            if Path(path).resolve() == Path(source).resolve():
                QMessageBox.warning(self,'Choose another file','Choose a different file from the active database.')
                return
            self.store.checkpoint()
            try:
                with sqlite3.connect(path) as target:
                    self.store.db.backup(target)
                QMessageBox.information(self,'Backup saved','The database backup is ready.')
            except sqlite3.Error as exc:
                QMessageBox.warning(self,'Backup failed',str(exc))


class ReportDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.store = owner.store
        self.rows = []
        self.setWindowTitle('Reports')
        self.resize(760, 520)
        box = QVBoxLayout(self)
        box.addWidget(label('Reports', 'brand'))
        box.addWidget(label('One row per task per day. Each session counts on the local day it started.', 'muted'))
        bar = QHBoxLayout()
        today = QDate.currentDate()
        self.start, self.end = QDateEdit(today.addDays(-6)), QDateEdit(today)
        for text, field in [('From', self.start), ('To', self.end)]:
            field.setCalendarPopup(True)
            field.setDisplayFormat('yyyy-MM-dd')
            field.dateChanged.connect(self.populate)
            bar.addWidget(label(text))
            bar.addWidget(field)
        bar.addStretch()
        bar.addWidget(button('Refresh', self.populate))
        box.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(REPORT_COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        for col, width in enumerate([95, 110, 130, 190, 70]):
            self.tree.setColumnWidth(col, width)
        box.addWidget(self.tree, 1)
        self.summary = label('', 'muted')
        box.addWidget(self.summary)
        bottom = QHBoxLayout()
        bottom.addWidget(button('Export CSV…', self.export, 'Save the rows shown above as CSV'))
        bottom.addStretch()
        bottom.addWidget(button('Done', self.accept, primary=True))
        box.addLayout(bottom)
        self.populate()

    def range(self):
        return self.start.date().toString(Qt.DateFormat.ISODate), self.end.date().toString(Qt.DateFormat.ISODate)

    def populate(self):
        self.store.checkpoint()
        self.tree.clear()
        start, end = self.range()
        self.rows = self.store.report(start, end) if start <= end else []
        for r in self.rows:
            item = QTreeWidgetItem(self.tree, [r['day'], r['category'], r['project'], r['task'], str(r['sessions']), clock(r['tracked'])])
            for col in (4, 5):
                item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if start > end:
            self.summary.setText('The From date is after the To date.')
        else:
            self.summary.setText(f"{len(self.rows)} rows · {sum(r['sessions'] for r in self.rows)} sessions · "
                                 f"{clock(sum(r['tracked'] for r in self.rows))} tracked")

    def export(self):
        if not self.rows:
            QMessageBox.information(self, 'Nothing to export', 'No sessions in this date range.')
            return
        start, end = self.range()
        path, _ = QFileDialog.getSaveFileName(self, 'Export CSV', f'split-task-{start}-to-{end}.csv', 'CSV (*.csv)')
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8-sig') as file:  # BOM so Excel reads UTF-8.
                    report_csv(self.rows, file)
            except OSError as exc:
                QMessageBox.warning(self, 'Export failed', str(exc))


class MainWindow(QMainWindow):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.settings = QSettings('SplitTask','SplitTask')
        self.cards = {}
        self.inline = None
        self.setWindowTitle('Split Task')
        self.setMinimumSize(510,380)
        self.resize(540,720)
        root = QWidget()
        self.setCentralWidget(root)
        box = QVBoxLayout(root)
        box.setContentsMargins(16,16,16,16)
        head = QHBoxLayout()
        head.addWidget(label('Split Task','brand'))
        head.addStretch()
        head.addWidget(label('MAKE TIME VISIBLE','muted'))
        box.addLayout(head)
        box.addWidget(label('One task at a time. Every session counts.','muted'))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        self.list = QVBoxLayout(content)
        self.list.setContentsMargins(0,10,0,10)
        self.list.setSpacing(8)
        self.empty = label('Your next session starts here.\n\nQuick add a task, or press Shift+A.','muted')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list.addWidget(self.empty)
        self.active = QVBoxLayout()
        self.active.setSpacing(8)
        self.list.addLayout(self.active)
        self.done_toggle = button('',self.toggle_done,'Show or hide completed tasks')
        self.done_toggle.setStyleSheet('QPushButton { text-align: left; background: transparent; border: none; color: #91a1af; }')
        self.list.addWidget(self.done_toggle)
        self.done = QWidget()
        self.done_list = QVBoxLayout(self.done)
        self.done_list.setContentsMargins(0,0,0,0)
        self.done_list.setSpacing(8)
        self.list.addWidget(self.done)
        self.list.addStretch()
        self.scroll.setWidget(content)
        box.addWidget(self.scroll,1)
        self.summary = label('','muted')
        box.addWidget(self.summary)
        bottom = QHBoxLayout()
        bottom.addWidget(button('+  Quick add',self.quick_add,'Add a task and session',True))
        bottom.addWidget(button('Inline',self.inline_add,'Add an editable row (Shift+A)'))
        bottom.addStretch()
        bottom.addWidget(button('Reports',self.reports,'Daily tracked time per task, with CSV export'))
        bottom.addWidget(button('⚙  Config',self.config,'Manage projects, totals, and settings'))
        box.addLayout(bottom)
        self.shortcut = QShortcut(QKeySequence('Shift+A'),self)
        self.shortcut.activated.connect(self.inline_add)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        geometry = self.settings.value('geometry')
        if geometry:
            self.restoreGeometry(geometry)
        self.set_topmost(self.settings.value('topmost',False,type=bool))
        self.refresh()

    def set_topmost(self, checked):
        self.settings.setValue('topmost',checked)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,checked)
        self.show()

    def tick(self):
        self.store.checkpoint()
        self.refresh()

    def refresh(self):
        tasks = self.store.tasks()
        active = [t for t in tasks if not t['done']]
        done = [t for t in tasks if t['done']]
        self.empty.setVisible(not active and self.inline is None)
        for layout, rows in [(self.active,active),(self.done_list,done)]:
            for index, row in enumerate(rows):
                card = self.cards.get(row['id'])
                if card is None:
                    card = self.cards[row['id']] = TaskRow(self,row)
                else:
                    card.refresh(row)
                if layout.indexOf(card) != index:  # New, or moved between active and Done.
                    self.active.removeWidget(card)
                    self.done_list.removeWidget(card)
                    layout.insertWidget(index,card)
        expanded = self.settings.value('done_expanded',False,type=bool)
        self.done_toggle.setText(f"{'▾' if expanded else '▸'}  Done ({len(done)})")
        self.done_toggle.setVisible(bool(done))
        self.done.setVisible(bool(done) and expanded)
        running = next((t['name'] for t in tasks if t['state']=='running'),None)
        self.summary.setText(f"{len(active)} tasks" + (f" · {len(done)} done" if done else '') +
                             f" · {clock(sum(t['total'] for t in tasks))} tracked" + (' · Timer running' if running else ' · Timers idle'))

    def toggle_done(self):
        self.settings.setValue('done_expanded',not self.settings.value('done_expanded',False,type=bool))
        self.refresh()

    def quick_add(self):
        TaskDialog(self).exec()
        self.refresh()

    def edit(self,row):
        TaskDialog(self,row).exec()
        self.refresh()

    def new_session(self,row):
        value,ok = QInputDialog.getText(self,'New session',f"Allocation for {row['name']} (minutes or HH:MM:SS)",text=clock(row['allocated']))
        if ok:
            try:
                self.store.new_session(row['id'],duration(value))
                self.refresh()
            except ValueError as exc:
                QMessageBox.warning(self,'Check allocation',str(exc))

    def inline_add(self):
        if self.inline:
            self.inline.form.name.setFocus()
            return
        self.inline = QFrame()
        self.inline.setObjectName('card')
        layout = QVBoxLayout(self.inline)
        layout.addWidget(label('New session','taskName'))
        self.inline.form = TaskForm(self.store)
        layout.addWidget(self.inline.form)
        bar = QHBoxLayout()
        bar.addWidget(button('Save session',self.save_inline,primary=True))
        bar.addWidget(button('Cancel',self.cancel_inline))
        layout.addLayout(bar)
        self.list.insertWidget(0,self.inline)
        self.inline.form.name.setFocus()
        self.scroll.ensureWidgetVisible(self.inline)
        self.refresh()

    def save_inline(self):
        try:
            self.store.add(*self.inline.form.values(),start=self.inline.form.start.isChecked())
            self.cancel_inline()
        except (ValueError,sqlite3.Error) as exc:
            QMessageBox.warning(self,'Check task',str(exc))

    def cancel_inline(self):
        self.list.removeWidget(self.inline)
        self.inline.hide()
        self.inline.deleteLater()
        self.inline = None
        self.refresh()

    def reports(self):
        ReportDialog(self).exec()

    def config(self):
        ConfigDialog(self).exec()
        self.refresh()

    def closeEvent(self,event):
        self.timer.stop()
        self.settings.setValue('geometry',self.saveGeometry())
        self.store.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('SplitTask')
    app.setOrganizationName('SplitTask')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    folder.mkdir(parents=True,exist_ok=True)
    lock = QLockFile(str(folder/'split-task.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None,'Split Task','Split Task is already open, or its data folder is unavailable.')
        return 1
    store = Store(folder/'tracker.sqlite3')
    window = MainWindow(store)
    window.show()
    result = app.exec()
    lock.unlock()
    return result


if __name__ == '__main__':
    raise SystemExit(main())
