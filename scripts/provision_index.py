"""Derived structural metadata over the existing PDF page-text cache (no PDF imports)."""
from pathlib import Path
import re

VERSION = '4'  # Bump when structural parsing changes; derived metadata rebuilds once.
HEADING = re.compile(r'(?m)^[ \t]*((?:[A-Z]-)?\d+(?:\.\d+){3}\.?)'
                     r'(?:[ \t]+|[ \t]*\r?\n[ \t]*)([A-Z][^\n]{1,140})$')
REFERENCE = re.compile(r'(?<![\w.-])((?:[A-Z]-)?\d+(?:\.\d+){3})(?![\w]|\.\d)(?:\.)?(?:[ \t]*\((\d+)\))?')
SENTENCE = re.compile(r'(?m)^[ \t]*\(?\d+\)')


def schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS provision_meta(source TEXT PRIMARY KEY,size INTEGER,mtime_ns INTEGER,version TEXT);
        CREATE TABLE IF NOT EXISTS provision_spans(
            source TEXT,page INTEGER,start INTEGER,end INTEGER,reference TEXT,heading TEXT,division TEXT,kind TEXT);
        CREATE INDEX IF NOT EXISTS provision_reference ON provision_spans(source,reference,kind,page);
        CREATE INDEX IF NOT EXISTS provision_page ON provision_spans(source,page);
    ''')


def invalidate(conn, source):
    schema(conn)
    conn.execute('DELETE FROM provision_meta WHERE source=?', (str(source.resolve()),))
    conn.execute('DELETE FROM provision_spans WHERE source=?', (str(source.resolve()),))


def ensure(conn, source):
    """One metadata migration per source/version; never re-extract an unchanged PDF."""
    schema(conn)
    key = str(source.resolve())
    size, mtime = conn.execute('SELECT size,mtime_ns FROM sources WHERE path=?', (key,)).fetchone()
    if conn.execute('SELECT size,mtime_ns,version FROM provision_meta WHERE source=?', (key,)).fetchone() == (size,mtime,VERSION):
        return
    conn.execute('DELETE FROM provision_spans WHERE source=?', (key,))
    records = []
    carried = None
    previous_division = None
    for page, text in conn.execute('SELECT CAST(pdf_page AS INTEGER),text FROM page_text WHERE source_path=? ORDER BY CAST(pdf_page AS INTEGER)', (key,)):
        divisions = re.findall(r'Division\s+([ABC])\b', text[:400]+'\n'+text[-250:])
        division = divisions[0] if divisions else previous_division
        part = re.search(r'Division\s+[ABC][^\n]*?Part\s+(\d+)\b', text[:400])
        note_page = bool(re.search(r'(?m)^\s*A-\d+(?:\.\d+){2,}\.?(?:\([^\n)]*\))*\s+[A-Z]', text))
        alternative = 'Compliance Alternative' in text
        objective_table = 'Functional Statements and Objectives' in text and 'Provision' in text
        owner = re.search(r'(?m)^\s*Table\s+(\d+(?:\.\d+){3})',text) if objective_table else None
        if division != previous_division or note_page or alternative:
            carried = None
        if objective_table and (not owner or not carried or carried[0]!=owner.group(1)):
            carried = None
        previous_division = division
        headings = []
        for h in HEADING.finditer(text):
            ref, title = h.group(1).rstrip('.'), h.group(2).strip()
            if re.search(r'Compendium|Division|National Building|Copyright', title) or title.endswith('.'):
                continue
            if objective_table and (not owner or ref!=owner.group(1)):
                continue
            # Only a real Code article body establishes a heading. TOCs and table
            # rows referencing other Articles cannot establish binding provisions.
            tail = text[h.end():h.end()+700]
            first_sentence=SENTENCE.search(tail)
            if not (first_sentence or title == 'Reserved'):
                continue
            if first_sentence and HEADING.search(tail[:first_sentence.start()]):
                continue
            if part and ref.lstrip('A-').split('.')[0] != part.group(1):
                continue
            headings.append(h)
        starts = ([0] if not headings or headings[0].start() else []) + [h.start() for h in headings] + [len(text)]
        for start, end in zip(starts, starts[1:]):
            h = next((h for h in headings if h.start() == start), None)
            if h:
                carried = (h.group(1).rstrip('.'), h.group(2).strip(), division,
                           'note' if note_page or h.group(1).startswith('A-') else 'article')
            body = text[start:end]
            if carried is None or alternative:
                continue
            ref, title, div, kind = carried
            if not h and not (SENTENCE.search(body) or re.search(r'(?m)^\s*(?:[a-z]|[ivx]+)\)',body) or 'Forming Part of' in body):
                continue
            records.append((key,page,start,end,ref,title,div,kind))
    conn.executemany('INSERT INTO provision_spans VALUES (?,?,?,?,?,?,?,?)', records)
    conn.execute('INSERT OR REPLACE INTO provision_meta VALUES (?,?,?,?)', (key,size,mtime,VERSION))
    conn.commit()


def references(question):
    return list(dict.fromkeys((m.group(1), m.group(2)) for m in REFERENCE.finditer(question)))


def blocks(conn, source, page, text):
    rows = conn.execute('SELECT start,end,reference,heading,division,kind FROM provision_spans WHERE source=? AND page=? ORDER BY start',
                        (str(source.resolve()),page)).fetchall()
    return [make_block(conn,source,page,text,*r) for r in rows]


def make_block(conn, source, page, text, start, end, ref, heading, division, kind):
    body = text[start:end]
    article_pages = [r[0] for r in conn.execute('SELECT DISTINCT page FROM provision_spans WHERE source=? AND reference=? AND division IS ? AND kind=? ORDER BY page',
                                             (str(source.resolve()),ref,division,kind))]
    return {'reference':ref+'.','heading':heading,'division':division,'kind':kind,
            'start':start,'end':end,'text':body,'review_adjacent_pages':len(article_pages)>1,
            'context_pages':[p for p in article_pages if p!=page],
            'cross_references':list(dict.fromkeys(m.group(1) for m in REFERENCE.finditer(body) if m.group(1)!=ref)),
            'context_notice':'Read necessary continuation pages, table lead-ins/footnotes and cross-references before answering.'}


def route(conn, sources, requested, maximum, full_page=False, division=None):
    """Return structurally established articles, not every page mentioning a number."""
    results = []
    for source in sources:
        ensure(conn,source)
        key = str(source.resolve())
        for ref, sentence in requested:
            rows = conn.execute('''SELECT s.page,p.text,s.start,s.end,s.reference,s.heading,s.division,s.kind
                FROM provision_spans s JOIN page_lookup l ON l.source_path=s.source AND l.pdf_page=s.page
                JOIN page_text p ON p.rowid=l.fts_rowid WHERE s.source=? AND s.reference=?
                ORDER BY CASE s.kind WHEN 'article' THEN 0 ELSE 1 END,s.page,s.start''', (key,ref)).fetchall()
            if division:
                rows=[row for row in rows if row[6]==division]
            if sentence:
                matching_divisions={row[6] for row in rows if re.search(r'(?m)^\s*\(?'+re.escape(sentence)+r'\)',row[1][row[2]:row[3]])}
                rows=[row for row in rows if row[6] in matching_divisions]
            for page,text,start,end,actual,title,division,kind in rows:
                block = make_block(conn,source,page,text,start,end,actual,title,division,kind)
                has_sentence = bool(sentence and re.search(r'(?m)^\s*\(?'+re.escape(sentence)+r'\)', block['text']))
                row={'source':source.name,'path':key,'pdf_page':page,'matches':[],
                     'provisions':[block],'routing':'structural','requested_reference':ref,
                     'requested_sentence':sentence,'sentence_found_on_page':has_sentence}
                if full_page: row['text']=text
                results.append(row)
    # Number matching is not jurisdiction/applicability. Preserve alternatives with
    # their source and division labels instead of silently selecting one.
    results.sort(key=lambda r:(r['provisions'][0]['kind']!='article',
                               bool(r['requested_sentence']) and not r['sentence_found_on_page'],r['pdf_page']))
    unique=[]
    for row in results:
        existing=next((r for r in unique if (r['path'],r['pdf_page'])==(row['path'],row['pdf_page'])),None)
        if existing:
            if row['provisions'][0] not in existing['provisions']:existing['provisions']+=row['provisions']
        else:unique.append(row)
    return unique[:maximum]
