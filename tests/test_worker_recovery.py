import asyncio
import sys
import time
import unittest
from unittest.mock import Mock, patch

from argos_process import ArgosProcess, ArgosWorkerError
from translator import ArgosBackend, TranslationService


# Exercise actual process termination and pipe cleanup without downloading/loading models.
WORKER = '''
import json, sys, time, os
for line in sys.stdin:
    request = json.loads(line)
    if request['text'] == 'hang':
        time.sleep(60)
    if request['text'] == 'crash':
        os._exit(7)
    print(json.dumps({'ok': True, 'result': 'hola'}), flush=True)
'''


class WorkerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        original = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            return await original(sys.executable, '-u', '-c', WORKER, **kwargs)
        self.patch = patch('argos_process.asyncio.create_subprocess_exec', side_effect=spawn)
        self.patch.start()
        self.worker = ArgosProcess()

    async def asyncTearDown(self):
        await self.worker.close()
        self.patch.stop()

    async def test_timeout_reaps_process_and_next_request_recovers(self):
        service = TranslationService(concurrency=1, start_interval_seconds=0,
                                     retries=1, task_timeout_seconds=0.5, backend=self.worker)
        result = await service.translate('hang', 'en', 'es')
        self.assertFalse(result.ok)
        self.assertEqual(result.timeout_errors, 1)
        self.assertIsNone(self.worker.process)
        service._task_timeout = 5
        self.assertTrue((await service.translate('hello', 'en', 'es')).ok)
        self.assertEqual(self.worker.restarts, 1)

    async def test_crash_does_not_poison_next_request(self):
        with self.assertRaises(ArgosWorkerError):
            await self.worker.translate('crash', 'en', 'es')
        self.assertIsNone(self.worker.process)
        self.assertEqual(await self.worker.translate('hello', 'en', 'es'), 'hola')

    async def test_cancellation_reaps_worker_before_returning(self):
        task = asyncio.create_task(self.worker.translate('hang', 'en', 'es'))
        while self.worker.process is None:
            await asyncio.sleep(0.01)
        process = self.worker.process
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(process.returncode)
        self.assertIsNone(self.worker.process)
        self.assertEqual(await self.worker.translate('hello', 'en', 'es'), 'hola')

    async def test_queued_requests_get_full_inference_timeout(self):
        backend = Mock()
        def translate(*args):
            time.sleep(0.12)
            return 'hola'
        backend.translate.side_effect = translate
        service = TranslationService(concurrency=1, start_interval_seconds=0,
                                     retries=1, task_timeout_seconds=0.3, backend=backend)
        results = await asyncio.gather(*(service.translate('hello', 'en', 'es') for _ in range(5)))
        self.assertTrue(all(result.ok for result in results))


class PivotReuseTests(unittest.TestCase):
    def test_english_result_reused_across_destinations_and_invalidated(self):
        backend = ArgosBackend()
        backend._leg = Mock(side_effect=lambda text, source, target: f'{text}-{target}')
        backend.translate('hallo', 'de', 'en')
        backend.translate('hallo', 'de', 'es')
        backend.translate('hallo', 'de', 'fr')
        pivots = [call for call in backend._leg.call_args_list if call.args[1:] == ('de', 'en')]
        self.assertEqual(len(pivots), 1)
        backend.translate('anders', 'de', 'es')
        pivots = [call for call in backend._leg.call_args_list if call.args[1:] == ('de', 'en')]
        self.assertEqual(len(pivots), 2)
