import unittest
from unittest.mock import patch

from progress_eta import ScanETA, duration


class ScanETATests(unittest.TestCase):
    def test_first_batch_includes_time_already_spent_processing(self):
        with patch('progress_eta.monotonic', return_value=100) as clock:
            eta = ScanETA()
            self.assertIn('считаем…', eta.text())
            clock.return_value = 110
            eta.update('prepare', 25, 100)
            self.assertEqual(eta.text(), 'Прошло 00:10 · Осталось на этапе: ≈ 00:30')
            clock.return_value = 115
            self.assertIn('≈ 01:00', eta.text())  # A stalled batch reduces average speed.

    def test_stage_rate_resets_and_new_scan_does_not_reuse_old_time(self):
        with patch('progress_eta.monotonic', return_value=0) as clock:
            eta = ScanETA()
            clock.return_value = 60
            eta.update('prepare', 100, 100)
            self.assertIn('этап завершён', eta.text())
            clock.return_value = 62
            eta.update('index', 50, 100)
            self.assertIn('считаем…', eta.text())
            clock.return_value = 65
            eta.update('index', 75, 100)
            self.assertEqual(eta.text(), 'Прошло 01:05 · Осталось на этапе: ≈ 00:02')
            clock.return_value = 100
            fresh = ScanETA()
            self.assertEqual(fresh.text(), 'Прошло 00:00 · Осталось на этапе: считаем…')

    def test_empty_progress_and_duration_boundaries(self):
        with patch('progress_eta.monotonic', return_value=10):
            eta = ScanETA()
            eta.update('prepare', 0, 0)
            self.assertIn('считаем…', eta.text())
        self.assertEqual(duration(3661), '1:01:01')
        self.assertEqual(duration(-1), '00:00')

    def test_cached_prefix_does_not_inflate_speed_of_uncached_batches(self):
        with patch('progress_eta.monotonic', return_value=0) as clock:
            eta = ScanETA()
            for when, done in ((.1,1000),(.2,2000),(10.2,2050),(20.2,2100)):
                clock.return_value = when
                eta.update('prepare',done,2200)
                result = eta.text()
                if when == 10.2:
                    self.assertIn('≈ 00:30', result)
            self.assertIn('≈ 00:20', result)

    def test_estimate_refreshes_every_five_seconds_and_forgets_old_rate(self):
        with patch('progress_eta.monotonic', return_value=0) as clock:
            eta = ScanETA()
            for when, done, remaining in ((5,50,'01:35'), (6,60,'01:34'),
                                           (10,100,'01:30'), (15,110,'02:28'),
                                           (20,120,'07:20')):
                clock.return_value = when
                eta.update('prepare',done,1000)
                self.assertIn('≈ '+remaining, eta.text())

    def test_window_boundary_interpolates_a_partially_overlapping_batch(self):
        with patch('progress_eta.monotonic', return_value=0) as clock:
            eta = ScanETA()
            clock.return_value = 4
            eta.update('prepare',40,100)
            clock.return_value = 12
            eta.update('prepare',60,100)
            self.assertIn('≈ 00:10', eta.text())

    def test_no_recent_progress_does_not_keep_a_stale_cached_rate(self):
        with patch('progress_eta.monotonic', return_value=0) as clock:
            eta = ScanETA()
            clock.return_value = 5
            eta.update('prepare',50,100)
            self.assertIn('≈ 00:05', eta.text())
            clock.return_value = 15
            self.assertIn('считаем…', eta.text())


if __name__ == '__main__':
    unittest.main()
