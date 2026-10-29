"""Setup-only dependency, source-integrity and local-storage checks. No indexing."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import subprocess
import tempfile
import time

from diagnostics import user_data_root

SKILL_ROOT = Path(__file__).resolve().parent.parent
READINESS_SCHEMA = 1


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


def file_signature(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def readiness_context(skill_root):
    manifest = skill_root / 'references' / 'library-checksums.json'
    entries = load_manifest(skill_root)
    return {'skill_root': str(skill_root.resolve()), 'host': platform.node(),
            'python': file_signature(sys.executable), 'python_version': sys.version,
            'python_prefix': str(Path(sys.prefix).resolve()),
            'python_setting': os.environ.get('WZMH_CODE_PYTHON') or None,
            'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'sources': [file_signature(skill_root / 'assets' / 'source_documents' / entry['filename']) for entry in entries]}


def cached_readiness(marker, skill_root):
    """Validate a small setup record without importing PyMuPDF or probing packages."""
    try:
        if marker.is_symlink() or marker.stat().st_size > 65536:
            return False
        data = json.loads(marker.read_text(encoding='utf-8'))
        if (data.get('schema') != READINESS_SCHEMA or data.get('status') != 'ready'
                or data.get('context') != readiness_context(skill_root)):
            return False
        modules = data['modules']
        if (not isinstance(modules, list) or not modules
                or not any(item['name'] == 'pymupdf' for item in modules)
                or not any(item['name'] == 'sqlite3' for item in modules)):
            return False
        return all(file_signature(item['file']['path']) == item['file'] for item in modules)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def remember_readiness(marker, skill_root):
    if marker.is_symlink():
        raise OSError('Setup record must not be a symbolic link.')
    modules = []
    for name, module in list(sys.modules.items()):
        if name in ('pymupdf', 'sqlite3', 'sqlite3.dbapi2', '_sqlite3') or name.startswith('pymupdf.'):
            filename = getattr(module, '__file__', None)
            if filename:
                modules.append({'name': name, 'file': file_signature(filename)})
    if not any(item['name'] == 'pymupdf' for item in modules) or not any(item['name'] == 'sqlite3' for item in modules):
        raise OSError('Successful dependency verification was not available to record.')
    data = {'schema': READINESS_SCHEMA, 'status': 'ready',
            'verified_utc': datetime.now(timezone.utc).isoformat(),
            'context': readiness_context(skill_root), 'modules': modules}
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=marker.parent,
                                         prefix='.ready-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def ensure_readiness(skill_root=None, runtime_root=None, force=False):
    """Remember successful setup per user; refresh only changed/failed environments."""
    started = time.perf_counter()
    skill_root = Path(skill_root) if skill_root is not None else SKILL_ROOT
    runtime_root = Path(runtime_root) if runtime_root is not None else user_data_root()
    marker = runtime_root / 'setup' / 'ready.json'
    if not force and cached_readiness(marker, skill_root):
        return {'ok': True, 'cached': True, 'setup_required': False, 'checks': [],
                'elapsed_ms': round((time.perf_counter() - started) * 1000)}
    report = check_installation(skill_root, runtime_root)
    report.update(cached=False, setup_required=not report['ok'])
    if report['ok']:
        try:
            remember_readiness(marker, skill_root)
            report['readiness_saved'] = True
        except (OSError, ValueError, TypeError, KeyError) as error:
            report.update(ok=False, setup_required=True, readiness_saved=False)
            report['checks'].append(result('Setup confirmation', 'FAIL',
                f'Dependencies passed, but setup could not be remembered ({type(error).__name__}).',
                'Ask IT to restore write access to the user-local WZMH/CodeAI/setup folder. Do not reinstall working Python packages.'))
    if not report['ok']:
        try:
            if marker.is_file() and not marker.is_symlink():
                marker.unlink()
        except OSError:
            report['readiness_invalidation_failed'] = True
    report['elapsed_ms'] = round((time.perf_counter() - started) * 1000)
    return report


def probe_python():
    """Read-only dependency probe for this interpreter; no PDFs/cache/log writes."""
    _, pdf_check = check_pdf_engine()
    checks = [pdf_check, check_sqlite()]
    implementation = sys.implementation.name
    return {'executable': str(Path(sys.executable).resolve()), 'version': sys.version.split()[0],
            'implementation': implementation, 'checks': checks,
            'ready': implementation == 'cpython' and all(c['status'] == 'PASS' for c in checks)}


def diagnose_python(candidates=None, selected_path=None, budget=10.0):
    """Report verified alternatives without selecting one or changing configuration."""
    started = time.monotonic()
    if candidates is None:
        try:
            candidates = json.loads(os.environ.get('WZMH_HEALTH_CANDIDATES', '[]'))
        except (ValueError, TypeError):
            candidates = []
    if not isinstance(candidates, list):
        candidates = []
    selected_path = selected_path if selected_path is not None else os.environ.get('CodePython', '')
    verified, attempted, notes, seen = [], [], [], set()
    for candidate in candidates[:16]:
        if not isinstance(candidate, dict) or not isinstance(candidate.get('path'), str):
            continue
        path = Path(candidate['path'])
        try:
            if not path.is_absolute() or not path.is_file() or path.stat().st_size == 0:
                continue
            key = os.path.normcase(str(path.resolve()))
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            notes.append('Discovery time limit reached; additional installations may exist.')
            break
        if len(attempted) >= 8:
            notes.append('Probe limit reached; additional installations may exist.')
            break
        entry = {'path': str(path), 'origin': candidate.get('origin', 'detected')}
        attempted.append(entry)
        try:
            child = subprocess.run([str(path), '-B', str(Path(__file__).resolve()), '--probe-python'],
                                   capture_output=True, text=True, encoding='utf-8',
                                   timeout=min(2.5, remaining), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if child.returncode:
                raise ValueError('Interpreter probe did not complete.')
            data = json.loads(child.stdout)
            if (not isinstance(data, dict) or not isinstance(data.get('executable'), str) or not isinstance(data.get('version'), str)
                    or data.get('implementation') != 'cpython' or not isinstance(data.get('checks'), list)
                    or len(data['checks']) != 2 or not all(isinstance(c, dict) for c in data['checks'])
                    or {c.get('name') for c in data['checks']} != {'PyMuPDF', 'SQLite full-text search'}
                    or type(data.get('ready')) is not bool
                    or data['ready'] != all(c.get('status') == 'PASS' for c in data['checks'])):
                raise ValueError('Not a verified CPython probe.')
            data['origin'] = entry['origin']
            if not any(os.path.normcase(item['executable']) == os.path.normcase(data['executable']) for item in verified):
                verified.append(data)
            if data['ready']:
                notes.append('Stopped after a compatible interpreter was verified; additional candidates were not probed.')
                break
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
            entry['verification_error'] = type(error).__name__
    ready = [candidate for candidate in verified if candidate['ready']]
    if ready:
        code = 'python_path_mismatch'
        detail = 'Python is installed and its required dependencies work, but the skill is not pointing to a verified executable.'
        suggested = ready[0]['executable']
        fix = f'Ask IT to set WZMH_CODE_PYTHON to {suggested}, restart Codex, then rerun the complete health check.'
    elif verified:
        code = 'python_dependencies_missing'
        detail = 'Python is installed outside the skill\'s configured locations, but no detected environment passed all dependency checks.'
        suggested = None
        fix = 'Ask IT to resolve the listed PyMuPDF/SQLite failures in a selected detected environment, set WZMH_CODE_PYTHON to its absolute executable path, restart Codex and rerun the complete check.'
    else:
        code = 'python_not_verified'
        detail = 'No usable Python interpreter was verified in the locations checked. This does not prove Python is not installed.'
        suggested = None
        fix = 'Ask IT for an existing CPython executable path or deploy the required environment, then configure WZMH_CODE_PYTHON. A custom or unregistered installation may not have been discovered.'
    return {'ok': False, 'checks': [result('Python', 'FAIL', detail, fix)], 'reason': code,
            'python_configuration': {'selected_path': selected_path,
                                     'explicit_setting': os.environ.get('WZMH_CODE_PYTHON'),
                                     'selection_failure': os.environ.get('CodePythonFailure', 'unavailable')},
            'detected_pythons': verified, 'attempted_candidates': attempted,
            'suggested_python': suggested, 'discovery_notes': notes,
            'configuration_changed': False, 'remaining_health_checks_run': False,
            'elapsed_ms': round((time.monotonic() - started) * 1000)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Return machine-readable results; exit 0 only when all checks pass.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--diagnose-python', action='store_true', help='Verify discovered interpreter candidates without changing settings or indexing.')
    mode.add_argument('--probe-python', action='store_true', help=argparse.SUPPRESS)
    mode.add_argument('--ensure-ready', action='store_true', help='Check setup on first use; reuse a valid per-user setup confirmation afterward.')
    args = parser.parse_args()
    if args.probe_python:
        print(json.dumps(probe_python(), ensure_ascii=True))
        return 0
    report = diagnose_python() if args.diagnose_python else ensure_readiness(force=not args.ensure_ready)
    if args.json:
        print(json.dumps(report, ensure_ascii=True))
    elif report.get('cached'):
        print('Setup already verified for this user. No setup check or installation prompt required.')
    else:
        for check in report['checks']:
            print(f"{check['status']:4}  {check['name']}: {check['detail']}")
            if check.get('fix'):
                print('      Fix: ' + check['fix'])
        if args.diagnose_python:
            print('      Skill selected: ' + (report['python_configuration']['selected_path'] or '(not configured)'))
            for candidate in report['detected_pythons']:
                print(f"      Found Python {candidate['version']}: {candidate['executable']}")
                for check in candidate['checks']:
                    print(f"        {check['status']}: {check['name']} — {check['detail']}")
            for note in report['discovery_notes']:
                print('      ' + note)
        print('\nSetup check passed.' if report['ok'] else '\nSetup check failed. Resolve the failures and rerun.')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
