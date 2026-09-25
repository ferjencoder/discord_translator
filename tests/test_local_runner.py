import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_runner import keep_awake, restart_delay, single_instance


class LocalRunnerTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows power request')
    def test_awake_request_released_on_error(self):
        with patch('local_runner.ctypes.windll.kernel32.SetThreadExecutionState', return_value=1) as state:
            with self.assertRaises(ValueError):
                with keep_awake():
                    raise ValueError('test')
            self.assertEqual([call.args[0] for call in state.call_args_list], [0x80000001, 0x80000000])

    def test_restart_backoff_is_bounded(self):
        self.assertEqual([restart_delay(i) for i in range(6)], [5, 10, 20, 40, 60, 60])

    @unittest.skipUnless(os.name == 'nt', 'Windows runner lock')
    def test_second_runner_is_rejected_and_lock_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runner.lock'
            with single_instance(path):
                with self.assertRaises(RuntimeError):
                    with single_instance(path):
                        self.fail('Duplicate runner acquired lock')
            with single_instance(path):
                pass
