"""Per-chat SQLite index of generated excerpts; PDFs remain the only evidence."""
import hashlib
from pathlib import Path
import sqlite3

MANAGED_TAG = "obc-reference-generated-v1"


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_path(directory, name):
    directory = Path(directory).resolve()
    if Path(name).name != name or Path(name).suffix.lower() != '.pdf':
        return None
    candidate = directory / name
    if candidate.is_symlink() or candidate.resolve().parent != directory:
        return None
    return candidate


def connect(directory, engine):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(directory / '.obc-reference-cache.sqlite3', timeout=2)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS entries (reference_key TEXT NOT NULL, filename TEXT PRIMARY KEY, sha256 TEXT NOT NULL)')
        conn.execute('CREATE INDEX IF NOT EXISTS by_reference_key ON entries(reference_key)')
        conn.execute('CREATE TABLE IF NOT EXISTS cache_meta (key TEXT PRIMARY KEY, value TEXT)')
        if conn.execute("SELECT value FROM cache_meta WHERE key='migrated'").fetchone() is None:
            # One-time discovery preserves compatibility with already-saved excerpts.
            for candidate in directory.glob('*.pdf'):
                if safe_path(directory, candidate.name) is None:
                    continue
                try:
                    with engine.open(candidate) as doc:
                        meta = doc.metadata or {}
                        subject = meta.get('subject', '')
                        if meta.get('keywords') != MANAGED_TAG:
                            continue
                    checksum = digest(candidate)
                    key = subject[len('reference-key:'):] if subject.startswith('reference-key:') else 'legacy:' + checksum
                    conn.execute('INSERT OR REPLACE INTO entries VALUES (?,?,?)', (key, candidate.name, checksum))
                except Exception:
                    continue
            conn.execute("INSERT INTO cache_meta VALUES ('migrated','1')")
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


def candidate(directory, key, engine):
    if not Path(directory).exists():
        return None
    conn = connect(directory, engine)
    try:
        row = conn.execute('SELECT filename,sha256 FROM entries WHERE reference_key=?', (key,)).fetchone()
        if not row:
            return None
        path = safe_path(directory, row[0])
        if path is None or not path.is_file() or digest(path) != row[1]:
            conn.execute('DELETE FROM entries WHERE filename=?', (row[0],))
            conn.commit()
            return None
        return path
    finally:
        conn.close()


def register(path, key, engine):
    path = Path(path).resolve()
    conn = connect(path.parent, engine)
    try:
        conn.execute('INSERT OR REPLACE INTO entries VALUES (?,?,?)', (key, path.name, digest(path)))
        conn.commit()
    finally:
        conn.close()


def entries(directory, engine):
    conn = connect(directory, engine)
    try:
        return conn.execute('SELECT filename,sha256 FROM entries').fetchall()
    finally:
        conn.close()


def forget(directory, name, engine):
    conn = connect(directory, engine)
    try:
        conn.execute('DELETE FROM entries WHERE filename=?', (name,))
        conn.commit()
    finally:
        conn.close()
