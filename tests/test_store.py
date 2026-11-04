import tempfile
import unittest
from pathlib import Path
from store import Store, duration


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

    def test_duration_validation(self):
        self.assertEqual(duration('25'),1500)
        self.assertEqual(duration('01:02:03'),3723)
        self.assertEqual(duration('1.5'),90)
        for invalid in ['-1','nan','inf','00:70:00','1:30','','-1:00:00']:
            with self.assertRaises(ValueError):
                duration(invalid)


if __name__ == '__main__':
    unittest.main()
