"""Windows desktop runner: one instance, rotating logs, bounded restart delay."""
import argparse
import asyncio
import ctypes
from contextlib import contextmanager
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent


def configure_local():
    os.chdir(ROOT)
    # Override hosting-only settings without changing the shared .env file.
    os.environ['SELF_PING_ENABLED'] = 'false'
    os.environ['RENDER_EXTERNAL_URL'] = ''
    os.environ['HEALTH_BIND_HOST'] = '127.0.0.1'


@contextmanager
def single_instance(path):
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('Local translator is already running') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


async def preflight():
    from settings import load_settings
    from translator import TranslationService
    settings = load_settings()
    service = TranslationService(concurrency=settings.translation_concurrency,
                                 start_interval_seconds=settings.translation_start_interval_seconds,
                                 retries=settings.translation_retries,
                                 task_timeout_seconds=settings.translation_task_timeout_seconds)
    try:
        count = await service.validate()
        print(f'Local configuration valid: {len(settings.channels)} languages, {count} models. No Discord connection made.')
    finally:
        await service.close()


def restart_delay(failures):
    return min(60, 5 * 2 ** min(failures, 4))


@contextmanager
def keep_awake():
    # Prevent idle sleep only while this runner's thread lives. The display may
    # turn off; exiting the runner restores normal power behavior automatically.
    set_state = ctypes.windll.kernel32.SetThreadExecutionState
    if not set_state(0x80000001):  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        raise RuntimeError('Windows could not prevent idle sleep')
    try:
        yield
    finally:
        set_state(0x80000000)


def run():
    from bot import run_bot
    from settings import ConfigError, load_settings
    logs = ROOT / 'data' / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / 'translator.log', maxBytes=5 * 1024 * 1024,
                                  backupCount=5, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True,
                        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    log = logging.getLogger('local-runner')
    with single_instance(ROOT / 'data' / 'local-runner.lock'), keep_awake():
        failures = 0
        while True:
            started = time.monotonic()
            try:
                settings = load_settings()
                log.info('Starting local translator; health endpoint is local-only')
                asyncio.run(run_bot(settings))
            except KeyboardInterrupt:
                return
            except ConfigError:
                log.error('Invalid configuration; run local_runner.py --check for details')
            except Exception as exc:
                log.error('Bot stopped (%s); preparing restart', type(exc).__name__)
            if time.monotonic() - started >= 300:
                failures = 0
            delay = restart_delay(failures)
            failures += 1
            log.warning('Restarting bot in %d seconds', delay)
            time.sleep(delay)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Validate settings and installed models offline')
    args = parser.parse_args()
    configure_local()
    if args.check:
        asyncio.run(preflight())
    else:
        try:
            run()
        except KeyboardInterrupt:
            pass
