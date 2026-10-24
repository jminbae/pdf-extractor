"""Owned-process accounting and conservative dispatch, without real documents."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pubnexus import resource_budget as resources


GIB = 1024 ** 3
MIB = 1024 ** 2


class ResourceBudgetTests(unittest.TestCase):
    def policy(self):
        return resources.Policy(physical_cpus=16, logical_cpus=24)

    def snapshot(self, **changes):
        return dict(total_bytes=16 * GIB, available_bytes=12 * GIB,
                    rss_bytes=300 * MIB, private_bytes=200 * MIB,
                    main_bytes=128 * MIB, system_cpu_percent=10, **changes)

    def test_cpu_capacity_is_bounded_and_small_machines_keep_one_slot(self):
        self.assertEqual(self.policy().max_workers, 8)
        self.assertEqual(resources.Policy(physical_cpus=128, logical_cpus=256).max_workers, 10)
        self.assertEqual(resources.Policy(physical_cpus=1, logical_cpus=1).max_workers, 1)
        self.assertEqual(resources.Policy(physical_cpus=0, logical_cpus=24).max_workers, 6)

    def test_active_estimates_and_actual_memory_share_one_budget(self):
        policy = self.policy()
        allowed, _, estimate = policy.can_start('book.pdf', [2 * GIB, 2 * GIB], self.snapshot())
        self.assertTrue(allowed)
        self.assertEqual(estimate, 2 * GIB)
        self.assertFalse(policy.can_start('book.pdf', [3 * GIB, 3 * GIB], self.snapshot())[0])
        measured = self.snapshot()
        measured['rss_bytes'] = 7 * GIB
        self.assertFalse(policy.can_start('book.pdf', [2 * GIB], measured)[0])

    def test_cpu_reserve_and_concurrency_delay_only_new_work(self):
        policy = self.policy()
        cpu = self.snapshot()
        cpu['system_cpu_percent'] = 90
        self.assertFalse(policy.can_start('a.txt', [], cpu)[0])
        low = self.snapshot()
        low['available_bytes'] = 3 * GIB
        self.assertFalse(policy.can_start('book.pdf', [2 * GIB], low)[0])
        self.assertFalse(policy.can_start('a.txt', [128 * MIB] * 8, self.snapshot())[0])

    def test_small_machine_single_exception_never_ignores_emergency_or_busy_system(self):
        policy = self.policy()
        small = self.snapshot()
        small.update(total_bytes=4 * GIB, available_bytes=3 * GIB)
        self.assertTrue(policy.can_start('book.pdf', [], small)[0])
        self.assertFalse(policy.can_start('book.pdf', [2 * GIB], small)[0])
        small['available_bytes'] = 600 * MIB
        self.assertFalse(policy.can_start('book.pdf', [], small)[0])
        small['available_bytes'] = 511 * MIB
        self.assertFalse(policy.can_start('book.pdf', [], small)[0])
        pressured = self.snapshot()
        pressured['available_bytes'] = GIB
        self.assertFalse(policy.can_start('book.pdf', [], pressured)[0])
        self.assertFalse(policy.can_start('book.pdf', [], {})[0])

    def test_observed_peak_only_raises_future_type_reservation(self):
        policy = self.policy()
        self.assertEqual(policy.estimate('a.csv'), 128 * MIB)
        self.assertEqual(policy.estimate('a.docx'), 512 * MIB)
        policy.observe('large.pdf', 3 * GIB)
        raised = policy.estimate('another.pdf')
        self.assertGreater(raised, 3 * GIB)
        policy.observe('small.pdf', 50 * MIB)
        self.assertEqual(policy.estimate('another.pdf'), raised)

    def test_sample_counts_owned_tree_once_and_never_queries_general_process_list(self):
        from pubnexus.external_documents import _stop_owned_process
        with tempfile.TemporaryDirectory(prefix='pnx-resource-') as temporary:
            ready = Path(temporary) / 'ready'
            script = ('import pathlib,time;data=bytearray(24*1024*1024);'
                      'pathlib.Path(' + repr(str(ready)) + ').write_text("ready");time.sleep(20)')
            child = subprocess.Popen([sys.executable, '-B', '-c', script],
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                with patch.object(resources.psutil, 'process_iter', side_effect=AssertionError('general process enumeration')):
                    observed = resources.sample({'fixture': child.pid, 'duplicate': child.pid})
                self.assertGreater(observed['total_bytes'], 0)
                self.assertGreater(observed['main_bytes'], 0)
                self.assertIn(child.pid, observed['jobs']['fixture']['pids'])
                self.assertGreater(observed['jobs']['fixture']['rss_bytes'], 20 * MIB)
                pids = [p['pid'] for p in observed['processes']]
                self.assertEqual(len(pids), len(set(pids)))
                self.assertEqual(observed['rss_bytes'], sum(p['rss_bytes'] for p in observed['processes']))
                self.assertEqual(observed['jobs']['fixture']['pids'], observed['jobs']['duplicate']['pids'])
                self.assertIn(os.getpid(), pids)
                json.dumps(observed)
            finally:
                _stop_owned_process(child)


if __name__ == '__main__':
    unittest.main()
