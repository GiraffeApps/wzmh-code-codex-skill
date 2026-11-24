#!/usr/bin/env python3
"""Require a higher skill VERSION for every changed pull request."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

VERSION_PATTERN = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')


def parse_version(value):
    value = value.strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError('VERSION must use MAJOR.MINOR.PATCH')
    return tuple(int(part) for part in value.split('.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-ref', required=True, help='base branch or commit to compare')
    args = parser.parse_args()
    changed = subprocess.check_output(['git', 'diff', '--name-only', '--diff-filter=ACMRT',
                                       args.base_ref, '--'], text=True).strip()
    if not changed:
        print('No changes from {}'.format(args.base_ref))
        return 0
    current = parse_version(Path('VERSION').read_text(encoding='utf-8'))
    previous = subprocess.run(['git', 'show', '{}:VERSION'.format(args.base_ref)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    old = parse_version(previous.stdout) if previous.returncode == 0 else (0, 0, 0)
    if current <= old:
        print('Skill changed; increase VERSION above {} before merging.'.format(
              '.'.join(map(str, old))), file=sys.stderr)
        return 1
    print('VERSION increased from {} to {}'.format('.'.join(map(str, old)),
          '.'.join(map(str, current))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
