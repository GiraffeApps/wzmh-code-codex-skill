#!/usr/bin/env python3
"""Install or update WZMH Code AI from a versioned source checkout."""

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SKILL_NAME = 'wzmh-code-skill'
REPOSITORY_URL = 'https://github.com/GiraffeApps/wzmh-code-codex-skill.git'
RELEASE_BRANCH = 'master'
VERSION_PATTERN = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
PAYLOAD = (
    ('agents', {'.yaml'}),
    ('references', {'.md', '.json'}),
    ('scripts', {'.py', '.cmd'}),
    ('assets/source_documents', {'.pdf'}),
)
ROOT_FILES = ('SKILL.md', 'README.md', 'CHANGELOG.md', 'VERSION')


def read_version(root):
    path = Path(root) / 'VERSION'
    if not path.is_file():
        return None
    value = path.read_text(encoding='utf-8').strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError('Invalid VERSION in {}'.format(root))
    return tuple(int(part) for part in value.split('.'))


def is_our_skill(root):
    skill_file = Path(root) / 'SKILL.md'
    if not skill_file.is_file():
        return False
    header = skill_file.read_text(encoding='utf-8', errors='replace').split('---', 2)
    return len(header) >= 3 and bool(re.search(r'^name:\s*wzmh-code-skill\s*$', header[1], re.MULTILINE))


def payload_files(source):
    source = Path(source)
    if not is_our_skill(source):
        raise ValueError('Source is not the WZMH Code skill: {}'.format(source))
    if read_version(source) is None:
        raise ValueError('Source has no VERSION file: {}'.format(source))
    required = (
        'agents/openai.yaml',
        'references/library.md', 'references/library-checksums.json', 'references/search-cli.md',
        'scripts/cache_safety.py', 'scripts/diagnostics.py', 'scripts/health_check.cmd',
        'scripts/health_check.py', 'scripts/highlight_sentence.py', 'scripts/provision_index.py',
        'scripts/reference_cache.py', 'scripts/search_pdf.py',
        'assets/source_documents/2024 OBC.Volume 1.pdf',
        'assets/source_documents/2024 OBC.Volume 2.pdf',
        'assets/source_documents/NBC 2020 Part 1.pdf',
    )
    if any(not (source / item).is_file() for item in required):
        raise ValueError('Source is missing required skill files')
    files = []
    for name in ROOT_FILES:
        path = source / name
        if path.is_file():
            files.append(path)
    for directory, suffixes in PAYLOAD:
        parent = source / directory
        if parent.is_dir():
            files.extend(path for path in parent.rglob('*') if path.is_file() and path.suffix.lower() in suffixes)
    for path in files:
        if path.is_symlink():
            raise ValueError('Source payload contains a symlink: {}'.format(path))
    return files


def remove_path(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(str(path))


def install(source, target, force=False):
    source = Path(source).resolve()
    target = Path(target).expanduser()
    target_location = Path(os.path.abspath(str(target)))
    if source == target_location or source in target_location.parents or target_location in source.parents:
        raise ValueError('Source and destination must be separate directories')
    files = payload_files(source)
    incoming_version = read_version(source)
    exists = target.exists() or target.is_symlink()
    if exists and not is_our_skill(target):
        raise ValueError('Refusing to replace an unrelated path: {}'.format(target))
    installed_version = read_version(target) if exists else None
    if installed_version and installed_version > incoming_version and not force:
        raise ValueError('Installed version is newer; use --force to downgrade')
    if installed_version == incoming_version and not force and not target.is_symlink():
        return 'current'

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.wzmh-code-stage-', dir=str(target.parent)))
    backup = None
    try:
        for path in files:
            destination = staging / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(path), str(destination))
        if exists:
            backup = Path(tempfile.mkdtemp(prefix='.wzmh-code-old-', dir=str(target.parent)))
            backup.rmdir()
            os.replace(str(target), str(backup))
        try:
            os.replace(str(staging), str(target))
        except Exception:
            if backup is not None:
                os.replace(str(backup), str(target))
                backup = None
            raise
    finally:
        remove_path(staging)
        if backup is not None:
            remove_path(backup)
    return 'updated' if exists else 'installed'


def default_target():
    codex_home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    return codex_home / 'skills' / SKILL_NAME


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', action='store_true', help='fetch the latest master release from GitHub before installing')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--target', type=Path, default=default_target())
    parser.add_argument('--force', action='store_true', help='reinstall the same version or allow a downgrade')
    parser.add_argument('--status', action='store_true', help='show source and installed versions without changing files')
    args = parser.parse_args()
    if args.status:
        source_version = read_version(args.source)
        installed_version = read_version(args.target)
        print('Source: {}'.format('.'.join(map(str, source_version)) if source_version else 'unversioned'))
        print('Installed: {}'.format('.'.join(map(str, installed_version)) if installed_version else 'unversioned or absent'))
        return
    if args.latest:
        with tempfile.TemporaryDirectory(prefix='wzmh-code-download-') as temporary:
            checkout = Path(temporary) / 'checkout'
            subprocess.run(['git', 'clone', '--depth', '1', '--branch', RELEASE_BRANCH,
                            REPOSITORY_URL, str(checkout)], check=True)
            result = install(checkout, args.target, args.force)
    else:
        result = install(args.source, args.target, args.force)
    print('{} {} version {} at {}'.format(SKILL_NAME, result,
          '.'.join(map(str, read_version(args.target))), args.target))


if __name__ == '__main__':
    main()
