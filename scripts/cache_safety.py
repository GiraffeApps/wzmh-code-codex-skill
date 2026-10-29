"""Bounded coordination and non-destructive recovery for disposable search indexes."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import sqlite3
import time
import uuid

LOCK_TIMEOUT = 5.0
SQLITE_TIMEOUT = 0.25
BUSY_RETRIES = 2


class CacheBusyError(RuntimeError):
    def __init__(self, message, wait_ms=0):
        super().__init__(message)
        self.wait_ms = wait_ms


class CacheRecoveryError(RuntimeError):
    pass


def primary_code(error):
    code = getattr(error, 'sqlite_errorcode', None)
    return code & 255 if isinstance(code, int) else None


def is_busy(error):
    return primary_code(error) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def is_corrupt(error):
    return primary_code(error) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


def _lock(stream):
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(stream):
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def cache_lock(path, timeout=None):
    """Serialize CLI users of one index; OS locks release even after a process dies."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + '.lock')
    if lock_path.is_symlink():
        raise CacheRecoveryError('Search cache lock must not be a symbolic link.')
    started = time.monotonic()
    deadline = started + (LOCK_TIMEOUT if timeout is None else timeout)
    acquired = False
    with lock_path.open('a+b') as stream:
        try:
            while not acquired:
                try:
                    _lock(stream)
                    acquired = True
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CacheBusyError(
                            'Search index is busy. Try again shortly; no cache files were reset.',
                            round((time.monotonic() - started) * 1000)) from error
                    time.sleep(min(0.05, remaining))
            yield round((time.monotonic() - started) * 1000)
        finally:
            if acquired:
                try:
                    _unlock(stream)
                except OSError:
                    pass  # Closing the descriptor also releases its OS lock.
    # Keep the empty lock file: unlinking it would permit races between openers.


def quarantine_cache(path):
    """Caller holds cache_lock and has closed SQLite. Preserve files, never overwrite."""
    path = Path(path)
    if path.is_symlink() or path.suffix.lower() == '.pdf' or not path.is_file():
        raise CacheRecoveryError('Refusing to move an unsafe search-cache path.')
    path = path.resolve()
    backup = path.with_name(path.name + '.corrupt-' + uuid.uuid4().hex + '.bak')
    pairs = []
    # Move the main file last, and restore any earlier moves if one fails.
    for suffix in ('-wal', '-shm', '-journal', ''):
        original = Path(str(path) + suffix)
        if original.is_symlink():
            raise CacheRecoveryError('Refusing to move a linked cache sidecar.')
        if not original.exists():
            continue
        destination = Path(str(backup) + suffix)
        if not original.is_file() or destination.exists() or original.resolve().parent != path.parent:
            raise CacheRecoveryError('Refusing to move unsafe cache files.')
        pairs.append((original, destination))
    moved = []
    try:
        for original, destination in pairs:
            original.rename(destination)
            moved.append((original, destination))
    except OSError as error:
        restored = True
        for original, destination in reversed(moved):
            try:
                if original.exists():
                    restored = False
                else:
                    destination.rename(original)
            except OSError:
                restored = False
        message = ('Could not preserve the damaged index; original cache files were restored.' if restored else
                   'Cache preservation was incomplete. Inspect the cache folder before retrying.')
        raise CacheRecoveryError(message) from error
    return backup
