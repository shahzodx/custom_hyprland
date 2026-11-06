import importlib.util
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'app_usage', Path(__file__).resolve().parents[1] / 'scripts/app-usage.py')
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        state = patch.object(usage, 'STATE_DIR', Path(self.directory.name))
        state.start()
        self.addCleanup(state.stop)
        self.database = usage.open_database()
        self.addCleanup(self.database.close)
        self.recorder = usage.Recorder(self.database)
        self.start = datetime.now(usage.LOCAL_TZ).replace(hour=12, minute=0, second=0,
                                                       microsecond=0).timestamp()

    def sample(self, seconds, app='firefox', eligible=True):
        self.recorder.sample(app, eligible, self.start + seconds, 100 + seconds)

    def test_switching_windows_counts_each_interval_once_for_previous_app(self):
        self.sample(0)
        self.sample(1)
        self.sample(2, 'kitty')
        self.sample(3, 'kitty')
        self.sample(4, 'firefox')
        self.sample(5, 'firefox')
        self.recorder.flush()
        report = usage.report()
        times = {app['app']: app['seconds'] for app in report['apps']}
        self.assertEqual(times, {'firefox': 3, 'kitty': 2})
        self.assertEqual(report['total'], 5)

    def test_idle_lock_and_no_active_window_do_not_accumulate_time(self):
        self.sample(0)
        self.sample(1)
        self.sample(2, '', False)
        self.sample(3, '', False)
        self.sample(4)
        self.sample(5)
        self.sample(6, '')
        self.sample(7, '')
        self.sample(8)
        self.sample(9)
        self.recorder.flush()
        self.assertEqual(usage.report()['total'], 3)

    def test_long_sampling_gap_is_not_added_after_resume(self):
        self.sample(0)
        self.sample(1)
        self.sample(3601)
        self.sample(3602)
        self.recorder.flush()
        self.assertEqual(usage.report()['total'], 2)

    def test_suspend_and_clock_correction_are_not_counted(self):
        self.recorder.sample('firefox', True, self.start, 10)
        self.recorder.sample('firefox', True, self.start + 3600, 11)
        self.recorder.sample('firefox', True, self.start + 3601, 12)
        self.recorder.sample('firefox', True, self.start - 100, 13)
        self.recorder.sample('firefox', True, self.start - 99, 14)
        self.recorder.flush()
        self.assertEqual(usage.report()['total'], 2)

    def test_midnight_splits_usage_between_local_dates(self):
        start = datetime(2026, 11, 6, 23, 59, 59, tzinfo=usage.LOCAL_TZ).timestamp()
        self.recorder.sample('kitty', True, start, 10)
        self.recorder.sample('kitty', True, start + 2, 12)
        self.recorder.flush()
        self.assertEqual(self.database.execute(
            'SELECT day, seconds FROM usage ORDER BY day').fetchall(),
            [('2026-11-06', 1.0), ('2026-11-07', 1.0)])

    def test_totals_persist_and_today_excludes_older_days(self):
        self.sample(0)
        self.sample(1)
        self.recorder.flush()
        with self.database:
            self.database.execute('INSERT INTO usage VALUES (?, ?, ?)',
                                  ('2000-01-01', 'firefox', 120))
        self.assertEqual(usage.report()['total'], 1)
        self.assertEqual(usage.report('all')['total'], 121)
        restarted = usage.Recorder(self.database)
        restarted.sample('kitty', True, self.start + 100, 100)
        restarted.sample('kitty', True, self.start + 101, 101)
        restarted.flush()
        restarted.flush()
        self.assertEqual(usage.report('all')['total'], 122)

    def test_idle_marker_from_previous_process_is_ignored(self):
        usage.write_json('idle.json', {'pid': 42, 'start': '100'})
        with patch.object(usage, 'process_identity', return_value=('hypridle', '100')):
            self.assertTrue(usage.is_idle())
        with patch.object(usage, 'process_identity', return_value=('hypridle', '101')):
            self.assertFalse(usage.is_idle())
        with patch.object(usage, 'process_identity', return_value=None):
            self.assertFalse(usage.is_idle())

if __name__ == '__main__':
    unittest.main()
