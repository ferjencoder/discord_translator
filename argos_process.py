"""A single restartable inference process; the Discord process stays lightweight."""
import asyncio
import json
from pathlib import Path
import sys


class ArgosWorkerError(RuntimeError):
    pass


class ArgosProcess:
    def __init__(self):
        self.process = None
        self.restarts = 0

    async def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

    async def request(self, **payload):
        try:
            if self.process is None or self.process.returncode is not None:
                await self.close()
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable, '-u', str(Path(__file__).with_name('argos_worker.py')),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    limit=1024 * 1024)
            process = self.process
            process.stdin.write((json.dumps(payload) + '\n').encode())
            await process.stdin.drain()
            line = await process.stdout.readline()
            if not line:
                raise ArgosWorkerError('Translation worker exited')
            response = json.loads(line)
            if not response['ok']:
                # Worker sends only exception class names, never message contents.
                raise ArgosWorkerError(response['error'])
            return response['result']
        except BaseException:
            # A timeout cancels this coroutine. Reap the old process before the
            # service releases its slot; no abandoned native model keeps running.
            await self.close()
            self.restarts += 1
            raise

    async def translate(self, text, source, target):
        return await self.request(action='translate', text=text, source=source, target=target)

    async def validate(self):
        return await self.request(action='validate')
