"""Private line-based worker protocol. Never connects to Discord."""
import contextlib
import json
import os
import sys

from translator import ArgosBackend, TranslationService
from check_argos_models import validate_models


def main():
    # Reserve stdout for the protocol, even if a dependency prints diagnostics.
    output = sys.stdout
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink):
        service = TranslationService(concurrency=1, start_interval_seconds=0,
                                     retries=1, task_timeout_seconds=90, backend=ArgosBackend())
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if request['action'] == 'validate':
                    result = validate_models()
                elif request['action'] == 'translate':
                    result = service._translate_sync(request['text'], request['source'], request['target'])
                else:
                    raise ValueError('Unknown worker action')
                response = {'ok': True, 'result': result}
            except Exception as exc:
                response = {'ok': False, 'error': type(exc).__name__}
            output.write(json.dumps(response) + '\n')
            output.flush()


if __name__ == '__main__':
    main()
