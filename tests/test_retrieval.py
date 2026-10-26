"""Small structural/relevance fixtures; no original PDFs, network or model calls."""
import json
import contextlib
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import search_pdf as search
import provision_index

class StructuralRetrieval(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='obc-routing-')
        self.source=Path(self.temp.name)/'fixture.pdf'
        self.source.write_bytes(b'cached-text-fixture-only')
        self.previous=search.CACHE_PATH
        search.CACHE_PATH=Path(self.temp.name)/'cache.sqlite3'
        self.conn=search.connect()
        self.texts=[
            'Division B - Part 3\n3.6.2.1. Fire Separations Around Service Rooms\n(1) Fuel-fired appliances shall be separated, except as permitted by Sentences (2) and (8).\n',
            '2024 Building Code Compendium\n3.6.2.5.\nDivision B - Part 3\n(6) Electrical equipment required by a regulation shall be in a service room.\n(8) Where equipment is limited, neither hazardous nor essential to fire safety, a separation is not required.\n3.6.2.2. Service Rooms Under Exits\n(1) Rooms subject to explosion shall not be under an exit.\n',
            'Division B - Part 3\n3.8.2.3. Washrooms Required to be Barrier-Free\n(3) The number of stalls shall conform to Table 3.8.2.3.-B.\n',
            'Division B - Part 3\nTable 3.8.2.3.-B\nForming Part of Sentence 3.8.2.3.(3)\nNumber of Water Closets\n4 to 9\n1\nNotes to Table: include the required footnote.\n3.8.2.4. Hotels\n(1) Hotels shall provide accessible suites.\n',
            'Division B\nA-3.6.2.1. Service Room Explanatory Notes\n(1) This is a related explanation, not an operative Article.\n',
            'Table of Contents\n3.6.2.1. Fire Separations Around Service Rooms\n3.6.2.2. Service Rooms Under Exits\n',
            'Division B - Part 11\nCompliance Alternative\n3.6.2.1. Fire Separations Around Service Rooms\n(1) This table references a different Article.\n',
            'Division B\nTable 3.10.1.1. (Continued)\nProvision\nFunctional Statements and Objectives\n3.6.2.1. Fire Separations Around Service Rooms\n(6)\n[F05-OS1.5]\n',
            'Division C - Part 3\n3.6.2.1. Definition\n(1) In this Subsection, registered person means a person registered by the Act.\n',
            'Division B - Part 9\n9.10.11.4. Firewalls in Detached Garages\n(1) A garage detached from a dwelling but attached to another garage has a fire-resistance rating that may be reduced or omitted in this artificial test.\n',
            'Division B - Part 9\n9.10.9.18. Separation of Storage Garages\n(1) An attached garage serving only its dwelling requires the separation conditions in this artificial test.\n',
        ]
        key=str(self.source.resolve());st=self.source.stat()
        self.conn.execute('INSERT INTO sources VALUES (?,?,?,?)',(key,st.st_size,st.st_mtime_ns,len(self.texts)))
        for page,text in enumerate(self.texts,1):
            cur=self.conn.execute('INSERT INTO page_text VALUES (?,?,?)',(key,page,text))
            self.conn.execute('INSERT INTO page_lookup VALUES (?,?,?)',(key,page,cur.lastrowid))
        self.conn.commit()

    def tearDown(self):
        self.conn.close();search.CACHE_PATH=self.previous;self.temp.cleanup()

    def test_sentence_routes_to_continuation_not_page_header_or_mention(self):
        rows=search.retrieve(self.conn,[self.source],query='Explain Sentence 3.6.2.1.(6).',focused=True,max_results=3)
        self.assertEqual(rows[0]['pdf_page'],2)
        self.assertEqual(rows[0]['provisions'][0]['reference'],'3.6.2.1.')
        self.assertIn('(8) Where equipment',rows[0]['provisions'][0]['text'])
        self.assertEqual(rows[0]['provisions'][0]['context_pages'],[1])
        self.assertNotIn(7,[r['pdf_page'] for r in rows])
        self.assertNotIn(8,[r['pdf_page'] for r in rows])
        self.assertTrue(all(b['division']=='B' for r in rows for b in r['provisions']))

    def test_explicit_reference_missing_does_not_return_unrelated_evidence(self):
        self.assertEqual(search.retrieve(self.conn,[self.source],reference='3.6.2.99.'),[])
        self.assertEqual(search.retrieve(self.conn,[self.source],reference='3.6.2.1.(99)'),[])
        with self.assertRaises(ValueError):search.retrieve(self.conn,[self.source],reference='words only')

    def test_explicit_division_does_not_silently_route_to_other_division(self):
        rows=search.retrieve(self.conn,[self.source],reference='Division C 3.6.2.1.')
        self.assertEqual([r['pdf_page'] for r in rows],[9])
        self.assertEqual(search.retrieve(self.conn,[self.source],reference='Division C 3.6.2.1.(6)'),[])

    def test_raw_offsets_tables_and_cross_references_preserved(self):
        rows=search.retrieve(self.conn,[self.source],reference='3.8.2.3.',max_results=5)
        self.assertEqual([r['pdf_page'] for r in rows],[3,4])
        for row in rows:
            for block in row['provisions']:
                self.assertEqual(block['text'],self.texts[row['pdf_page']-1][block['start']:block['end']])
        self.assertIn('required footnote',rows[1]['provisions'][0]['text'])
        self.assertIn('3.8.2.3',rows[0]['requested_reference'])

    def test_note_reference_is_not_silently_an_operative_article(self):
        rows=search.retrieve(self.conn,[self.source],reference='A-3.6.2.1.')
        self.assertTrue(rows)
        self.assertTrue(all(b['kind']=='note' for r in rows for b in r['provisions']))

    def test_reindex_invalidation_rebuilds_structure(self):
        provision_index.ensure(self.conn,self.source)
        self.conn.execute('UPDATE page_text SET text=replace(text,?,?) WHERE pdf_page=1',('3.6.2.1.','3.6.2.9.'))
        provision_index.invalidate(self.conn,self.source)
        self.conn.commit()
        rows=search.retrieve(self.conn,[self.source],reference='3.6.2.9.')
        self.assertEqual(rows[0]['pdf_page'],1)
        self.assertIn('3.6.2.9.',rows[0]['provisions'][0]['text'])

    def test_natural_query_prefers_binding_block_and_keeps_exception(self):
        rows=search.retrieve(self.conn,[self.source],query='electrical equipment service room limited fire safety',focused=True,max_results=3)
        self.assertEqual(rows[0]['pdf_page'],2)
        self.assertIn('neither hazardous nor essential',rows[0]['provisions'][0]['text'])
        self.assertEqual(rows[0]['provisions'][0]['kind'],'article')

    def test_context_in_same_call_keeps_table_governing_page(self):
        rows=search.retrieve(self.conn,[self.source],query='How many barrier free water closets?',focused=True,max_results=1)
        self.assertEqual(rows[0]['pdf_page'],4)
        search.add_context(self.conn,rows)
        self.assertEqual(rows[0]['context'][0]['pdf_page'],3)
        self.assertIn('(3) The number of stalls',rows[0]['context'][0]['text'])

    def test_batched_reference_pages_and_keywords_equal_individual(self):
        jobs={'requests':[{'id':'ref','reference':'3.6.2.1.(6)'},
                          {'id':'page','pages':[3,4]},
                          {'id':'word','keywords':['Electrical equipment'],'focused':True,'with_context':True}]}
        argv=sys.argv;out=io.StringIO()
        try:
            sys.argv=['search','--source',str(self.source),'--batch-json',json.dumps(jobs)]
            with contextlib.redirect_stdout(out):search.main()
        finally:sys.argv=argv
        data=json.loads(out.getvalue())
        self.assertEqual(data['responses'][0]['results'][0]['pdf_page'],2)
        self.assertEqual([r['pdf_page'] for r in data['responses'][1]['results']],[3,4])
        self.assertIn('context',data['responses'][2]['results'][0])

    def test_phrase_and_regex_legacy_modes_still_match(self):
        words=search.retrieve(self.conn,[self.source],keywords=['Electrical equipment'])
        regex=search.retrieve(self.conn,[self.source],regex=r'Electrical equipment')
        self.assertEqual(words[0]['pdf_page'],regex[0]['pdf_page'])

    def test_heading_contrast_does_not_prefer_detached_for_attached_query(self):
        rows=search.retrieve(self.conn,[self.source],query='When can the fire-resistance rating between an attached garage and dwelling be reduced or omitted?',focused=True,max_results=3)
        self.assertEqual(rows[0]['pdf_page'],11)

    def test_context_limit_reports_unread_continuation_instead_of_hiding_it(self):
        key=str(self.source.resolve())
        for page in range(12,16):
            raw=('Division B - Part 3\n3.2.9.1. Safety Conditions\n' if page==12 else '')+f'({page-11}) A complete condition on source page {page}.\n'
            cur=self.conn.execute('INSERT INTO page_text VALUES (?,?,?)',(key,page,raw))
            self.conn.execute('INSERT INTO page_lookup VALUES (?,?,?)',(key,page,cur.lastrowid))
        self.conn.execute('UPDATE sources SET pages=16 WHERE path=?',(key,))
        self.conn.commit()
        rows=search.retrieve(self.conn,[self.source],reference='3.2.9.1.',max_results=1)
        search.add_context(self.conn,rows)
        self.assertEqual([p['pdf_page'] for p in rows[0]['context']],[13,14])
        self.assertEqual(rows[0]['unread_context_pages'],[15])

    def test_reference_cap_reports_unresolved_requested_reference(self):
        argv=sys.argv;out=io.StringIO()
        try:
            sys.argv=['search','--source',str(self.source),'--reference','3.6.2.1. and 3.8.2.3.','--max-results','1','--json']
            with contextlib.redirect_stdout(out):search.main()
        finally:sys.argv=argv
        self.assertEqual(json.loads(out.getvalue())['unresolved_references'],['3.8.2.3'])

    def test_cached_structural_route_does_not_import_pdf_engine(self):
        code='import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import search_pdf as s;s.CACHE_PATH=Path(sys.argv[2]);c=s.connect();assert s.retrieve(c,[Path(sys.argv[3])],reference="3.6.2.1.");assert "pymupdf" not in sys.modules;c.close()'
        done=subprocess.run([sys.executable,'-c',code,str(Path(search.__file__).parent),str(search.CACHE_PATH),str(self.source)],capture_output=True,text=True)
        self.assertEqual(done.returncode,0,done.stderr)

    def test_malformed_reference_suffix_is_not_a_structural_match(self):
        self.assertEqual(provision_index.references('3.6.2.1wrong'),[])

if __name__=='__main__':unittest.main()
