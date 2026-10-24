"""Rapid file navigation cancels obsolete work without serializing the UI."""
import threading
import unittest

from pubnexus.preview_requests import PreviewRequests, PreviewCancelled


class PreviewRequestTests(unittest.TestCase):
    def test_new_selection_cancels_previous_and_late_older_request_is_rejected(self):
        requests = PreviewRequests(slots=2)
        with requests.work('reader', 1, 'A') as old_cancel:
            with requests.work('reader', 2, 'B') as new_cancel:
                self.assertTrue(old_cancel())
                self.assertFalse(new_cancel())
            with self.assertRaises(PreviewCancelled):
                with requests.work('reader', 1, 'A'):
                    self.fail('An obsolete document must not enter parsing')

    def test_same_selection_metadata_window_and_images_share_cancellation(self):
        requests = PreviewRequests(slots=2)
        with requests.work('reader', 3, 'A') as first:
            with requests.work('reader', 3, 'A') as child:
                self.assertFalse(first())
                self.assertFalse(child())
                requests.cancel('reader', 4)
                self.assertTrue(first())
                self.assertTrue(child())
        with self.assertRaises(PreviewCancelled):
            with requests.work('reader', 3, 'B'):
                self.fail('Closed selection must not restart')

    def test_waiting_work_is_cancelled_before_it_can_parse(self):
        requests = PreviewRequests(slots=1)
        entered = threading.Event()
        stopped = threading.Event()
        failures = []

        def waiting():
            entered.set()
            try:
                with requests.work('reader', 2, 'B'):
                    failures.append('obsolete work entered parser')
            except PreviewCancelled:
                stopped.set()

        with requests.work('other-reader', 1, 'X'):
            thread = threading.Thread(target=waiting)
            thread.start()
            self.assertTrue(entered.wait(1))
            requests.cancel('reader', 3)
            self.assertTrue(stopped.wait(1), 'Cancellation must not wait for an occupied slot')
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def test_eviction_cancels_old_clients_and_registry_stays_bounded(self):
        requests = PreviewRequests(slots=2, clients=2)
        with requests.work('one', 1, 'A') as old:
            with requests.work('two', 1, 'B'):
                requests.cancel('three', 1)
                self.assertTrue(old())
                self.assertEqual(len(requests._current), 2)


if __name__ == '__main__':
    unittest.main()
