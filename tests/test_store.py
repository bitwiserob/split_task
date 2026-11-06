import io
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from store import (
    SPRINT_ITEM_COLUMNS, SPRINT_RUN_COLUMNS, Store, duration, report_csv, signed,
    sprint_csv, sprint_table,
)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'test.db'
        self.time = 0
        self.store = Store(self.path,lambda:self.time)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_pause_resume_stop_and_aggregation(self):
        tid = self.store.add('Work','Website','Layout',3600,600)
        sid = self.store.tasks()[0]['sid']
        self.time = 10
        self.store.transition(sid,'paused')
        self.time = 100
        self.store.transition(sid,'running')
        self.time = 105
        self.store.transition(sid,'stopped')
        self.store.transition(sid,'running')
        self.assertEqual(self.store.tasks()[0]['state'],'stopped')
        same = self.store.add('work','website','layout',9999,300)
        self.assertEqual(same,tid)
        self.time = 115
        self.store.checkpoint()
        row = self.store.tasks()[0]
        self.assertEqual(row['total'],25)
        self.assertEqual(row['elapsed'],10)
        self.assertEqual(row['budget'],3600)
        self.assertEqual(row['session_count'],2)

    def test_one_active_and_new_session(self):
        self.store.add('','','First',600,100)
        self.time = 5
        tid = self.store.add('','','Second',600,100)
        self.time = 12
        self.store.new_session(tid,50)
        self.time = 15
        self.store.checkpoint()
        first,second = self.store.tasks()
        self.assertEqual(first['state'],'paused')
        self.assertEqual(first['total'],5)
        self.assertEqual(second['total'],10)
        self.assertEqual(second['elapsed'],3)
        self.assertEqual(first['category'],'Main')
        self.assertEqual(first['project'],'General')

    def test_restart_pauses_without_counting_downtime(self):
        self.store.add('Study','Math','Revision',3600,600)
        self.time = 12.5
        self.store.checkpoint()
        self.store.db.close()  # Simulate abrupt exit after the last checkpoint.
        self.time = 1000
        self.store = Store(self.path,lambda:self.time)
        self.assertEqual(self.store.tasks()[0]['total'],12.5)
        self.assertEqual(self.store.tasks()[0]['state'],'paused')

    def test_edit_moves_whole_history_and_rejects_collision(self):
        tid = self.store.add('Work','A','Task',100,20,False)
        self.store.new_session(tid,30,False)
        self.store.edit(tid,'Hobby','B','Renamed',200,40)
        row = self.store.tasks()[0]
        self.assertEqual((row['category'],row['project'],row['budget'],row['allocated'],row['session_count']),('Hobby','B',200,40,2))
        other = self.store.add('Hobby','B','Other',30,20,False)
        with self.assertRaises(ValueError):
            self.store.edit(other,'Hobby','B','Renamed',50,10)
        self.assertEqual(self.store.tasks()[1]['name'],'Other')

    def test_edit_without_allocation_keeps_sessions(self):
        tid = self.store.add('Work','A','Task',100,20,False)
        self.store.edit(tid,'Work','A','Task',300)
        row = self.store.tasks()[0]
        self.assertEqual((row['budget'],row['allocated']),(300,20))

    def test_correct_pauses_running_session_and_updates_totals(self):
        tid = self.store.add('Work','A','Task',0,600)
        first = self.store.tasks()[0]['sid']
        self.time = 30
        second = self.store.new_session(tid,300)
        self.time = 50
        self.store.correct(second,120,900)
        self.time = 500  # A paused session no longer accumulates.
        self.store.checkpoint()
        self.store.correct(first,45,600)
        row = self.store.tasks()[0]
        self.assertEqual((row['state'],row['elapsed'],row['allocated'],row['total']),('paused',120,900,165))
        self.assertIsNone(self.store.active)
        self.assertEqual([s['elapsed'] for s in self.store.sessions(tid)],[45,120])
        with self.assertRaises(ValueError):
            self.store.correct(first,-1,0)

    def test_report_groups_by_local_start_day_and_task(self):
        a = self.store.add('Work','Site','Layout',0,0,False)
        b = self.store.add('Work','Site','Copy',0,0,False)
        sessions = [(a,'2026-03-01T23:30:00Z',100),(a,'2026-03-02T00:30:00Z',50),
                    (a,'2026-03-02T12:00:00Z',25),(b,'2026-03-02T12:00:00Z',7)]
        with self.store.db:
            self.store.db.execute('DELETE FROM sessions')
            for tid,created,elapsed in sessions:
                self.store.db.execute("INSERT INTO sessions(task_id,allocated,elapsed,state,created) VALUES (?,0,?,'stopped',?)",(tid,elapsed,created))
        local = lambda text: datetime.fromisoformat(text.replace('Z','+00:00')).astimezone().date().isoformat()
        expected = {}
        for tid,created,elapsed in sessions:
            key = (local(created),'Layout' if tid == a else 'Copy')
            count,total = expected.get(key,(0,0))
            expected[key] = (count+1,total+elapsed)
        rows = self.store.report('2026-01-01','2026-12-31')
        self.assertEqual({(r['day'],r['task']):(r['sessions'],r['tracked']) for r in rows},expected)
        self.assertEqual([r['day'] for r in rows],sorted(r['day'] for r in rows))
        self.assertEqual(self.store.report('2027-01-01','2027-12-31'),[])
        out = io.StringIO()
        report_csv(rows,out)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0],'Date,Category,Project,Task,Sessions,Tracked Time')
        self.assertEqual(len(lines),len(rows)+1)
        self.assertIn(f"{local('2026-03-02T12:00:00Z')},Work,Site,Copy,1,00:00:07",lines)

    def test_mark_done_stops_timer_and_reopen_keeps_sessions(self):
        tid = self.store.add('Work','A','Task',0,0)
        self.time = 20
        self.store.set_done(tid)
        self.time = 90
        self.store.checkpoint()
        row = self.store.tasks()[0]
        self.assertEqual((row['done'],row['state'],row['total']),(1,'stopped',20))
        self.assertIsNone(self.store.active)
        self.assertEqual(self.store.report('0000-01-01','9999-12-31')[0]['tracked'],20)
        self.store.set_done(tid,False)
        row = self.store.tasks()[0]
        self.assertEqual((row['done'],row['session_count'],row['total']),(0,1,20))
        self.store.set_done(tid)
        self.store.add('Work','A','Task',0,0,False)  # Adding a session reopens it.
        row = self.store.tasks()[0]
        self.assertEqual((row['done'],row['session_count'],row['state']),(0,2,'paused'))

    def test_migrates_database_without_done_column(self):
        self.store.close()
        old = Path(self.temp.name)/'old.db'
        with sqlite3.connect(old) as db:
            db.executescript('''
            CREATE TABLE categories(id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE);
            CREATE TABLE projects(id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL REFERENCES categories(id),
              name TEXT NOT NULL COLLATE NOCASE, UNIQUE(category_id,name));
            CREATE TABLE tasks(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
              name TEXT NOT NULL COLLATE NOCASE, budget INTEGER NOT NULL CHECK(budget>=0), UNIQUE(project_id,name));
            CREATE TABLE sessions(id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),
              allocated INTEGER NOT NULL CHECK(allocated>=0), elapsed REAL NOT NULL DEFAULT 0,
              state TEXT NOT NULL CHECK(state IN ('running','paused','stopped')),
              created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
            INSERT INTO categories VALUES (1,'Work');
            INSERT INTO projects VALUES (1,1,'Site');
            INSERT INTO tasks VALUES (1,1,'Layout',600);
            INSERT INTO sessions(task_id,allocated,elapsed,state) VALUES (1,300,42.5,'running');
            ''')
        db.close()
        self.store = Store(old,lambda:self.time)
        row = self.store.tasks()[0]
        self.assertEqual((row['name'],row['budget'],row['total'],row['state'],row['done']),('Layout',600,42.5,'paused',0))
        self.assertEqual((row['note'],row['subtasks']),('',0))
        self.assertEqual(self.store.sessions(row['id'])[0]['day'],self.store.open_day()['label'])
        self.assertEqual([d['tracked'] for d in self.store.days()],[42.5])
        self.store.set_note(row['sid'],'Added after migrating')
        self.store.add_subtask(row['id'],'Added after migrating')
        self.store.close()
        self.store = Store(old,lambda:self.time)  # Reopening an already migrated database is a no-op.
        row = self.store.tasks()[0]
        self.assertEqual((row['done'],row['note'],row['subtasks']),(0,'Added after migrating',1))

    def test_subtasks_are_checklist_items_without_time(self):
        tid = self.store.add('Work','A','Task',0,600)
        other = self.store.add('Work','A','Other',0,600,False)
        first = self.store.add_subtask(tid,'  Draft the copy  ')
        second = self.store.add_subtask(tid,'Pick a hero image')
        self.store.add_subtask(other,'Unrelated')
        self.assertEqual([s['name'] for s in self.store.subtasks(tid)],['Draft the copy','Pick a hero image'])
        self.store.edit_subtask(first,done=True)
        self.store.edit_subtask(second,name='Pick the hero image')
        row = self.store.tasks()[0]
        self.assertEqual((row['subtasks'],row['subtasks_done']),(2,1))
        self.time = 60  # Checklist edits never touch the clock.
        self.store.checkpoint()
        self.assertEqual(self.store.tasks()[0]['total'],60)
        self.store.edit_subtask(first,done=False)
        self.assertEqual(self.store.tasks()[0]['subtasks_done'],0)
        self.store.delete_subtask(first)
        self.assertEqual([s['name'] for s in self.store.subtasks(tid)],['Pick the hero image'])
        self.assertEqual(self.store.tasks()[1]['subtasks'],1)  # Other tasks keep their own.
        for blank in ['','   ']:
            with self.assertRaises(ValueError):
                self.store.add_subtask(tid,blank)
            with self.assertRaises(ValueError):
                self.store.edit_subtask(second,name=blank)

    def test_one_note_per_session(self):
        tid = self.store.add('Work','A','Task',0,600)
        first = self.store.tasks()[0]['sid']
        second = self.store.new_session(tid,600)
        self.assertEqual([s['note'] for s in self.store.sessions(tid)],['',''])
        self.assertEqual(self.store.tasks()[0]['note'],'')
        self.store.set_note(first,'  Kick-off  ')
        self.store.set_note(second,'Blocked on copy')
        self.assertEqual([s['note'] for s in self.store.sessions(tid)],['Kick-off','Blocked on copy'])
        self.assertEqual(self.store.tasks()[0]['note'],'Blocked on copy')  # The card shows the latest.
        self.store.set_note(second,'Rewritten')
        self.assertEqual(self.store.sessions(tid)[1]['note'],'Rewritten')
        self.store.set_note(second,'')
        self.assertEqual(self.store.tasks()[0]['note'],'')
        self.store.correct(first,120,600)  # Corrections leave the note alone.
        self.assertEqual(self.store.sessions(tid)[0]['note'],'Kick-off')

    def test_new_day_resets_the_board_and_keeps_the_day_tracked(self):
        emails = self.store.add('Work','A','Emails',3600,1500)
        self.time = 300  # Starting Finished pauses Emails; only one timer runs.
        finished = self.store.add('Work','A','Finished',3600,1500)
        self.time = 600
        self.store.set_done(finished)
        self.store.transition(next(t['sid'] for t in self.store.tasks() if t['id'] == emails),'running')
        self.time = 1200  # The day tracked 900s on Emails and 300s on Finished.
        first = self.store.day
        self.store.new_day()
        self.assertNotEqual(self.store.day,first)
        self.assertIsNone(self.store.active)
        board = self.store.tasks()
        self.assertEqual([t['name'] for t in board],['Emails'])  # Done tasks stay on their day.
        row = board[0]
        self.assertEqual((row['total'],row['elapsed'],row['state'],row['session_count']),(0,0,'paused',1))
        self.assertEqual((row['lifetime'],row['allocated'],row['budget']),(900,1500,3600))
        yesterday = self.store.tasks(first)  # The closed day is still readable per task.
        self.assertEqual({t['name']:t['total'] for t in yesterday},{'Emails':900,'Finished':300})
        self.assertEqual([t['state'] for t in yesterday],['stopped','stopped'])
        self.store.transition(row['sid'],'running')
        self.time = 1300
        self.store.checkpoint()
        self.assertEqual(self.store.tasks()[0]['total'],100)
        self.assertEqual(self.store.tasks(first)[0]['total'],900)  # A closed day never moves.
        self.assertEqual(self.store.tasks()[0]['lifetime'],1000)
        rows = self.store.report('0000-01-01','9999-12-31')  # Days sharing a date merge per task.
        self.assertEqual(sorted((r['task'],r['sessions'],r['tracked']) for r in rows),
                         [('Emails',2,1000),('Finished',1,300)])
        self.assertEqual(len(self.store.days()),2)
        self.assertEqual([d['tracked'] for d in self.store.days()],[100,1200])
        self.assertEqual([d['tasks'] for d in self.store.days()],[1,2])

    def test_new_day_carries_the_latest_allocation_and_survives_restart(self):
        tid = self.store.add('Work','A','Task',0,600)
        self.store.new_session(tid,1800)
        day = self.store.new_day()
        self.assertEqual(self.store.tasks()[0]['allocated'],1800)
        self.store.close()
        self.store = Store(self.path,lambda:self.time)
        self.assertEqual(self.store.day,day)  # Only New day ends a day, not a restart.
        self.assertEqual(self.store.tasks()[0]['session_count'],1)

    def test_duration_validation(self):
        self.assertEqual(duration('25'),1500)
        self.assertEqual(duration('01:02:03'),3723)
        self.assertEqual(duration('1.5'),90)
        for invalid in ['-1','nan','inf','00:70:00','1:30','','-1:00:00']:
            with self.assertRaises(ValueError):
                duration(invalid)

    def plan(self, name='Work', items=(('Reply to emails',300),('Check report',300))):
        self.store.new_sprint(name)
        for item, allocated in items:
            self.store.add_sprint_item(item,allocated)
        return self.store.sprint_items()

    def test_sprint_runs_items_in_sequence_and_hands_off_on_complete(self):
        self.plan()
        self.assertEqual(self.store.sprint()['planned'],600)
        self.assertEqual(self.store.sprint_current()['name'],'Reply to emails')
        self.store.sprint_start()
        self.time = 120
        self.store.checkpoint()
        first = self.store.sprint_items()[0]
        self.assertEqual((first['state'],first['elapsed'],first['remaining']),('running',120,180))
        second = self.store.sprint_advance()  # Complete: the next item starts on the spot.
        self.assertEqual(second['name'],'Check report')
        self.time = 150
        self.store.checkpoint()
        done, running = self.store.sprint_items()
        self.assertEqual((done['state'],done['elapsed']),('done',120))
        self.assertEqual((running['state'],running['elapsed']),('running',30))
        self.assertEqual(self.store.sprint()['spent'],150)

    def test_sprint_keeps_overtime_and_ends_after_the_last_item(self):
        self.plan(items=(('One',60),))
        self.store.sprint_start()
        self.time = 95  # Zero does not stop the clock; the item runs into overtime.
        self.store.checkpoint()
        self.assertEqual(self.store.sprint_items()[0]['remaining'],-35)
        self.assertIsNone(self.store.sprint_advance())
        sprint = self.store.sprint()
        self.assertIsNotNone(sprint['ended'])
        self.assertEqual((sprint['items_done'],sprint['spent']),(1,95))
        self.assertIsNone(self.store.sprinting)
        self.time = 200  # A finished run banks nothing more.
        self.store.checkpoint()
        self.assertEqual(self.store.sprint()['spent'],95)

    def test_skip_moves_on_without_crediting_the_item(self):
        self.plan(items=(('One',60),('Two',60),('Three',60)))
        self.store.sprint_start()
        self.time = 30
        self.assertEqual(self.store.sprint_advance('skipped')['name'],'Two')
        states = [(i['name'],i['state'],i['elapsed']) for i in self.store.sprint_items()]
        self.assertEqual(states[0],('One','skipped',30))
        self.assertEqual(self.store.sprint()['items_skipped'],1)
        with self.assertRaises(ValueError):
            self.store.sprint_advance('abandoned')

    def test_only_one_clock_runs_across_both_modes(self):
        self.store.add('Work','Site','Layout',0,600)
        sid = self.store.tasks()[0]['sid']
        self.plan(items=(('One',300),))
        self.time = 60
        self.store.sprint_start()  # Starting a sprint pauses the tracked session.
        self.assertEqual(self.store.tasks()[0]['state'],'paused')
        self.assertIsNone(self.store.active)
        self.time = 100
        self.store.transition(sid,'running')  # And starting a session pauses the sprint.
        self.assertEqual(self.store.sprint_items()[0]['state'],'paused')
        self.assertIsNone(self.store.sprinting)
        self.time = 130
        self.store.checkpoint()
        self.assertEqual(self.store.tasks()[0]['elapsed'],90)
        self.assertEqual(self.store.sprint_items()[0]['elapsed'],40)

    def test_pause_and_resume_keep_the_item_on_the_clock(self):
        self.plan(items=(('One',300),('Two',300)))
        self.store.sprint_start()
        self.time = 40
        self.store.sprint_pause()
        self.time = 500  # Paused time is not spent time.
        self.assertEqual(self.store.sprint_current()['name'],'One')
        self.assertEqual(self.store.sprint_start()['name'],'One')
        self.time = 510
        self.store.checkpoint()
        self.assertEqual(self.store.sprint_items()[0]['elapsed'],50)

    def test_plan_edits_reorder_and_reset(self):
        items = self.plan(items=(('One',60),('Two',120),('Three',180)))
        self.store.move_sprint_item(items[2]['id'],-1)
        self.assertEqual([i['name'] for i in self.store.sprint_items()],['One','Three','Two'])
        self.store.move_sprint_item(items[0]['id'],-1)  # Already first; nothing moves.
        self.assertEqual([i['name'] for i in self.store.sprint_items()],['One','Three','Two'])
        self.store.edit_sprint_item(items[1]['id'],name='Two again',allocated=240)
        self.store.delete_sprint_item(items[0]['id'])
        self.assertEqual([(i['name'],i['allocated']) for i in self.store.sprint_items()],
                         [('Three',180),('Two again',240)])
        self.store.sprint_start()
        self.time = 60
        self.store.sprint_advance()
        self.time = 90
        self.store.sprint_reset()  # Run the same routine again from the start line.
        sprint = self.store.sprint()
        self.assertEqual((sprint['spent'],sprint['items_waiting'],sprint['started']),(0,2,None))
        self.assertEqual(self.store.sprint_current()['name'],'Three')

    def test_deleting_the_running_item_stops_its_clock(self):
        items = self.plan(items=(('One',60),('Two',60)))
        self.store.sprint_start()
        self.time = 20
        self.store.delete_sprint_item(items[0]['id'])
        self.assertIsNone(self.store.sprinting)
        self.time = 300
        self.store.checkpoint()
        self.assertEqual(self.store.sprint_items()[0]['elapsed'],0)

    def test_sprint_survives_restart_paused_without_counting_downtime(self):
        self.plan(items=(('One',300),))
        self.store.sprint_start()
        self.time = 30
        self.store.close()
        self.time = 9000  # Closed-app downtime is never spent.
        self.store = Store(self.path,lambda:self.time)
        item = self.store.sprint_items()[0]
        self.assertEqual((item['state'],item['elapsed']),('paused',30))
        self.assertEqual(self.store.sprint()['name'],'Work')
        self.assertEqual(self.store.sprint_current()['name'],'One')

    def test_a_new_sprint_sets_the_old_one_aside(self):
        self.plan(name='Morning',items=(('One',60),))
        self.store.sprint_start()
        self.time = 25
        self.store.new_sprint('Afternoon')
        self.assertEqual(self.store.sprint()['name'],'Afternoon')
        self.assertEqual(self.store.sprint_items(),[])
        self.assertIsNone(self.store.sprinting)
        self.assertIsNone(self.store.sprint_current())

    def test_sprint_item_validation(self):
        self.store.new_sprint('Work')
        for name, allocated in [('',60),('   ',60),('One',0),('One',-60)]:
            with self.assertRaises(ValueError):
                self.store.add_sprint_item(name,allocated)
        for blank in ['','  ']:
            with self.assertRaises(ValueError):
                self.store.new_sprint(blank)
            with self.assertRaises(ValueError):
                self.store.rename_sprint(blank)
        self.store.rename_sprint(' Small tasks ')
        self.assertEqual(self.store.sprint()['name'],'Small tasks')

    def test_sprint_time_stays_out_of_tasks_and_reports(self):
        self.plan(items=(('One',60),))
        self.store.sprint_start()
        self.time = 45
        self.store.sprint_advance()
        self.assertEqual(self.store.tasks(),[])
        self.assertEqual(self.store.totals(),[])
        self.assertEqual(self.store.report('2000-01-01','2999-12-31'),[])


    def dated_run(self, started='2026-03-02T12:00:00Z', ended='2026-03-02T12:30:00Z'):
        """Pin the current run to a known UTC start, the way a real one is dated."""
        with self.store.db:
            self.store.db.execute('UPDATE sprints SET started=?,ended=? WHERE id=(SELECT MAX(id) FROM sprints)',
                                  (started,ended))
        return datetime.fromisoformat(started.replace('Z','+00:00')).astimezone().date().isoformat()

    def test_sprint_report_rows_per_item_and_per_run(self):
        self.plan(name='Morning',items=(('One',60),('Two',120)))
        self.store.sprint_start()
        self.time = 90  # One takes 90s against a 60s countdown.
        self.store.sprint_advance()
        self.time = 150  # Two is skipped after 60s of a 120s countdown.
        self.store.sprint_advance('skipped')
        day = self.dated_run()
        items = self.store.sprint_report(day,day)
        self.assertEqual([(r['day'],r['sprint'],r['position'],r['item'],r['planned'],r['actual'],
                           r['delta'],r['state']) for r in items],
                         [(day,'Morning',1,'One',60,90,30,'done'),
                          (day,'Morning',2,'Two',120,60,-60,'skipped')])
        runs = self.store.sprint_runs(day,day)
        self.assertEqual([(r['day'],r['sprint'],r['items'],r['done'],r['skipped'],
                           r['planned'],r['actual'],r['delta']) for r in runs],
                         [(day,'Morning',2,1,1,180,150,-30)])
        for out_of_range in [('2027-01-01','2027-12-31'),('2020-01-01','2020-12-31')]:
            self.assertEqual(self.store.sprint_report(*out_of_range),[])
            self.assertEqual(self.store.sprint_runs(*out_of_range),[])

    def test_a_run_keeps_the_day_it_started_on(self):
        self.plan(name='Late night',items=(('One',60),))
        self.store.sprint_start()
        self.time = 60
        self.store.sprint_advance()
        day = self.dated_run('2026-03-02T23:50:00Z','2026-03-03T00:20:00Z')
        self.assertEqual([r['day'] for r in self.store.sprint_runs(day,day)],[day])

    def test_sprints_that_were_never_started_are_not_runs(self):
        self.plan(name='Planned only',items=(('One',60),))
        whole = ('2000-01-01','2999-12-31')
        self.assertEqual(self.store.sprint_report(*whole),[])
        self.assertEqual(self.store.sprint_runs(*whole),[])
        self.store.sprint_start()  # Starting it is what makes it a run.
        self.assertEqual(len(self.store.sprint_runs(*whole)),1)
        self.assertEqual(len(self.store.sprint_report(*whole)),1)

    def test_each_sprint_is_reported_on_its_own(self):
        self.plan(name='First',items=(('One',60),))
        self.store.sprint_start()
        self.time = 30
        self.store.sprint_advance()
        self.plan(name='Second',items=(('Two',60),('Three',60)))
        self.store.sprint_start()
        self.time = 100
        self.store.sprint_advance()
        whole = ('2000-01-01','2999-12-31')
        self.assertEqual([(r['sprint'],r['items']) for r in self.store.sprint_runs(*whole)],
                         [('First',1),('Second',2)])
        self.assertEqual([(r['sprint'],r['position'],r['item']) for r in self.store.sprint_report(*whole)],
                         [('First',1,'One'),('Second',1,'Two'),('Second',2,'Three')])

    def test_sprint_tables_and_csv_match_what_is_shown(self):
        self.assertEqual([signed(0),signed(90),signed(-90),signed(-0.4)],
                         ['00:00:00','+00:01:30','-00:01:30','00:00:00'])
        self.plan(name='Morning',items=(('One',60),('Two',120)))
        self.store.sprint_start()
        self.time = 90
        self.store.sprint_advance()
        self.time = 150
        self.store.sprint_advance('skipped')
        day = self.dated_run()
        for rows, per_item, columns, expected in [
                (self.store.sprint_report(day,day),True,SPRINT_ITEM_COLUMNS,
                 [day,'07:00:00','Morning','1','One','00:01:00','00:01:30','+00:00:30','done']),
                (self.store.sprint_runs(day,day),False,SPRINT_RUN_COLUMNS,
                 [day,'07:00:00','Morning','2','1','1','00:03:00','00:02:30','-00:00:30'])]:
            table = sprint_table(rows,per_item)
            self.assertEqual(len(table[0]),len(columns))
            self.assertEqual(table[0][:1]+table[0][2:],expected[:1]+expected[2:])  # Not the local time.
            out = io.StringIO()
            sprint_csv(table,columns,out)
            lines = out.getvalue().splitlines()
            self.assertEqual(lines[0],','.join(columns))
            self.assertEqual(len(lines),len(table)+1)
            self.assertEqual(lines[1].split(',')[2:],expected[2:])


    def test_an_item_the_run_never_reached_has_no_delta(self):
        self.plan(name='Morning',items=(('One',60),('Two',120)))
        self.store.sprint_start()
        self.time = 30
        self.store.sprint_finish()  # Two is never reached.
        rows = self.store.sprint_report('2000-01-01','2999-12-31')
        self.assertEqual([r['state'] for r in rows],['paused','waiting'])
        self.assertEqual([r[7] for r in sprint_table(rows,True)],['-00:00:30','—'])

    def test_sprints_are_listed_oldest_first_and_read_back_by_id(self):
        self.plan(name='Morning',items=(('One',60),))
        first = self.store.sprint()['id']
        self.store.sprint_start()
        self.time = 45
        self.store.sprint_advance()
        self.plan(name='Afternoon',items=(('Two',120),('Three',180)))
        second = self.store.sprint()['id']
        listed = self.store.sprints()
        self.assertEqual([(s['id'],s['name']) for s in listed],[(first,'Morning'),(second,'Afternoon')])
        self.assertEqual(self.store.sprint()['id'],second)  # No id means the current one.
        earlier = self.store.sprint(first)
        self.assertEqual((earlier['name'],earlier['items'],earlier['planned'],earlier['spent']),
                         ('Morning',1,60,45))
        self.assertEqual([i['name'] for i in self.store.sprint_items(first)],['One'])
        self.assertEqual([i['name'] for i in self.store.sprint_items()],['Two','Three'])
        self.assertIsNone(self.store.sprint(-1))
        self.assertEqual(self.store.sprint_items(-1),[])

    def test_reading_an_earlier_sprint_leaves_the_current_one_running(self):
        self.plan(name='Morning',items=(('One',60),))
        first = self.store.sprint()['id']
        self.plan(name='Afternoon',items=(('Two',600),))
        self.store.sprint_start()
        self.time = 90
        self.store.sprint(first)
        self.store.sprint_items(first)
        self.store.checkpoint()
        self.assertEqual(self.store.sprint_items()[0]['elapsed'],90)
        self.assertIsNotNone(self.store.sprinting)

if __name__ == '__main__':
    unittest.main()
