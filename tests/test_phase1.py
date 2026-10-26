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
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import search_pdf as search
import highlight_sentence as highlight
import reference_cache
import pymupdf

SENTENCE = 'The minimum width of a public corridor shall be 1 100 mm.'


def invoke(module, *args):
    previous = sys.argv
    out = io.StringIO()
    sys.argv = [module.__file__, *map(str, args)]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            module.main()
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


if __name__ == '__main__':
    unittest.main()
