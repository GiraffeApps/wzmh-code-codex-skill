import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parents[1] / 'scripts' / 'check_version.py'


class VersionCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        (self.repo / 'VERSION').write_text('1.0.0\n')
        (self.repo / 'SKILL.md').write_text('old')
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test',
                        '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base'], check=True)
        self.base = subprocess.check_output(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], text=True).strip()

    def check_version(self):
        return subprocess.run([sys.executable, str(CHECKER), '--base-ref', self.base],
                              cwd=str(self.repo), capture_output=True, text=True)

    def test_changed_skill_requires_newer_version(self):
        (self.repo / 'SKILL.md').write_text('updated')
        result = self.check_version()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('increase VERSION', result.stderr)

    def test_changed_skill_with_newer_version_passes(self):
        (self.repo / 'SKILL.md').write_text('updated')
        (self.repo / 'VERSION').write_text('1.0.1\n')
        result = self.check_version()
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
