import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('work_timer', Path(__file__).resolve().parents[1] / 'scripts/work-timer.py')
timer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timer)


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = patch.object(timer, 'STATE_DIR', Path(self.temp.name))
        self.directory.start()
        self.addCleanup(self.directory.stop)
        self.now = 1000
        self.time = patch.object(timer.time, 'time', side_effect=lambda: self.now)
        self.time.start()
        self.addCleanup(self.time.stop)
        self.notify = patch.object(timer.subprocess, 'Popen')
        self.notification = self.notify.start()
        self.addCleanup(self.notify.stop)

    def test_pause_resume_preserves_time_and_counts_while_window_closed(self):
        timer.update('start', 1500)
        self.now += 31
        state = timer.update('pause')
        self.assertEqual(state['remaining'], 1469)
        self.now += 500
        self.assertEqual(timer.update()['remaining'], 1469)
        timer.update('resume')
        self.now += 69
        self.assertEqual(timer.update()['remaining'], 1400)

    def test_completion_notifies_once_across_repeated_readers(self):
        timer.update('start', 60)
        self.now += 61
        self.assertEqual(timer.update()['mode'], 'done')
        self.assertEqual(timer.update()['remaining'], 0)
        self.notification.assert_called_once()

    def test_reset_cancels_and_keeps_selected_duration(self):
        timer.update('start', 3000)
        timer.update('reset')
        self.now += 4000
        state = timer.update()
        self.assertEqual((state['mode'], state['remaining']), ('idle', 3000))
        self.notification.assert_not_called()

    def test_running_status_does_not_write_state_each_tick(self):
        timer.update('start', 1500)
        before = (timer.STATE_DIR / 'state.json').read_bytes()
        self.now += 42
        self.assertEqual(timer.update()['remaining'], 1458)
        self.assertEqual((timer.STATE_DIR / 'state.json').read_bytes(), before)

    def test_bad_state_recovers(self):
        (timer.STATE_DIR / 'state.json').write_text('{broken')
        self.assertEqual(timer.update()['mode'], 'idle')

    def test_invalid_duration_is_rejected(self):
        for duration in (0, -1, 10801):
            with self.assertRaises(ValueError):
                timer.update('start', duration)

    def test_display_rounds_up(self):
        self.assertEqual(timer.clock_text(59.1), '01:00')
        self.assertEqual(timer.clock_text(3000), '50:00')


if __name__ == '__main__':
    unittest.main()
