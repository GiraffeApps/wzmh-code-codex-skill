"""Regression tests use isolated generated fixtures, never the bundled PDF library."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import search_pdf as search
import highlight_sentence as highlight
import reference_cache
import diagnostics
import cache_safety
import health_check as health
import pymupdf

SENTENCE = 'The minimum width of a public corridor shall be 1 100 mm.'


def invoke(module, *args, diagnostic=None):
    previous = sys.argv
    out = io.StringIO()
    sys.argv = [module.__file__, *map(str, args)]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            module.main(diagnostic)
        return json.loads(out.getvalue())
    finally:
        sys.argv = previous


class Regression(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='obc-phase1-')
        self.directory = Path(self.temporary.name)
        self.source = self.directory / 'source.pdf'
        self.cache_before = search.CACHE_PATH
        search.CACHE_PATH = self.directory / 'cache' / 'text.sqlite3'
        self.make_pdf()

    def tearDown(self):
        search.CACHE_PATH = self.cache_before
        self.temporary.cleanup()

    def make_pdf(self, sentence=SENTENCE):
        doc = pymupdf.open()
        doc.set_metadata({'author': 'Owner-A'})
        p = doc.new_page()
        p.insert_text((50, 60), '3.3.1.9. Corridors\n(1) ' + sentence + '\n(2) Exceptions require separate review.')
        p.draw_line((50, 150), (250, 150))
        p = doc.new_page()
        p.insert_text((50, 60), '3.3.1.10. Doors\nA garage door shall be self-closing.\nThe sentence continues on this page.')
        doc.save(self.source)
        doc.close()

    def search(self, *args):
        return invoke(search, '--source', self.source, '--json', *args)

    def excerpt(self, name='a.pdf', *extra):
        return invoke(highlight, self.source, '--page', 1, '--text', SENTENCE,
                      '--excerpt-output', self.directory / 'references' / name, *extra)

    def test_default_cache_path_uses_local_user_data(self):
        for user in ('user-a', 'user-b'):
            local_data = self.directory / user / 'AppData' / 'Local'
            with patch.dict(os.environ, {'LOCALAPPDATA': str(local_data)}), \
                 patch.object(search, 'SKILL_ROOT', self.directory / 'installed-skill'):
                self.assertEqual(search.default_cache_path(),
                                 local_data / 'WZMH' / 'CodeAI' / 'cache' / 'pdf_text_index.sqlite3')

    def test_first_lookup_automatically_creates_user_cache_outside_skill(self):
        install = self.directory / 'installed-skill'
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.directory / 'user-data')}), \
             patch.object(search, 'SKILL_ROOT', install):
            cache = search.default_cache_path()
            with patch.object(search, 'CACHE_PATH', cache):
                cold = self.search('--keyword', 'public corridor')
                warm = self.search('--keyword', 'public corridor')
        self.assertTrue(cache.is_file())
        self.assertTrue(cold['indexed_pages'])
        self.assertFalse(warm['indexed_pages'])
        self.assertEqual(cold['results'], warm['results'])
        self.assertFalse(install.exists())

    def test_separate_chat_processes_reuse_user_cache_without_sharing_between_users(self):
        chats = [self.directory / 'chat-one', self.directory / 'chat-two']
        for folder in chats:
            folder.mkdir()
        command = [sys.executable, '-B', '-X', 'utf8', str(SCRIPTS / 'search_pdf.py'),
                   '--source', str(self.source), '--keyword', 'public corridor', '--json']
        results = []
        for user, folder in [('user-a', chats[0]), ('user-a', chats[1]), ('user-b', chats[0])]:
            env = os.environ.copy()
            env['LOCALAPPDATA'] = str(self.directory / user)
            done = subprocess.run(command, cwd=folder, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            results.append(json.loads(done.stdout))
        self.assertTrue(results[0]['indexed_pages'])
        self.assertFalse(results[1]['indexed_pages'])
        self.assertEqual(results[1]['reused_sources'], [str(self.source.resolve())])
        self.assertTrue(results[2]['indexed_pages'])
        self.assertEqual(results[0]['results'], results[1]['results'])
        for user in ('user-a', 'user-b'):
            self.assertTrue((self.directory / user / 'WZMH' / 'CodeAI' / 'cache' / 'pdf_text_index.sqlite3').is_file())

    def test_keyword_regex_full_page_and_exact_pages(self):
        keyword = self.search('--keyword', 'public corridor')
        self.assertEqual([r['pdf_page'] for r in keyword['results']], [1])
        regex = self.search('--regex', r'3\.3\.1\.10\.')
        self.assertEqual([r['pdf_page'] for r in regex['results']], [2])
        pages = self.search('--pages', 2, 1)
        self.assertEqual([r['pdf_page'] for r in pages['results']], [2, 1])
        self.assertIn(SENTENCE, pages['results'][1]['text'])
        full = self.search('--keyword', 'public corridor', '--full-page')
        self.assertIn(SENTENCE, full['results'][0]['text'])

    def test_batch_equals_individual_requests(self):
        jobs = {'requests': [{'id': 'keyword', 'keywords': ['public corridor']},
                             {'id': 'pages', 'pages': [1, 2]},
                             {'id': 'regex', 'regex': r'self-closing'}]}
        batch = self.search('--batch-json', json.dumps(jobs))
        individual = [self.search('--keyword', 'public corridor'), self.search('--pages', 1, 2),
                      self.search('--regex', 'self-closing')]
        for response, expected in zip(batch['responses'], individual):
            self.assertEqual(response['results'], expected['results'])

    def test_focused_blocks_are_exact_pdf_text_and_retain_conditions(self):
        result = self.search('--keyword', 'public corridor', '--focused')
        row = result['results'][0]
        with pymupdf.open(self.source) as doc:
            raw = doc[0].get_text()
        for block in row['provisions']:
            self.assertEqual(block['text'], raw[block['start']:block['end']])
        self.assertIn('Exceptions require separate review.', row['provisions'][0]['text'])
        self.assertEqual(row['provisions'][0]['reference'].rstrip('.'), '3.3.1.9')

    def test_natural_query_is_pdf_lexical_retrieval(self):
        result = self.search('--query', 'What is the minimum width of a public corridor?', '--focused')
        self.assertEqual(result['results'][0]['pdf_page'], 1)
        self.assertIn(SENTENCE, result['results'][0]['provisions'][0]['text'])

    def test_warm_search_does_not_import_pymupdf(self):
        self.search('--index-only')
        code = "import sys,io,contextlib;sys.path.insert(0,sys.argv[1]);import search_pdf as s;from pathlib import Path;s.CACHE_PATH=Path(sys.argv[2]);source=sys.argv[3];sys.argv=['search','--source',source,'--pages','1','--json'];buf=io.StringIO();\nwith contextlib.redirect_stdout(buf):s.main()\nassert 'pymupdf' not in sys.modules;print('no-pdf-import')"
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, str(SCRIPTS),
                                 str(search.CACHE_PATH), str(self.source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('no-pdf-import', result.stdout)

    def test_changed_source_refreshes_text_and_page_mapping(self):
        self.search('--keyword', 'public corridor')
        self.source.unlink()
        self.make_pdf('A completely revised corridor provision applies.')
        result = self.search('--keyword', 'completely revised')
        self.assertTrue(result['indexed_pages'])
        self.assertEqual(result['results'][0]['pdf_page'], 1)
        self.assertFalse(self.search('--keyword', 'public corridor')['results'])
        self.assertIn('completely revised', self.search('--pages', 1)['results'][0]['text'])

    def test_chat_local_index_does_not_write_skill_wide_cache(self):
        chat_cache = self.directory / 'references' / 'pdf-text-index.sqlite3'
        first = self.search('--cache-path', chat_cache, '--keyword', 'public corridor')
        self.assertEqual(first['results'][0]['pdf_page'], 1)
        self.assertTrue(chat_cache.is_file())
        self.assertFalse(search.CACHE_PATH.exists())
        warm = self.search('--cache-path', chat_cache, '--keyword', 'public corridor')
        self.assertFalse(warm['indexed_pages'])
        self.assertEqual(first['results'], warm['results'])

    def test_shared_index_does_not_retrieve_unselected_test_pdf(self):
        decoy = self.directory / 'tests' / 'sample-answer.pdf'
        decoy.parent.mkdir()
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((50, 60), '3.3.1.9. Corridors\n(1) The minimum width of a public corridor shall be 9 999 mm.', fontsize=9)
            document.save(decoy)
        invoke(search, '--source', decoy, '--index-only', '--json')
        for args in [('--keyword', 'public corridor'), ('--regex', 'public corridor'),
                     ('--query', 'minimum width public corridor', '--focused'),
                     ('--reference', '3.3.1.9.'), ('--pages', 1)]:
            with self.subTest(mode=args[0]):
                rows = self.search(*args)['results']
                self.assertTrue(rows)
                self.assertTrue(all(row['path'] == str(self.source.resolve()) for row in rows))
                self.assertNotIn('9 999', json.dumps(rows))

    def test_default_pdf_discovery_does_not_scan_tests_or_work(self):
        library = self.directory / 'assets' / 'source_documents'
        library.mkdir(parents=True)
        bundled = library / 'code.pdf'
        bundled.write_bytes(self.source.read_bytes())
        for folder in ('tests', 'work'):
            target = self.directory / folder
            target.mkdir()
            (target / 'sample-answer.pdf').write_bytes(self.source.read_bytes())
        with patch.object(search, 'LIBRARY_ROOT', library):
            self.assertEqual(search.bundled_sources(), [bundled])

    def test_cli_routing_notice_preserves_explicit_source_selection(self):
        library = self.directory / 'library'
        library.mkdir()
        self.source = self.source.rename(library / '2024 OBC.Volume 2.pdf')
        with patch.object(search, 'LIBRARY_ROOT', library):
            result = self.search('--query', 'Part 9 public corridor', '--focused')
        notice = result['source_routing_notice']
        self.assertEqual(notice['selected_source'], str(self.source.resolve()))
        self.assertEqual(notice['requirement_source'],
                         str((library / '2024 OBC.Volume 1.pdf').resolve()))
        self.assertTrue(result['results'])
        self.assertTrue(all(row['path'] == str(self.source.resolve()) for row in result['results']))
        self.assertIn('indexed_pages', result)
        self.assertIn('reused_sources', result)

    def test_same_stat_source_change_invalidates_search_index(self):
        self.search('--index-only')
        self.assertFalse(self.search('--index-only')['indexed_pages'])
        stat = self.source.stat()
        raw = self.source.read_bytes()
        self.assertIn(b'Owner-A', raw)
        self.source.write_bytes(raw.replace(b'Owner-A', b'Owner-B'))
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.source.stat().st_size, stat.st_size)
        self.assertTrue(self.search('--index-only')['indexed_pages'])

    def test_table_regions_on_two_pages_are_verified_and_reused(self):
        before = reference_cache.digest(self.source)
        passages = {1: ['3.3.1.9. Corridors', SENTENCE, 'Exceptions require separate review.'],
                    2: ['3.3.1.10. Doors', 'A garage door shall be self-closing.']}
        selections = []
        for text in passages[1]:
            selections.extend(['--table-text', text])
        selections.extend(['--context-page', 2])
        for text in passages[2]:
            selections.extend(['--context-table-text', text])
        first = invoke(highlight, self.source, '--page', 1, *selections,
                       '--excerpt-output', self.directory / 'references' / 'table.pdf')
        self.assertTrue(first['verified'])
        self.assertEqual(first['retention']['limit'], 200)
        with pymupdf.open(first['preview']) as saved, pymupdf.open(self.source) as original:
            self.assertEqual(len(saved), 2)
            for number, texts in passages.items():
                source_page, excerpt_page = original[number - 1], saved[number - 1]
                self.assertEqual(source_page.get_text(), excerpt_page.get_text())
                self.assertEqual(source_page.mediabox, excerpt_page.mediabox)
                for text in texts:
                    self.assertTrue(highlight.covers_highlights(
                        excerpt_page, source_page.search_for(text, quads=True)))
        second = invoke(highlight, self.source, '--page', 1, *selections,
                        '--excerpt-output', self.directory / 'references' / 'table-reused.pdf')
        self.assertTrue(second['reused'])
        self.assertEqual(first['preview'], second['preview'])
        self.assertEqual(before, reference_cache.digest(self.source))

    def test_missing_table_region_rejects_entire_excerpt(self):
        target = self.directory / 'references' / 'wrong-table.pdf'
        with self.assertRaises(SystemExit):
            invoke(highlight, self.source, '--page', 1, '--table-text', SENTENCE,
                   '--table-text', 'Invented table footnote.', '--excerpt-output', target)
        self.assertFalse(target.exists())

    def test_excerpt_quality_full_sentence_and_source_unchanged(self):
        before = reference_cache.digest(self.source)
        result = self.excerpt()
        self.assertFalse(result['reused'])
        with pymupdf.open(result['preview']) as saved, pymupdf.open(self.source) as original:
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].get_text(), original[0].get_text())
            self.assertEqual(saved[0].mediabox, original[0].mediabox)
            self.assertEqual(saved[0].cropbox, original[0].cropbox)
            self.assertEqual(saved[0].rotation, original[0].rotation)
            self.assertFalse(list(saved[0].annots()))
            self.assertTrue(highlight.covers_highlights(saved[0], original[0].search_for(SENTENCE, quads=True)))
            # Render independently to exercise baked appearances, without rasterizing delivery.
            pix = saved[0].get_pixmap()
            self.assertGreater(len(pix.samples), 0)
        self.assertEqual(before, reference_cache.digest(self.source))
        self.assertIn('view=Fit', result['browser_url'])

    def test_indexed_reuse_and_missing_output_regeneration(self):
        first = self.excerpt()
        second = self.excerpt('b.pdf')
        self.assertTrue(second['reused'])
        self.assertEqual(first['preview'], second['preview'])
        self.assertFalse((self.directory / 'references' / 'b.pdf').exists())
        Path(first['preview']).unlink()
        third = self.excerpt('c.pdf')
        self.assertFalse(third['reused'])

    def test_tampered_output_is_not_reused(self):
        first = self.excerpt()
        with Path(first['preview']).open('ab') as stream:
            stream.write(b'\n% changed output\n')
        second = self.excerpt('b.pdf')
        self.assertFalse(second['reused'])

    def test_same_stat_source_change_invalidates_excerpt(self):
        self.excerpt()
        stat = self.source.stat()
        raw = self.source.read_bytes()
        self.assertIn(b'Owner-A', raw)
        self.source.write_bytes(raw.replace(b'Owner-A', b'Owner-B'))
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.source.stat().st_size, stat.st_size)
        self.assertFalse(self.excerpt('changed.pdf')['reused'])

    def test_two_page_continuation_is_fully_highlighted(self):
        continuation = 'The sentence continues on this page.'
        result = self.excerpt('two.pdf', '--context-page', 2, '--context-text', continuation)
        with pymupdf.open(result['preview']) as saved, pymupdf.open(self.source) as original:
            self.assertEqual(len(saved), 2)
            self.assertTrue(highlight.covers_highlights(saved[1], original[1].search_for(continuation, quads=True)))

    def test_ambiguous_or_missing_quote_rejected(self):
        with self.assertRaises(SystemExit):
            invoke(highlight, self.source, '--page', 1, '--text', 'Invented quotation.',
                   '--excerpt-output', self.directory / 'wrong.pdf')
        self.assertFalse((self.directory / 'wrong.pdf').exists())
        self.source.unlink()
        self.make_pdf(SENTENCE + '\n' + SENTENCE)
        with self.assertRaises(SystemExit):
            self.excerpt('ambiguous.pdf')

    def test_text_file_interface_is_compatible(self):
        text = self.directory / 'sentence.txt'
        text.write_text(SENTENCE, encoding='utf-8')
        result = invoke(highlight, self.source, '--page', 1, '--text-file', text,
                        '--excerpt-output', self.directory / 'text-file.pdf')
        self.assertTrue(result['verified'])

    def test_default_retention_is_200_for_new_and_reused_excerpts(self):
        first = self.excerpt('first.pdf')
        self.assertEqual(first['retention']['limit'], 200)
        self.assertEqual(first['retention']['removed'], [])
        reused = self.excerpt('reused.pdf')
        self.assertTrue(reused['reused'])
        self.assertEqual(reused['retention']['limit'], 200)
        self.assertEqual(reused['retention']['removed'], [])

    def test_retention_preserves_pins_and_unrelated_files(self):
        first = self.excerpt('first.pdf')
        pinned = Path(first['preview'])
        pinned.with_suffix('.pdf.keep').touch()
        second = self.excerpt('second.pdf', '--context-page', 2)
        directory = pinned.parent
        unrelated = directory / 'unrelated.pdf'
        unrelated.write_bytes(self.source.read_bytes())
        third = invoke(highlight, self.source, '--page', 2, '--text', 'A garage door shall be self-closing.',
                       '--excerpt-output', directory / 'third.pdf', '--max-pdfs', 1)
        self.assertFalse(Path(second['preview']).exists())
        self.assertTrue(pinned.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(third['retention']['remaining_unpinned'], 1)

    def test_manifest_rejects_path_traversal(self):
        self.assertIsNone(reference_cache.safe_path(self.directory, '../other.pdf'))

    def test_legacy_full_volume_output_still_verifies(self):
        out = self.directory / 'full.pdf'
        result = invoke(highlight, self.source, '--page', 1, '--text', SENTENCE, '--output', out)
        self.assertTrue(result['verified'])
        with pymupdf.open(out) as doc:
            self.assertEqual(len(doc), 2)
            self.assertTrue(any(a.type[0] == 8 for a in doc[0].annots()))

    def test_broken_manifest_falls_back_to_verified_pdf(self):
        first = self.excerpt()
        manifest = Path(first['preview']).parent / '.obc-reference-cache.sqlite3'
        manifest.write_bytes(b'broken manifest')
        second = self.excerpt('fallback.pdf')
        self.assertTrue(second['reused'])
        self.assertEqual(first['preview'], second['preview'])

    def test_page_lookup_has_index_and_source_mapping(self):
        self.search('--index-only')
        with contextlib.closing(sqlite3.connect(search.CACHE_PATH)) as conn:
            plan = conn.execute('EXPLAIN QUERY PLAN SELECT fts_rowid FROM page_lookup WHERE source_path=? AND pdf_page=?',
                                (str(self.source.resolve()), 1)).fetchall()
        self.assertTrue(any('INDEX' in row[3] for row in plan))

    def test_batch_file_and_invalid_batch(self):
        batch = self.directory / 'requests.json'
        batch.write_text(json.dumps({'requests': [{'id': 'p', 'pages': [2]}]}), encoding='utf-8')
        result = self.search('--batch-file', batch)
        self.assertEqual(result['responses'][0]['results'][0]['pdf_page'], 2)
        with self.assertRaises(SystemExit):
            self.search('--batch-json', '{"requests":[{"keywords":["door"],"pages":[1]}]}')

    def test_damaged_index_is_preserved_and_rebuilt_without_touching_pdfs(self):
        excerpt = Path(self.excerpt()['preview'])
        original_hash = reference_cache.digest(self.source)
        excerpt_hash = reference_cache.digest(excerpt)
        cache = search.CACHE_PATH
        cache.parent.mkdir(parents=True)
        damaged = b'not a SQLite database: preserve this evidence'
        cache.write_bytes(damaged)
        result = self.search('--reference', '3.3.1.9.(1)', '--with-context')
        backup = Path(result['cache_recovery']['preserved_index'])
        self.assertEqual(backup.read_bytes(), damaged)
        self.assertEqual(result['results'][0]['pdf_page'], 1)
        self.assertIn(SENTENCE, result['results'][0]['provisions'][0]['text'])
        self.assertEqual(reference_cache.digest(self.source), original_hash)
        self.assertEqual(reference_cache.digest(excerpt), excerpt_hash)
        warm = self.search('--keyword', 'public corridor')
        self.assertNotIn('cache_recovery', warm)
        self.assertFalse(warm['indexed_pages'])
        self.assertEqual(len(list(cache.parent.glob('*.corrupt-*.bak'))), 1)

    @staticmethod
    def sqlite_error(code):
        error = sqlite3.DatabaseError('isolated simulated SQLite failure')
        error.sqlite_errorcode = code
        return error

    def test_corruption_during_retrieval_retries_whole_batch_once(self):
        self.search('--index-only')
        real_retrieve = search.retrieve
        calls = []
        def retrieve(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise self.sqlite_error(sqlite3.SQLITE_CORRUPT)
            return real_retrieve(*args, **kwargs)
        jobs = [{'id': 'one', 'pages': [1]}, {'id': 'two', 'pages': [2]}]
        with patch.object(search, 'retrieve', side_effect=retrieve):
            result = self.search('--batch-json', json.dumps(jobs))
        self.assertEqual(len(calls), 4)
        self.assertEqual([r['id'] for r in result['responses']], ['one', 'two'])
        self.assertEqual([r['results'][0]['pdf_page'] for r in result['responses']], [1, 2])
        self.assertTrue(result['cache_recovery']['recovered'])

    def test_busy_and_other_sqlite_errors_never_quarantine(self):
        self.search('--index-only')
        cache = search.CACHE_PATH
        before = cache.read_bytes()
        for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
                     sqlite3.SQLITE_FULL, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_ERROR):
            with self.subTest(code=code), patch.object(search, 'run_prepared',
                    side_effect=self.sqlite_error(code)) as run, patch.object(search.time, 'sleep'):
                with self.assertRaises(SystemExit) as failure:
                    self.search('--pages', 1)
                self.assertEqual(failure.exception.code, 2)
                self.assertEqual(run.call_count, 3 if cache_safety.is_busy(self.sqlite_error(code)) else 1)
            self.assertEqual(cache.read_bytes(), before)
            self.assertFalse(list(cache.parent.glob('*.corrupt-*.bak')))

    def test_repeated_corruption_stops_after_one_preserved_backup(self):
        self.search('--index-only')
        with patch.object(search, 'run_prepared', side_effect=self.sqlite_error(sqlite3.SQLITE_CORRUPT)) as run:
            with self.assertRaises(SystemExit):
                self.search('--pages', 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(list(search.CACHE_PATH.parent.glob('*.corrupt-*.bak'))), 1)

    def test_cache_paths_cannot_alias_original_pdf(self):
        before = reference_cache.digest(self.source)
        alias = self.directory / 'hardlink.sqlite3'
        os.link(self.source, alias)
        for path in (self.source, alias, search.LIBRARY_ROOT / 'unsafe.sqlite3'):
            with self.subTest(path=path), self.assertRaises(SystemExit):
                self.search('--cache-path', path, '--pages', 1)
        self.assertEqual(reference_cache.digest(self.source), before)
        self.assertFalse(search.CACHE_PATH.exists())

    def test_quarantine_preserves_main_file_and_all_sidecars(self):
        cache = search.CACHE_PATH
        cache.parent.mkdir(parents=True)
        for suffix in ('', '-wal', '-shm', '-journal'):
            Path(str(cache) + suffix).write_bytes(('evidence' + suffix).encode())
        with cache_safety.cache_lock(cache):
            backup = cache_safety.quarantine_cache(cache)
        for suffix in ('', '-wal', '-shm', '-journal'):
            self.assertEqual(Path(str(backup) + suffix).read_bytes(), ('evidence' + suffix).encode())
            self.assertFalse(Path(str(cache) + suffix).exists())

    def test_failed_preservation_rolls_back_without_rebuilding(self):
        cache = search.CACHE_PATH
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b'damaged')
        wal = Path(str(cache) + '-wal')
        wal.write_bytes(b'wal evidence')
        original_rename = Path.rename
        def denied(path, target):
            if path == cache.resolve():
                raise PermissionError('isolated denial')
            return original_rename(path, target)
        with patch.object(Path, 'rename', denied), self.assertRaises(cache_safety.CacheRecoveryError):
            cache_safety.quarantine_cache(cache)
        self.assertEqual(cache.read_bytes(), b'damaged')
        self.assertEqual(wal.read_bytes(), b'wal evidence')
        self.assertFalse(list(cache.parent.glob('*.corrupt-*')))

    def test_permission_error_never_resets_cache(self):
        self.search('--index-only')
        before = search.CACHE_PATH.read_bytes()
        with patch.object(search, 'connect', side_effect=PermissionError('isolated denial')):
            with self.assertRaises(SystemExit):
                self.search('--pages', 1)
        self.assertEqual(search.CACHE_PATH.read_bytes(), before)
        self.assertFalse(list(search.CACHE_PATH.parent.glob('*.corrupt-*')))

    def test_lock_timeout_returns_no_partial_answer_or_reset(self):
        self.search('--index-only')
        cache = search.CACHE_PATH
        before = cache.read_bytes()
        with cache_safety.cache_lock(cache), patch.object(cache_safety, 'LOCK_TIMEOUT', 0.04):
            # The sidecar guard covers even readers, making quarantine safe across chats.
            out, errors = io.StringIO(), io.StringIO()
            metadata = {}
            with patch.object(sys, 'argv', ['search', '--source', str(self.source), '--pages', '1', '--json']), \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(errors):
                started = time.monotonic()
                with self.assertRaises(SystemExit):
                    search.main(metadata)
            self.assertLess(time.monotonic() - started, 1)
            self.assertGreaterEqual(metadata['cache_wait_ms'], 35)
            self.assertEqual(out.getvalue(), '')
            self.assertIn('busy', errors.getvalue())
            self.assertEqual(cache.read_bytes(), before)
        self.assertFalse(list(cache.parent.glob('*.corrupt-*')))
        self.assertTrue(self.search('--pages', 1)['results'])

    def test_parallel_cli_lookups_share_one_index_and_reuse_it(self):
        command = [sys.executable, '-B', '-X', 'utf8', str(SCRIPTS / 'search_pdf.py'),
                   '--source', str(self.source), '--cache-path', str(search.CACHE_PATH),
                   '--keyword', 'public corridor', '--json']
        env = dict(os.environ, LOCALAPPDATA=str(self.directory / 'runtime'))
        processes = []
        try:
            with cache_safety.cache_lock(search.CACHE_PATH):
                processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              text=True, env=env) for _ in range(2)]
            results = []
            for process in processes:
                out, error = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, error)
                results.append(json.loads(out))
            self.assertEqual(sum(bool(r['indexed_pages']) for r in results), 1)
            self.assertEqual(results[0]['results'], results[1]['results'])
            self.assertFalse(list(search.CACHE_PATH.parent.glob('*.corrupt-*')))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_process_exit_releases_lock_even_with_sidecar_left_behind(self):
        code = ('import os,sys;sys.path.insert(0,sys.argv[1]);from cache_safety import cache_lock;'
                '\nwith cache_lock(sys.argv[2]):os._exit(0)')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(SCRIPTS), str(search.CACHE_PATH)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(str(search.CACHE_PATH) + '.lock').exists())
        self.assertTrue(self.search('--pages', 1)['results'])

    def test_external_writer_contention_recovers_after_bounded_retry(self):
        self.search('--index-only')
        conn = sqlite3.connect(search.CACHE_PATH, check_same_thread=False)
        conn.execute('BEGIN IMMEDIATE')
        released = threading.Event()
        def release():
            time.sleep(0.35)
            conn.rollback()
            released.set()
        worker = threading.Thread(target=release)
        worker.start()
        try:
            # Force a source refresh, which needs the external writer's SQLite lock.
            result = self.search('--rebuild', '--keyword', 'public corridor')
            self.assertTrue(released.is_set())
            self.assertEqual(result['results'][0]['pdf_page'], 1)
            self.assertNotIn('cache_recovery', result)
        finally:
            worker.join(timeout=5)
            conn.close()

    def test_extended_error_codes_are_classified_by_primary_code(self):
        self.assertTrue(cache_safety.is_busy(self.sqlite_error(sqlite3.SQLITE_BUSY | (2 << 8))))
        self.assertTrue(cache_safety.is_corrupt(self.sqlite_error(sqlite3.SQLITE_CORRUPT | (1 << 8))))
        self.assertFalse(cache_safety.is_corrupt(sqlite3.DatabaseError('malformed, but no confirmed code')))

    def test_recovery_diagnostics_record_status_without_source_text_or_paths(self):
        search.CACHE_PATH.parent.mkdir(parents=True)
        search.CACHE_PATH.write_bytes(b'broken index')
        log = self.directory / 'diagnostics.jsonl'
        with patch.object(diagnostics, 'LOG_PATH', log), \
             patch.object(sys, 'argv', ['search', '--source', str(self.source), '--keyword', 'public corridor', '--json']), \
             contextlib.redirect_stdout(io.StringIO()):
            with diagnostics.diagnostic_run('search') as metadata:
                search.main(metadata)
        raw = log.read_text(encoding='utf-8')
        entry = json.loads(raw)
        self.assertTrue(entry['cache_recovered'])
        self.assertEqual(entry['cache_busy_retries'], 0)
        self.assertEqual(entry['status'], 'ok')
        self.assertIn('cache_wait_ms', entry)
        self.assertNotIn('corridor', raw)
        self.assertNotIn('preserved_index', raw)
        self.assertNotIn(str(self.source), raw)

    def test_search_stage_timings_distinguish_cold_warm_and_context_reads(self):
        cold, warm = {}, {}
        args = ('--source', self.source, '--query', 'minimum width public corridor',
                '--focused', '--with-context', '--json')
        first = invoke(search, *args, diagnostic=cold)
        second = invoke(search, *args, diagnostic=warm)
        expected = {'cache_open', 'source_validation', 'cache_commit', 'retrieval', 'context_loading'}
        self.assertTrue(expected <= cold['stages_ms'].keys())
        self.assertTrue(expected <= warm['stages_ms'].keys())
        self.assertIn('indexing', cold['stages_ms'])
        self.assertNotIn('indexing', warm['stages_ms'])
        self.assertEqual(first['results'], second['results'])
        self.assertNotIn('stages_ms', second)
        self.assertTrue(all(value >= 0 for value in cold['stages_ms'].values()))

    def test_batch_timings_do_not_add_source_checks_or_searches(self):
        fields = {}
        jobs = [{'keywords': ['public corridor']}, {'pages': [2]}]
        with patch.object(search, 'needs_index', wraps=search.needs_index) as freshness, \
             patch.object(search, 'retrieve', wraps=search.retrieve) as retrieve:
            result = invoke(search, '--source', self.source, '--batch-json', json.dumps(jobs), diagnostic=fields)
        self.assertEqual(freshness.call_count, 1)
        self.assertEqual(retrieve.call_count, 2)
        self.assertEqual(len(result['responses']), 2)
        self.assertIn('retrieval', fields['stages_ms'])
        self.assertNotIn('context_loading', fields['stages_ms'])

    def test_highlight_timings_preserve_verification_on_cache_reuse(self):
        cold, warm = {}, {}
        args = (self.source, '--page', 1, '--text', SENTENCE, '--excerpt-output')
        first = invoke(highlight, *args, self.directory / 'references' / 'timed.pdf', diagnostic=cold)
        second = invoke(highlight, *args, self.directory / 'references' / 'reused.pdf', diagnostic=warm)
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual(first['preview'], second['preview'])
        self.assertTrue(warm['verified'])
        for fields in (cold, warm):
            self.assertTrue({'source_open', 'source_validation', 'passage_matching', 'verification', 'retention'}
                            <= fields['stages_ms'].keys())
        self.assertIn('pdf_generation', cold['stages_ms'])
        self.assertIn('excerpt_registration', cold['stages_ms'])
        self.assertIn('excerpt_lookup', warm['stages_ms'])
        self.assertNotIn('pdf_generation', warm['stages_ms'])
        self.assertFalse((self.directory / 'references' / 'reused.pdf').exists())
        self.assertNotIn('stages_ms', first)
        self.assertNotIn('stages_ms', second)

    def test_failed_pdf_verification_is_timed_and_still_stops_output(self):
        fields = {}
        target = self.directory / 'references' / 'failure.pdf'
        with patch.object(highlight, 'covers_highlights', return_value=False):
            with self.assertRaisesRegex(AssertionError, 'Complete highlight coverage missing'):
                invoke(highlight, self.source, '--page', 1, '--text', SENTENCE,
                       '--excerpt-output', target, diagnostic=fields)
        self.assertIn('verification', fields['stages_ms'])
        self.assertNotIn('verified', fields)
        self.assertNotIn('excerpt_registration', fields['stages_ms'])

    def test_cli_writes_stage_timings_only_to_local_log(self):
        env = dict(os.environ, LOCALAPPDATA=str(self.directory / 'runtime'))
        command = [sys.executable, '-B', '-X', 'utf8', str(SCRIPTS / 'search_pdf.py'),
                   '--source', str(self.source), '--keyword', 'public corridor', '--json']
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('stages_ms', json.loads(result.stdout))
        path = Path(env['LOCALAPPDATA']) / 'WZMH' / 'CodeAI' / 'logs' / 'diagnostics.jsonl'
        raw = path.read_text(encoding='utf-8')
        row = json.loads(raw)
        self.assertIn('retrieval', row['stages_ms'])
        self.assertIn('indexing', row['stages_ms'])
        self.assertNotIn('public corridor', raw)
        self.assertNotIn(str(self.source), raw)
        self.assertEqual(row['status'], 'ok')


class HealthCheckRegression(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wzmh-health-')
        self.root = Path(self.temporary.name)
        self.runtime = self.root / 'runtime'
        self.library = self.root / 'assets' / 'source_documents'
        self.library.mkdir(parents=True)
        self.manifest = self.root / 'references' / 'library-checksums.json'
        self.manifest.parent.mkdir()
        entries = json.loads((SCRIPTS.parent / 'references' / 'library-checksums.json').read_text(encoding='utf-8'))['pdfs']
        with pymupdf.open() as document:
            document.new_page()
            document.set_metadata({'author': 'Fixture-A'})
            raw = document.tobytes()
        self.entries = []
        for entry in entries:
            (self.library / entry['filename']).write_bytes(raw)
            self.entries.append({'filename': entry['filename'], 'sha256': hashlib.sha256(raw).hexdigest()})
        self.write_manifest(self.entries)

    def tearDown(self):
        self.temporary.cleanup()

    def write_manifest(self, entries):
        self.manifest.write_text(json.dumps({'pdfs': entries}), encoding='utf-8')

    def run_check(self):
        return health.check_installation(self.root, self.runtime)

    def test_healthy_installation_preserves_sources_existing_cache_and_logs(self):
        before = {path: path.read_bytes() for path in self.library.iterdir()}
        for folder, name in [('cache', 'pdf_text_index.sqlite3'), ('logs', 'diagnostics.jsonl')]:
            directory = self.runtime / folder
            directory.mkdir(parents=True)
            (directory / name).write_bytes(b'existing data must remain untouched')
        report = self.run_check()
        self.assertTrue(report['ok'], report)
        self.assertEqual(len(report['checks']), 8)
        self.assertTrue(all(check['status'] == 'PASS' for check in report['checks']))
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        for path in self.runtime.glob('*/*'):
            self.assertEqual(path.read_bytes(), b'existing data must remain untouched')
        self.assertFalse(list(self.runtime.rglob('.wzmh-health-*')))
        self.assertEqual(len(list(self.library.iterdir())), 3)

    def test_missing_dependency_skips_opening_without_false_success(self):
        with patch.object(health, 'load_pdf_engine', side_effect=ImportError('isolated missing dependency')):
            report = self.run_check()
        self.assertFalse(report['ok'])
        self.assertEqual(next(c for c in report['checks'] if c['name'] == 'PyMuPDF')['status'], 'FAIL')
        self.assertEqual(sum(c['status'] == 'SKIP' for c in report['checks']), 3)
        self.assertTrue(all(c.get('fix') for c in report['checks'] if c['status'] != 'PASS'))

    def test_incompatible_pdf_engine_is_not_reported_as_healthy(self):
        class OldEngine:
            open = staticmethod(pymupdf.open)
            class Document:
                pass
        with patch.object(health, 'load_pdf_engine', return_value=OldEngine):
            report = self.run_check()
        self.assertFalse(report['ok'])
        self.assertEqual(next(c for c in report['checks'] if c['name'] == 'PyMuPDF')['status'], 'FAIL')

    def test_sqlite_fts_failure_has_fix_and_does_not_use_disk_cache(self):
        with patch.object(sqlite3, 'connect', side_effect=sqlite3.OperationalError('no such module: fts5')):
            report = self.run_check()
        check = next(c for c in report['checks'] if c['name'] == 'SQLite full-text search')
        self.assertFalse(report['ok'])
        self.assertEqual(check['status'], 'FAIL')
        self.assertIn('FTS5', check['fix'])
        self.assertFalse(list(self.runtime.rglob('*.sqlite3')))

    def test_source_change_fails_checksum_without_replacing_file_or_expected_hash(self):
        source = self.library / self.entries[0]['filename']
        original_manifest = self.manifest.read_bytes()
        raw = source.read_bytes()
        self.assertIn(b'Fixture-A', raw)
        changed = raw.replace(b'Fixture-A', b'Fixture-B')
        source.write_bytes(changed)
        report = self.run_check()
        check = next(c for c in report['checks'] if c['name'] == 'PDF: ' + source.name)
        self.assertFalse(report['ok'])
        self.assertEqual(check['status'], 'FAIL')
        self.assertIn('SHA-256', check['detail'])
        self.assertEqual(source.read_bytes(), changed)
        self.assertEqual(self.manifest.read_bytes(), original_manifest)

    def test_missing_pdf_reports_failure_and_checks_remaining_files(self):
        (self.library / self.entries[0]['filename']).unlink()
        report = self.run_check()
        pdfs = [c for c in report['checks'] if c['name'].startswith('PDF:')]
        self.assertFalse(report['ok'])
        self.assertEqual([c['status'] for c in pdfs], ['FAIL', 'PASS', 'PASS'])

    def test_corrupt_pdf_is_rejected_even_when_fixture_checksum_matches(self):
        source = self.library / self.entries[0]['filename']
        source.write_bytes(b'not a pdf')
        self.entries[0]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.write_manifest(self.entries)
        report = self.run_check()
        self.assertFalse(report['ok'])
        self.assertEqual(next(c for c in report['checks'] if c['name'] == 'PDF: ' + source.name)['status'], 'FAIL')

    def test_invalid_manifest_never_opens_unlisted_paths(self):
        for change in ('traversal', 'duplicate', 'bad_hash'):
            entries = [dict(entry) for entry in self.entries]
            if change == 'traversal': entries[0]['filename'] = '../outside.pdf'
            elif change == 'duplicate': entries[0]['filename'] = entries[1]['filename']
            else: entries[0]['sha256'] = 'invalid'
            self.write_manifest(entries)
            with self.subTest(change=change), patch.object(health, 'check_pdf') as check_pdf:
                report = self.run_check()
                self.assertFalse(report['ok'])
                check_pdf.assert_not_called()
                self.assertEqual(next(c for c in report['checks'] if c['name'] == 'Bundled PDF manifest')['status'], 'FAIL')

    def test_missing_manifest_is_actionable(self):
        self.manifest.unlink()
        report = self.run_check()
        self.assertFalse(report['ok'])
        check = next(c for c in report['checks'] if c['name'] == 'Bundled PDF manifest')
        self.assertIn('library-checksums.json', check['fix'])

    def test_blocked_runtime_folder_does_not_delete_existing_file(self):
        self.runtime.mkdir()
        blocker = self.runtime / 'cache'
        blocker.write_bytes(b'keep this file')
        report = self.run_check()
        self.assertFalse(report['ok'])
        self.assertEqual(next(c for c in report['checks'] if c['name'] == 'Local cache')['status'], 'FAIL')
        self.assertEqual(next(c for c in report['checks'] if c['name'] == 'Local logs')['status'], 'PASS')
        self.assertEqual(blocker.read_bytes(), b'keep this file')

    def test_probe_permission_failure_is_not_ignored(self):
        with patch.object(health.tempfile, 'TemporaryFile', side_effect=PermissionError('isolated probe denial')):
            report = self.run_check()
        self.assertFalse(report['ok'])
        self.assertEqual([c['status'] for c in report['checks'] if c['name'].startswith('Local ')], ['FAIL', 'FAIL'])
        self.assertFalse(list(self.runtime.rglob('.wzmh-health-*')))

    def test_json_cli_exit_status_matches_health_and_uses_user_runtime(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('health_check.py', 'diagnostics.py'):
            (scripts / name).write_bytes((SCRIPTS / name).read_bytes())
        env = dict(os.environ, LOCALAPPDATA=str(self.root / 'user-local'))
        command = [sys.executable, '-B', str(scripts / 'health_check.py'), '--json']
        first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(json.loads(first.stdout)['ok'])
        self.assertTrue((self.root / 'user-local' / 'WZMH' / 'CodeAI' / 'cache').is_dir())
        self.assertFalse((self.root / '.cache').exists())
        (self.library / self.entries[0]['filename']).unlink()
        failed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertFalse(json.loads(failed.stdout)['ok'])

    @unittest.skipUnless(os.name == 'nt', 'Windows native launcher')
    def test_native_launcher_healthy_install_and_missing_helper_in_spaced_path(self):
        scripts = self.root / 'package with spaces' / 'scripts'
        scripts.mkdir(parents=True)
        for name in ('health_check.cmd', 'health_check.py', 'diagnostics.py'):
            (scripts / name).write_bytes((SCRIPTS / name).read_bytes())
        # The helper locates its own deployed root, even when paths contain spaces.
        package = scripts.parent
        (package / 'references').mkdir()
        (package / 'references' / 'library-checksums.json').write_bytes(self.manifest.read_bytes())
        library = package / 'assets' / 'source_documents'
        library.mkdir(parents=True)
        for path in self.library.iterdir():
            (library / path.name).write_bytes(path.read_bytes())
        env = dict(os.environ, WZMH_CODE_PYTHON=sys.executable, LOCALAPPDATA=str(self.root / 'user-data'))
        command = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(scripts / 'health_check.cmd'), '--json']
        healthy = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(healthy.returncode, 0, healthy.stderr)
        self.assertTrue(json.loads(healthy.stdout)['ok'])
        (scripts / 'health_check.py').unlink()
        failed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertEqual(json.loads(failed.stdout)['checks'][0]['name'], 'Skill files')

    @unittest.skipUnless(os.name == 'nt', 'Windows native launcher')
    def test_native_launcher_rejects_missing_relative_or_directory_python_without_fallback(self):
        command = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(SCRIPTS / 'health_check.cmd'), '--json']
        local = self.root / 'user-data'
        for executable in (str(self.root / 'missing-python.exe'), 'python.exe', str(self.root)):
            with self.subTest(executable=executable):
                env = dict(os.environ, WZMH_CODE_PYTHON=executable, LOCALAPPDATA=str(local))
                failed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(failed.returncode, 1, failed.stderr)
                self.assertFalse(json.loads(failed.stdout)['ok'])
                self.assertEqual(json.loads(failed.stdout)['checks'][0]['name'], 'Python')
                self.assertFalse(local.exists())


class DiagnosticsRegression(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wzmh-diagnostics-')
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_stage_timer_accumulates_elapsed_time_even_when_stage_fails(self):
        fields = {}
        with patch.object(diagnostics.time, 'perf_counter', side_effect=[10, 10.125, 20, 20.25]):
            with diagnostics.stage_timer(fields, 'retrieval'):
                pass
            with self.assertRaisesRegex(ValueError, 'original error'):
                with diagnostics.stage_timer(fields, 'retrieval'):
                    raise ValueError('original error')
        self.assertEqual(fields['stages_ms'], {'retrieval': 375.0})

    def test_disabled_stage_timings_do_not_read_clock_or_hide_errors(self):
        with patch.object(diagnostics.time, 'perf_counter', side_effect=AssertionError('clock not needed')):
            with diagnostics.stage_timer(None, 'retrieval'):
                pass
            with self.assertRaisesRegex(ValueError, 'original error'):
                with diagnostics.stage_timer(None, 'retrieval'):
                    raise ValueError('original error')

    def test_localappdata_location_is_independent_of_skill_install(self):
        local_data = self.directory / 'user' / 'AppData' / 'Local'
        install = self.directory / 'installed-skill'
        with patch.dict(os.environ, {'LOCALAPPDATA': str(local_data)}), \
             patch.object(diagnostics, '__file__', str(install / 'scripts' / 'diagnostics.py')):
            target = diagnostics.default_log_path()
        self.assertEqual(target, local_data / 'WZMH' / 'CodeAI' / 'logs' / 'diagnostics.jsonl')
        self.assertFalse(target.parent.exists())
        with patch.object(diagnostics, 'LOG_PATH', target):
            with diagnostics.diagnostic_run('search') as fields:
                fields.update(mode='pages', result_pages=1)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['status'], 'ok')
        self.assertFalse(install.exists())

    def test_distinct_users_get_distinct_logs(self):
        paths = []
        for user in ('user-a', 'user-b'):
            with patch.dict(os.environ, {'LOCALAPPDATA': str(self.directory / user)}):
                target = diagnostics.default_log_path()
            with patch.object(diagnostics, 'LOG_PATH', target):
                with diagnostics.diagnostic_run('search'):
                    pass
            paths.append(target)
        self.assertNotEqual(*paths)
        self.assertTrue(all(path.is_file() for path in paths))

    def test_windows_missing_localappdata_falls_back_to_user_profile(self):
        home = self.directory / 'user'
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, 'platform', 'win32'), \
             patch.object(Path, 'home', return_value=home):
            self.assertEqual(diagnostics.default_log_path(),
                             home / 'AppData' / 'Local' / 'WZMH' / 'CodeAI' / 'logs' / 'diagnostics.jsonl')

    def test_non_windows_fallback_stays_in_user_state_folder(self):
        home = self.directory / 'user'
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, 'platform', 'linux'), \
             patch.object(Path, 'home', return_value=home):
            self.assertEqual(diagnostics.default_log_path(),
                             home / '.local' / 'state' / 'WZMH' / 'CodeAI' / 'logs' / 'diagnostics.jsonl')
            with patch.dict(os.environ, {'XDG_STATE_HOME': str(home / 'state')}):
                self.assertEqual(diagnostics.default_log_path(),
                                 home / 'state' / 'WZMH' / 'CodeAI' / 'logs' / 'diagnostics.jsonl')

    def test_success_and_error_logs_do_not_record_exception_messages(self):
        target = self.directory / 'logs' / 'diagnostics.jsonl'
        with patch.object(diagnostics, 'LOG_PATH', target):
            with diagnostics.diagnostic_run('search') as fields:
                fields.update(mode='pages', result_pages=1, reused_sources=1)
            with self.assertRaises(ValueError):
                with diagnostics.diagnostic_run('highlight'):
                    raise ValueError('PRIVATE_SENTINEL_DO_NOT_LOG')
        raw = target.read_text(encoding='utf-8')
        rows = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual([row['status'] for row in rows], ['ok', 'error'])
        self.assertEqual(rows[-1]['error_type'], 'ValueError')
        self.assertTrue(all('elapsed_ms' in row and 'time_utc' in row for row in rows))
        self.assertNotIn('PRIVATE_SENTINEL_DO_NOT_LOG', raw)

    def test_log_rotation_remains_bounded(self):
        target = self.directory / 'logs' / 'diagnostics.jsonl'
        with patch.object(diagnostics, 'LOG_PATH', target):
            for number in range(900):
                with diagnostics.diagnostic_run('search') as fields:
                    fields.update(mode='query', request_count=1, source_count=1,
                                  indexed_pages=0, reused_sources=1, result_pages=3)
        files = list(target.parent.glob('diagnostics.jsonl*'))
        self.assertEqual(len(files), 2)
        self.assertTrue(target.with_name('diagnostics.jsonl.1').is_file())
        self.assertTrue(all(path.stat().st_size <= 131072 for path in files))

    def test_logging_failure_does_not_break_lookup_or_hide_original_error(self):
        blocker = self.directory / 'not-a-directory'
        blocker.touch()
        with patch.object(diagnostics, 'LOG_PATH', blocker / 'diagnostics.jsonl'):
            with diagnostics.diagnostic_run('search'):
                pass
            with self.assertRaises(ValueError):
                with diagnostics.diagnostic_run('search'):
                    raise ValueError('Original operation error')


if __name__ == '__main__':
    unittest.main()
