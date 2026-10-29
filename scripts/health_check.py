"""Setup-only dependency, source-integrity and local-storage checks. No indexing."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time

from diagnostics import user_data_root

SKILL_ROOT = Path(__file__).resolve().parent.parent


def result(name, status, detail, fix=None):
    check = {'name': name, 'status': status, 'detail': detail}
    if fix:
        check['fix'] = fix
    return check


def load_pdf_engine():
    return importlib.import_module('pymupdf')


def check_pdf_engine():
    try:
        engine = load_pdf_engine()
        if not callable(getattr(engine, 'open', None)) or not callable(getattr(engine.Document, 'bake', None)):
            raise RuntimeError('Required PDF opening/highlight-baking support is unavailable.')
        version = getattr(engine, 'VersionBind', getattr(engine, '__version__', 'available'))
        return engine, result('PyMuPDF', 'PASS', str(version))
    except Exception as error:
        return None, result('PyMuPDF', 'FAIL', f'Unavailable or incompatible ({type(error).__name__}).',
                            'Ask IT to install a compatible PyMuPDF in the selected Python environment; the tested setup uses PyMuPDF 2.0.')


def check_sqlite():
    try:
        import sqlite3
        with closing(sqlite3.connect(':memory:')) as connection:
            connection.execute('CREATE VIRTUAL TABLE probe USING fts5(text)')
            connection.execute('INSERT INTO probe VALUES (?)', ('installation verification',))
            if connection.execute("SELECT count(*) FROM probe WHERE probe MATCH 'verification'").fetchone() != (1,):
                raise RuntimeError('Full-text result was not correct.')
            try:
                connection.execute('SELECT * FROM missing_health_check_table')
            except sqlite3.DatabaseError as error:
                if not isinstance(getattr(error, 'sqlite_errorcode', None), int):
                    raise RuntimeError('SQLite error codes required for cache recovery are unavailable.')
        return result('SQLite full-text search', 'PASS', f'SQLite {sqlite3.sqlite_version}; FTS5 query and error-code support work.')
    except Exception as error:
        return result('SQLite full-text search', 'FAIL', f'FTS5 or required SQLite support failed ({type(error).__name__}).',
                      'Use a Python build with working SQLite FTS5 and SQLite error-code support; the tested setup uses Python 3.14.')


def load_manifest(skill_root):
    data = json.loads((skill_root / 'references' / 'library-checksums.json').read_text(encoding='utf-8-sig'))
    entries = data['pdfs']
    if not isinstance(entries, list) or len(entries) != 3:
        raise ValueError('Expected the three bundled PDF entries.')
    names = set()
    for entry in entries:
        name, checksum = entry['filename'], entry['sha256']
        if (not isinstance(name, str) or not name or '/' in name or '\\' in name
                or Path(name).name != name or Path(name).suffix.lower() != '.pdf'
                or name in names or not isinstance(checksum, str)
                or re.fullmatch(r'[0-9a-f]{64}', checksum) is None):
            raise ValueError('Invalid or duplicate bundled PDF entry.')
        names.add(name)
    return entries


def check_pdf(path, expected_hash, engine):
    name = 'PDF: ' + path.name
    fix = 'Restore this PDF from the approved skill package. Do not change its expected checksum to accept an altered file.'
    try:
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                checksum.update(chunk)
        if checksum.hexdigest() != expected_hash:
            return result(name, 'FAIL', 'SHA-256 does not match the bundled original.', fix)
        if engine is None:
            return result(name, 'SKIP', 'Checksum matches; PDF opening was not checked because PyMuPDF is unavailable.',
                          'Resolve the PyMuPDF failure, then rerun this check.')
        with engine.open(path) as document:
            if not document.is_pdf or document.needs_pass or document.page_count < 1:
                raise ValueError('PDF is empty, encrypted or not readable.')
            document.load_page(0)
            count = document.page_count
        return result(name, 'PASS', f'SHA-256 matches; opens successfully ({count} pages).')
    except Exception as error:
        return result(name, 'FAIL', f'Missing or unreadable PDF ({type(error).__name__}).', fix)


def check_storage(name, directory):
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # The OS removes only this uniquely created probe on close, including errors.
        with tempfile.TemporaryFile(mode='w+b', prefix='.wzmh-health-', dir=directory) as probe:
            probe.write(b'WZMH storage check')
            probe.flush()
            probe.seek(0)
            if probe.read() != b'WZMH storage check':
                raise OSError('Local storage read-back failed.')
        return result(name, 'PASS', 'Temporary-file write/read/removal succeeded.')
    except Exception as error:
        return result(name, 'FAIL', f'Local folder is not usable ({type(error).__name__}).',
                      'Check this user\'s local data location and permissions. Run in the user\'s Windows session, not a different account.')


def check_installation(skill_root=None, runtime_root=None):
    started = time.perf_counter()
    skill_root = Path(skill_root) if skill_root is not None else SKILL_ROOT
    runtime_root = Path(runtime_root) if runtime_root is not None else user_data_root()
    checks = [result('Python', 'PASS', f'{sys.version.split()[0]} at {sys.executable}')]
    engine, pdf_check = check_pdf_engine()
    checks.extend((pdf_check, check_sqlite()))
    try:
        entries = load_manifest(skill_root)
    except Exception as error:
        checks.append(result('Bundled PDF manifest', 'FAIL', f'Missing or invalid checksum manifest ({type(error).__name__}).',
                             'Restore references/library-checksums.json from the approved skill package.'))
    else:
        checks.extend(check_pdf(skill_root / 'assets' / 'source_documents' / entry['filename'], entry['sha256'], engine)
                      for entry in entries)
    checks.extend((check_storage('Local cache', runtime_root / 'cache'),
                   check_storage('Local logs', runtime_root / 'logs')))
    return {'ok': all(check['status'] == 'PASS' for check in checks), 'checks': checks,
            'elapsed_ms': round((time.perf_counter() - started) * 1000)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Return machine-readable results; exit 0 only when all checks pass.')
    args = parser.parse_args()
    report = check_installation()
    if args.json:
        print(json.dumps(report, ensure_ascii=True))
    else:
        for check in report['checks']:
            print(f"{check['status']:4}  {check['name']}: {check['detail']}")
            if check.get('fix'):
                print('      Fix: ' + check['fix'])
        print('\nSetup check passed.' if report['ok'] else '\nSetup check failed. Resolve the failures and rerun.')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
