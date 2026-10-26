"""Exact source matching and independent word-box coverage for line-end hyphens."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import highlight_sentence as highlight
import pymupdf
from test_phase1 import invoke

QUOTE='Electrical equipment shall have a fire-resistance rating of not less than 1 h.'

class Hyphenation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='obc-hyphen-')
        self.directory=Path(self.temp.name)
        self.source=self.directory/'source.pdf'
        doc=pymupdf.open()
        page=doc.new_page();page.insert_text((50,60),QUOTE.replace('fire-resistance','fire-\nresistance'),fontsize=9)
        page=doc.new_page();page.insert_text((50,60),QUOTE,fontsize=9)
        page=doc.new_page();page.insert_text((50,60),QUOTE.replace('fire-resistance','fire-\nresistance')+'\n'+QUOTE,fontsize=9)
        doc.save(self.source);doc.close()

    def tearDown(self):self.temp.cleanup()

    def test_baked_output_covers_every_source_word_and_keeps_original_text(self):
        result=invoke(highlight,self.source,'--page',1,'--text',QUOTE,'--excerpt-output',self.directory/'reference.pdf')
        with pymupdf.open(self.source) as original,pymupdf.open(result['preview']) as saved:
            self.assertEqual(original[0].get_text(),saved[0].get_text())
            yellow=[d['rect'] for d in saved[0].get_drawings() if d.get('fill')==(1.,1.,0.)]
            for word in original[0].get_text('words'):
                box=pymupdf.Rect(word[:4])
                self.assertTrue(any((r&box).get_area()>=.9*box.get_area() for r in yellow),word[4])
            self.assertFalse(list(saved[0].annots()))
            self.assertEqual(saved[0].mediabox,original[0].mediabox)

    def test_only_original_line_end_hyphens_may_be_joined(self):
        with pymupdf.open(self.source) as doc:
            literal,quads=highlight.exact_passage(doc[0],QUOTE.replace('fire-resistance','fireresistance'))
            self.assertIn('fire- resistance',literal)
            self.assertTrue(quads)
            with self.assertRaises(ValueError):highlight.exact_passage(doc[1],QUOTE.replace('fire-resistance','fireresistance'))

    def test_wrong_number_or_punctuation_still_rejected(self):
        with pymupdf.open(self.source) as doc:
            for wrong in [QUOTE.replace('1 h','2 h'),QUOTE.replace('shall','may'),QUOTE.replace('1 h.','1 h!')]:
                with self.assertRaises(ValueError):highlight.exact_passage(doc[0],wrong)

    def test_layout_equivalent_ambiguity_rejected(self):
        with pymupdf.open(self.source) as doc:
            with self.assertRaises(ValueError):highlight.exact_passage(doc[2],QUOTE)

    def test_layout_variants_reuse_the_same_fully_verified_pdf(self):
        first=invoke(highlight,self.source,'--page',1,'--text',QUOTE,'--excerpt-output',self.directory/'one.pdf')
        second=invoke(highlight,self.source,'--page',1,'--text',QUOTE.replace('fire-resistance','fire- resistance'),'--excerpt-output',self.directory/'two.pdf')
        self.assertTrue(second['reused'])
        self.assertEqual(first['preview'],second['preview'])

if __name__=='__main__':unittest.main()
