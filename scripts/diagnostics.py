"""Small rotating JSONL diagnostics; never record prompts or source text."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import time

def user_data_root():
    """Writable per-user runtime data, independent of skill installation."""
    local_data = os.environ.get('LOCALAPPDATA')
    if local_data:
        root = Path(local_data)
    elif sys.platform == 'win32':
        root = Path.home() / 'AppData' / 'Local'
    else:
        root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')
    return root / 'WZMH' / 'CodeAI'


def default_log_path():
    """Keep logs in the current user's local data, independent of skill installation."""
    return user_data_root() / 'logs' / 'diagnostics.jsonl'


LOG_PATH = default_log_path()


@contextmanager
def stage_timer(fields, name):
    """Accumulate wall time for a fixed script stage, including failed attempts.

    Callers use constant stage names, never source paths, questions or PDF text.
    Disabled diagnostics do not read the clock or change helper return values.
    """
    if fields is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        stages = fields.setdefault('stages_ms', {})
        stages[name] = round(stages.get(name, 0) + (time.perf_counter() - started) * 1000, 3)


@contextmanager
def diagnostic_run(operation):
    started = time.perf_counter()
    fields = {"operation": operation}
    status = "ok"
    try:
        yield fields
    except BaseException as error:
        status = "error"
        fields["error_type"] = type(error).__name__
        # Exception messages may contain quoted Code text; store only the type/code.
        if isinstance(error, SystemExit):
            status = "ok" if error.code in (None, 0) else "error"
            fields["exit_code"] = error.code if isinstance(error.code, int) else 1
        raise
    finally:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("obc-reference-diagnostics")
            logger.propagate = False
            logger.setLevel(logging.INFO)
            # Logging must never delay or fail a lookup if the file is locked/full.
            logging.raiseExceptions = False
            handler = RotatingFileHandler(LOG_PATH, maxBytes=131072, backupCount=1,
                                          encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            try:
                logger.info(json.dumps({"time_utc": datetime.now(timezone.utc).isoformat(),
                                        "status": status,
                                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                                        **fields}, ensure_ascii=True))
            finally:
                logger.removeHandler(handler)
                handler.close()
        except Exception:
            pass
