"""Small rotating JSONL diagnostics; never record prompts or source text."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
from logging.handlers import RotatingFileHandler
import sys
import time

LOG_PATH = Path(__file__).resolve().parent.parent / ".cache" / "diagnostics.jsonl"


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
