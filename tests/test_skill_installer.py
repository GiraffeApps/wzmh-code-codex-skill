import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_skill', ROOT / 'scripts' / 'install_skill.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SkillInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'codex' / 'skills' / 'wzmh-code-skill'
        self.source.mkdir()
        (self.source / 'SKILL.md').write_text('---\nname: wzmh-code-skill\n---\n', encoding='utf-8')
        (self.source / 'VERSION').write_text('1.0.0\n', encoding='utf-8')
        (self.source / 'scripts').mkdir()
        for name in ('cache_safety.py', 'diagnostics.py', 'health_check.cmd', 'health_check.py', 'highlight_sentence.py', 'provision_index.py', 'reference_cache.py', 'search_pdf.py'):
            (self.source / 'scripts' / name).write_text('new', encoding='utf-8')
        (self.source / 'agents').mkdir()
        (self.source / 'agents' / 'openai.yaml').write_text('name: test', encoding='utf-8')
        (self.source / 'references').mkdir()
        for name in ('library.md', 'library-checksums.json', 'search-cli.md'):
            (self.source / 'references' / name).write_text('test', encoding='utf-8')
        (self.source / 'assets' / 'source_documents').mkdir(parents=True)
        for name in ('2024 OBC.Volume 1.pdf', '2024 OBC.Volume 2.pdf', 'NBC 2020 Part 1.pdf'):
            (self.source / 'assets' / 'source_documents' / name).write_bytes(b'%PDF-test')

    def test_update_replaces_old_directory_and_clears_stale_files(self):
        self.target.mkdir(parents=True)
        (self.target / 'SKILL.md').write_text('---\nname: wzmh-code-skill\n---\n', encoding='utf-8')
        (self.target / 'VERSION').write_text('0.9.0\n', encoding='utf-8')
        (self.target / 'obsolete.py').write_text('stale', encoding='utf-8')
        (self.target / 'scripts').mkdir()
        (self.target / 'scripts' / 'search_pdf.py').write_text('old', encoding='utf-8')
        result = installer.install(self.source, self.target)
        self.assertEqual(result, 'updated')
        self.assertFalse((self.target / 'obsolete.py').exists())
        self.assertEqual((self.target / 'scripts' / 'search_pdf.py').read_text(), 'new')
        self.assertEqual((self.target / 'VERSION').read_text().strip(), '1.0.0')

    def test_same_version_skips_update(self):
        installer.install(self.source, self.target)
        (self.target / 'marker').write_text('keep', encoding='utf-8')
        self.assertEqual(installer.install(self.source, self.target), 'current')
        self.assertTrue((self.target / 'marker').exists())

    def test_refuses_to_replace_unrelated_directory(self):
        self.target.mkdir(parents=True)
        (self.target / 'SKILL.md').write_text('---\nname: other-skill\n---\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            installer.install(self.source, self.target)
        self.assertEqual((self.target / 'SKILL.md').read_text().splitlines()[1], 'name: other-skill')

    def test_refuses_downgrade(self):
        installer.install(self.source, self.target)
        (self.source / 'VERSION').write_text('0.9.0\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            installer.install(self.source, self.target)
        self.assertEqual((self.target / 'VERSION').read_text().strip(), '1.0.0')

    def test_symlink_install_is_replaced_without_deleting_checkout(self):
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(self.source, target_is_directory=True)
        self.assertEqual(installer.install(self.source, self.target), 'updated')
        self.assertFalse(self.target.is_symlink())
        self.assertTrue((self.source / 'scripts' / 'search_pdf.py').exists())
        self.assertEqual((self.target / 'VERSION').read_text().strip(), '1.0.0')

    def test_refuses_to_replace_its_source_checkout(self):
        with self.assertRaises(ValueError):
            installer.install(self.source, self.source, force=True)
        self.assertTrue((self.source / 'scripts' / 'search_pdf.py').exists())

    def test_incomplete_source_does_not_remove_existing_install(self):
        installer.install(self.source, self.target)
        (self.source / 'assets' / 'source_documents' / 'NBC 2020 Part 1.pdf').unlink()
        with self.assertRaises(ValueError):
            installer.install(self.source, self.target, force=True)
        self.assertTrue((self.target / 'assets' / 'source_documents' / 'NBC 2020 Part 1.pdf').exists())

    def test_legacy_install_is_replaced(self):
        self.target.mkdir(parents=True)
        (self.target / 'SKILL.md').write_text('---\nname: wzmh-code-skill\n---\n', encoding='utf-8')
        (self.target / 'old_file').write_text('stale', encoding='utf-8')
        self.assertEqual(installer.install(self.source, self.target), 'updated')
        self.assertFalse((self.target / 'old_file').exists())


if __name__ == '__main__':
    unittest.main()
