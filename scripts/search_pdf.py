"""Search bundled code PDFs by keyword or regular expression with a reusable page-text index."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Iterable
from diagnostics import diagnostic_run, stage_timer, user_data_root
from reference_cache import digest
import cache_safety

def pdf_engine():
    """Warm-cache retrieval uses only SQLite; import the PDF engine on extraction."""
    try:
        import pymupdf
    except ImportError:
        raise SystemExit("Missing PyMuPDF. Install with: python -m pip install pymupdf")
    return pymupdf


SKILL_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_ROOT = SKILL_ROOT / "assets" / "source_documents"


def default_cache_path() -> Path:
    """Per-user index location, independent of skill installation."""
    return user_data_root() / 'cache' / 'pdf_text_index.sqlite3'


CACHE_PATH = default_cache_path()


def connect(cache_path=None) -> sqlite3.Connection:
    cache_path = Path(cache_path) if cache_path is not None else CACHE_PATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cache_path, timeout=cache_safety.SQLITE_TIMEOUT)
    try:
        initialize_schema(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def initialize_schema(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            pages INTEGER NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS page_text USING fts5(
            source_path UNINDEXED,
            pdf_page UNINDEXED,
            text
        );
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS search_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS source_digests (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS page_lookup (source_path TEXT, pdf_page INTEGER, fts_rowid INTEGER, PRIMARY KEY(source_path,pdf_page))")
    if conn.execute("SELECT value FROM search_meta WHERE key='page_lookup_version'").fetchone() != ('1',):
        conn.execute("INSERT OR REPLACE INTO page_lookup SELECT source_path,CAST(pdf_page AS INTEGER),rowid FROM page_text")
        conn.execute("INSERT OR REPLACE INTO search_meta VALUES ('page_lookup_version','1')")
        conn.commit()


def bundled_sources() -> list[Path]:
    return sorted(LIBRARY_ROOT.glob("*.pdf"))


def source_routing_notice(sources, request):
    """Warn on the bundled OBC mapping volume without overriding source selection."""
    mapping_source = (LIBRARY_ROOT / '2024 OBC.Volume 2.pdf').resolve()
    if {source.resolve() for source in sources} != {mapping_source}:
        return None
    from provision_index import references
    if not (references(request) or re.search(r'\b(?:Division\s+B|Parts?\s+\d+)\b', request, re.I)):
        return None
    return {
        'selected_source': str(mapping_source),
        'requirement_source': str((LIBRARY_ROOT / '2024 OBC.Volume 1.pdf').resolve()),
        'notice': ('The selected bundled OBC Volume 2 contains explanatory material and supplementary standards. '
                   'For Division B requirement text, start with Volume 1. '
                   'SA-1 Article/Sentence rows are objective mappings, not the referenced requirement text. '
                   'Keep necessary supplementary-standard cross-references; no source was automatically changed.'),
    }


def needs_index(conn: sqlite3.Connection, source: Path, rebuild: bool) -> bool:
    if rebuild:
        return True
    stat = source.stat()
    record = conn.execute(
        "SELECT size, mtime_ns FROM sources WHERE path = ?", (str(source.resolve()),)
    ).fetchone()
    if record != (stat.st_size, stat.st_mtime_ns):
        return True
    checksum = conn.execute("SELECT sha256 FROM source_digests WHERE path=?", (str(source.resolve()),)).fetchone()
    return checksum is None or checksum[0] != digest(source)


def index_source(conn: sqlite3.Connection, source: Path) -> int:
    from provision_index import invalidate
    invalidate(conn, source)
    resolved = str(source.resolve())
    stat = source.stat()
    conn.execute("DELETE FROM page_text WHERE source_path = ?", (resolved,))
    conn.execute("DELETE FROM page_lookup WHERE source_path = ?", (resolved,))
    with pdf_engine().open(source) as document:
        rows = [
            (resolved, page_number, document[page_number - 1].get_text("text"))
            for page_number in range(1, len(document) + 1)
        ]
    conn.executemany(
        "INSERT INTO page_text(source_path, pdf_page, text) VALUES (?, ?, ?)", rows
    )
    conn.execute("INSERT OR REPLACE INTO page_lookup SELECT source_path,CAST(pdf_page AS INTEGER),rowid FROM page_text WHERE source_path=?", (resolved,))
    conn.execute(
        """
        INSERT INTO sources(path, size, mtime_ns, pages) VALUES (?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            size = excluded.size, mtime_ns = excluded.mtime_ns, pages = excluded.pages
        """,
        (resolved, stat.st_size, stat.st_mtime_ns, len(rows)),
    )
    conn.execute("INSERT OR REPLACE INTO source_digests VALUES (?,?)", (resolved, digest(source)))
    return len(rows)


def quote_fts_term(term: str) -> str:
    # Phrase quoting keeps punctuation in code references from changing FTS syntax.
    return '"' + term.replace('"', '""') + '"'


def fts_query(keywords: list[str]) -> str:
    return " AND ".join(quote_fts_term(keyword.strip()) for keyword in keywords if keyword.strip())


def excerpt(text: str, start: int, end: int, context: int) -> str:
    left = max(0, start - context)
    right = min(len(text), end + context)
    prefix = "…" if left else ""
    suffix = "…" if right < len(text) else ""
    return prefix + re.sub(r"\s+", " ", text[left:right]).strip() + suffix


def regex_matches(text: str, pattern: re.Pattern[str], context: int) -> list[dict[str, object]]:
    return [
        {
            "match": match.group(0),
            "start": match.start(),
            "end": match.end(),
            "excerpt": excerpt(text, match.start(), match.end(), context),
        }
        for match in pattern.finditer(text)
    ]


def keyword_matches(text: str, keywords: list[str], context: int) -> list[dict[str, object]]:
    # PDF extraction can change line breaks; accept arbitrary whitespace between phrase words.
    found = []
    for keyword in keywords:
        pattern = r"\s+".join(re.escape(word) for word in keyword.split())
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return []
        found.append(match)
    start = min(match.start() for match in found)
    end = max(match.end() for match in found)
    return [{"match": "; ".join(keywords), "start": start, "end": end,
             "excerpt": excerpt(text, start, end, context)}]


def iter_candidates(
    conn: sqlite3.Connection, sources: Iterable[Path], keywords: list[str], ranked=False, query_override=None, limit=None
) -> Iterable[tuple[str, int, str]]:
    allowed = {str(source.resolve()) for source in sources}
    scope = ",".join("?" for _ in allowed)
    query = query_override if query_override is not None else fts_query(keywords)
    if query:
        rows = conn.execute(
            f"SELECT source_path, pdf_page, text FROM page_text WHERE page_text MATCH ? AND source_path IN ({scope})" + (" ORDER BY bm25(page_text),rowid" if ranked else "") + (f" LIMIT {int(limit)}" if limit else ''),
            (query, *allowed),
        )
    else:
        rows = conn.execute(f"SELECT source_path, pdf_page, text FROM page_text WHERE source_path IN ({scope})", tuple(allowed))
    yield from (row for row in rows if row[0] in allowed)


STOP_WORDS = set("a an the is are was were what which how do does did can should shall must i my we our you your for of in on at to and or if it this that between containing required require need needs by itself be with under building code ontario".split())
STOP_WORDS.update('explain article sentence locate summarize summary general principal relevant give reference based requirement requirements apply applies always instead expected details changes find corresponding own wording material their about an has many'.split())
LEXICAL_ROOTS = {'separated':'separat','separation':'separat','separations':'separat',
                 'exceptions':'except','exception':'except','ratings':'rating','rated':'rat',
                 'reduced':'reduc','omitted':'omit','flashing':'visible'}
ARTICLE_HEADING = re.compile(r"(?m)^[ \t]*((?:[A-Z]-)?\d+(?:\.\d+){2,4}\.?)\s+([A-Z][^\n]{0,140})")


def natural_terms(question):
    terms = [LEXICAL_ROOTS.get(word,word[:-1] if word.endswith('s') and not word.endswith('ss') else word)
             for word in re.findall(r"[a-z]+(?:-[a-z]+)*", question.lower().replace('-', ' '))
             if word not in STOP_WORDS and len(word) > 1]
    # Lexical vocabulary bridges only; these never decide applicability or an answer.
    if 'house' in terms:
        terms.append('dwelling')
    if 'close by itself' in question.lower() or 'self closing' in question.lower():
        terms.append('self-closing')
    if re.search(r'fire[-\s]+resistance\s+rating',question,re.I):
        terms.append('separat')  # Vocabulary bridge only, not a rating/applicability rule.
    return list(dict.fromkeys(terms))


def focused_search(conn, sources, *, keywords, query, pattern, context, maximum, full_page):
    """Bound candidate work, rank whole source blocks, expose unresolved context."""
    from provision_index import ensure, blocks, objective_mapping_page
    for source in sources: ensure(conn,source)
    terms = natural_terms(query) if query is not None else [word for term in keywords for word in natural_terms(term)]
    if query is not None and not terms: return []
    override = ' OR '.join(quote_fts_term(t)+'*' for t in terms) if query is not None else None
    # Natural-language candidate generation is a lead-finding operation, not an
    # exhaustive exception search. Explicit references bypass this lexical bound.
    candidates=list(iter_candidates(conn,sources,keywords,True,override,120 if query is not None else None))
    frequencies=Counter()
    prepared=[]
    patterns={term:re.compile(r'\b'+r'[-\s]+'.join(re.escape(w) for w in term.split('-'))+r'\w*',re.I) for term in terms}
    requested_parts={n for match in re.finditer(r'\bParts?\s+(\d+(?:(?:\s*,\s*|\s+(?:and|or)\s+|\s*/\s*)(?:Part\s+)?\d+)*)',query or '',re.I) for n in re.findall(r'\d+',match.group(1))}
    for path,page,text in candidates:
        if query is not None:
            matches=[{'match':term,'start':m.start(),'end':m.end()} for term,p in patterns.items() for m in p.finditer(text)]
        else:
            matches=regex_matches(text,pattern,context) if pattern else keyword_matches(text,keywords,context)
        if not matches: continue
        frequencies.update({term for term,p in patterns.items() if p.search(text)})
        prepared.append((path,int(page),text,matches))
    weights={t:min(3.,1+math.log((1+len(prepared))/(1+frequencies[t])) ) for t in terms}
    results=[]
    for ordinal,(path,page,text,matches) in enumerate(prepared):
        spans=blocks(conn,Path(path),page,text)
        if not spans:
            mapping = objective_mapping_page(text)
            spans=[{'reference':None,'heading':None,'division':None,
                    'kind':'objective_mapping' if mapping else 'unclassified',
                    'start':0,'end':len(text),'text':text,'review_adjacent_pages':True,
                    'context_pages':[],'cross_references':[],
                    'context_notice':('Objectives/functional-statements mapping, not the text of the referenced Code requirement.'
                                      if mapping else 'No reliable article boundary; verify full page and adjacent context.')}]
        ranked=[]
        for block in spans:
            title=(block['heading'] or '')
            hits=[t for t,p in patterns.items() if p.search(block['text']) or p.search(title)]
            if query is None and not any(m['start']<block['end'] and m['end']>block['start'] for m in matches):continue
            if query is not None and not hits:continue
            score=sum(weights[t]*(1+bool(patterns[t].search(title))) for t in hits)
            score+=3*len(hits)/max(1,len(terms))+1/(1+ordinal)
            if query and re.search(r'\bhow many\b|\bnumber of\b',query,re.I) and 'Forming Part of' in block['text']:
                score+=2
            if block['kind']!='article':score*=.55
            if block['kind']=='objective_mapping':
                score*=.1
                block['ranking_caution']='Mapping rows cannot establish operative requirements; locate the actual Code provision.'
            if requested_parts and block.get('division')=='B' and block.get('reference') and block['reference'].lstrip('A-').split('.')[0] not in requested_parts:
                score*=.25
            if query and re.search(r'\battached\b',query,re.I) and not re.search(r'\bdetached\b',query,re.I) and re.search(r'\bdetached\b',title,re.I):
                score*=.3  # Keep the alternative available, but do not prefer a contrary heading.
                block['ranking_caution']='Heading concerns detached garages; query concerns attached garages.'
            if re.search(r'(?m)^\s*(?:Index|Table of Contents)\s*$',text):score*=.1
            ranked.append((score,block))
        if not ranked:continue
        ranked.sort(key=lambda item:-item[0])
        score,block=ranked[0]
        row={'source':Path(path).name,'path':path,'pdf_page':page,
             'matches':[{k:m[k] for k in ('match','start','end')} for m in matches[:8]],
             'provisions':[block],'routing':'lexical','_score':score}
        if full_page:row['text']=text
        results.append(row)
    results.sort(key=lambda r:-r['_score'])
    for row in results:del row['_score']
    return results[:maximum]


def add_context(conn, results):
    """Fetch established continuation pages in this invocation, without guesses."""
    represented={(r['path'],r['pdf_page'],b.get('reference')) for r in results for b in r.get('provisions',[])}
    fetched={}
    for row in results:
        needed={p for b in row.get('provisions',[]) for p in b.get('context_pages',[])
                if (row['path'],p,b.get('reference')) not in represented}
        selected=sorted(sorted(needed,key=lambda p:(abs(p-row['pdf_page']),p))[:2])
        row['context']=[]
        for page in selected:
            key=(row['path'],page)
            if key not in fetched:
                item=conn.execute('SELECT p.text FROM page_lookup l JOIN page_text p ON p.rowid=l.fts_rowid WHERE l.source_path=? AND l.pdf_page=?',key).fetchone()
                if item is None:raise ValueError('Required cached context page missing; refresh this source.')
                fetched[key]=item[0]
            row['context'].append({'pdf_page':page,'text':fetched[key]})
        row['unread_context_pages']=sorted(needed-set(selected))
    return results


def focused_provisions(text, matches):
    """Return intact PDF-extracted article blocks, never synthesized Code text."""
    headings = [h for h in ARTICLE_HEADING.finditer(text)
                if not re.search(r'\b(Division|Compendium)\b', h.group(2))
                and not h.group(2).rstrip().endswith('.')]
    if 'Compliance Alternative' in text:
        headings = []  # Table article references are not article headings.
    boundaries = ([0] if not headings or headings[0].start() else []) + [h.start() for h in headings] + [len(text)]
    blocks = []
    for start, end in zip(boundaries, boundaries[1:]):
        hits = [m for m in matches if m['start'] < end and m['end'] > start]
        if not hits:
            continue
        heading = next((h for h in headings if h.start() == start), None)
        distinct = len({m['match'].casefold() for m in hits})
        blocks.append({'reference': heading.group(1) if heading else None,
                       'heading': heading.group(0).strip() if heading else None,
                       'start': start, 'end': end, 'text': text[start:end],
                       'match_count': len(hits), 'distinct_matches': distinct,
                       'review_adjacent_pages': not heading or end == len(text)})
    blocks.sort(key=lambda b: (-b['distinct_matches'], -b['match_count'], b['start']))
    return blocks[:2]


def retrieve(conn, sources, *, keywords=None, regex=None, query=None, pages=None,
             reference=None, ignore_case=False, context=250, max_results=25, focused=False, full_page=False):
    if reference is not None or (query is not None and focused):
        from provision_index import references, route
        requested = references(reference if reference is not None else query)
        if reference is not None and not requested:
            raise ValueError('Reference must contain an Article number, optionally followed by a Sentence number.')
        if requested:
            division=re.search(r'\bDivision\s+([ABC])\b',reference or query,re.I)
            return route(conn,sources,requested,max_results,full_page,division.group(1).upper() if division else None)
    if pages:
        source = str(sources[0].resolve())
        count = conn.execute('SELECT pages FROM sources WHERE path=?', (source,)).fetchone()[0]
        if any(n < 1 or n > count for n in pages):
            raise ValueError('Requested page is outside the PDF.')
        rows = []
        for number in dict.fromkeys(pages):
            row = conn.execute('SELECT p.source_path,p.pdf_page,p.text FROM page_lookup l JOIN page_text p ON p.rowid=l.fts_rowid WHERE l.source_path=? AND l.pdf_page=?', (source, number)).fetchone()
            if row is None:
                raise ValueError('Cached page is missing; rebuild this source.')
            rows.append({'source': Path(row[0]).name, 'path': row[0], 'pdf_page': int(row[1]), 'text': row[2], 'matches': []})
        return rows
    if focused:
        return focused_search(conn,sources,keywords=keywords or [],query=query,
                              pattern=re.compile(regex,re.I if ignore_case else 0) if regex else None,
                              context=context,maximum=max_results,full_page=full_page)
    pattern = re.compile(regex, re.IGNORECASE if ignore_case else 0) if regex else None
    terms = natural_terms(query) if query is not None else []
    override = ' OR '.join(quote_fts_term(t) + '*' for t in terms) if terms else None
    if query is not None and not terms:
        return []
    results = []
    for source_path, page, text in iter_candidates(conn, sources, keywords or [], ranked=focused, query_override=override):
        if query is not None:
            matches = []
            for term in terms:
                for match in re.finditer(r'\b' + re.escape(term) + r'\w*', text, re.IGNORECASE):
                    matches.append({'match': term, 'start': match.start(), 'end': match.end(), 'excerpt': excerpt(text, match.start(), match.end(), context)})
        else:
            matches = regex_matches(text, pattern, context) if pattern else keyword_matches(text, keywords or [], context)
            if focused and not pattern:
                separate = [keyword_matches(text, [term], context) for term in keywords or []]
                matches = [match for found in separate for match in found] if all(separate) else []
        if not matches:
            continue
        row = {'source': Path(source_path).name, 'path': source_path, 'pdf_page': int(page), 'matches': matches}
        if full_page:
            row['text'] = text
        if focused:
            row['provisions'] = focused_provisions(text, matches)
            # Keep the response compact; all exact offsets and block text remain available.
            row['matches'] = [{k: m[k] for k in ('match', 'start', 'end')} for m in matches[:8]]
            words = natural_terms(query) if query is not None else [w.casefold() for term in keywords or [] for w in term.split()]
            row['_focus_score'] = max((10 * b['distinct_matches'] + 2 * sum(
                any(word.rstrip('s') in title_word.rstrip('s') for title_word in re.findall(r'[a-z-]+', (b['heading'] or '').casefold()))
                for word in words) for b in row['provisions']), default=0)
        results.append(row)
        if not focused and len(results) >= max_results:
            break
    if focused:
        results.sort(key=lambda r: -r['_focus_score'])  # Stable ties retain BM25 ordering.
        for row in results:
            del row['_focus_score']
    return results[:max_results]


def validated_cache_path(path, sources):
    """Cache recovery must never rename a PDF or write into bundled source assets."""
    path = Path(path)
    resolved = path.resolve()
    library = LIBRARY_ROOT.resolve()
    if path.is_symlink() or resolved.suffix.lower() == '.pdf' or resolved == library or library in resolved.parents:
        raise ValueError('Use a separate SQLite cache path outside the bundled source library, not a PDF or symbolic link.')
    for source in list(sources) + bundled_sources():
        if resolved == source.resolve() or (path.exists() and path.samefile(source)):
            raise ValueError('Search cache must be distinct from every source PDF.')
    return resolved


def run_prepared(prepared, cache_path, rebuild, diagnostic=None):
    """Run a complete request batch; failed attempts close SQLite before any recovery."""
    indexed, reused, responses = {}, [], []
    with stage_timer(diagnostic, 'cache_open'):
        conn = connect(cache_path)
    try:
        all_sources = {str(s.resolve()): s for _, sources, _ in prepared for s in sources}
        for resolved, source in all_sources.items():
            with stage_timer(diagnostic, 'source_validation'):
                stale = needs_index(conn, source, rebuild)
            if stale:
                with stage_timer(diagnostic, 'indexing'):
                    indexed[resolved] = index_source(conn, source)
            else:
                reused.append(resolved)
        with stage_timer(diagnostic, 'cache_commit'):
            conn.commit()
        for number, (job, sources, options) in enumerate(prepared):
            results = []
            if not job.get('index_only'):
                with stage_timer(diagnostic, 'retrieval'):
                    results = retrieve(conn, sources, **options)
            if job.get('with_context', False):
                with stage_timer(diagnostic, 'context_loading'):
                    add_context(conn, results)
            response = {'id': job.get('id', str(number)), 'results': results}
            source_notice = source_routing_notice(sources, options['reference'] or options['query'] or ' '.join(options['keywords'] or []))
            if source_notice is not None:
                response['source_routing_notice'] = source_notice
            if options['reference'] is not None or (options['query'] is not None and options['focused']):
                from provision_index import references
                requested = [ref for ref, _ in references(options['reference'] or options['query'])]
                if requested:
                    found = {p['reference'].rstrip('.') for r in results for p in r.get('provisions', []) if p.get('reference')}
                    response['requested_references'] = list(dict.fromkeys(requested))
                    response['unresolved_references'] = [ref for ref in dict.fromkeys(requested) if ref not in found]
            responses.append(response)
        return indexed, reused, responses
    finally:
        conn.close()


def main(diagnostic=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", type=Path, help="PDF path; repeat for multiple volumes.")
    parser.add_argument("--cache-path", type=Path,
                        help="SQLite index path for this invocation; use a chat-local path for temporary PDFs.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--keyword", action="append", help="Exact keyword/phrase; repeat for AND (legacy).")
    group.add_argument("--regex", help="Python regex over cached page text.")
    group.add_argument("--query", help="Natural-language lexical search; use --focused for ranked article blocks.")
    group.add_argument("--reference", help="Structural Article/Sentence lookup, e.g. 3.6.2.1.(6).")
    group.add_argument("--pages", type=int, nargs="+", help="Read exact one-based pages; requires one source.")
    group.add_argument("--index-only", action="store_true", help="Refresh cache without searching.")
    group.add_argument("--batch-json", help="JSON object with requests[], or a JSON array of requests.")
    group.add_argument("--batch-file", type=Path, help="UTF-8 file with the same batch JSON schema.")
    parser.add_argument("--focused", action="store_true", help="Rank by FTS relevance and return intact matching article blocks.")
    parser.add_argument("--full-page", action="store_true", help="Include complete cached page text.")
    parser.add_argument("--with-context", action="store_true", help="Include up to two established continuation/table-context pages, reporting any unread remainder.")
    parser.add_argument("--ignore-case", action="store_true")
    parser.add_argument("--context", type=int, default=250)
    parser.add_argument("--max-results", type=int, default=None, help="Default 25 legacy; 5 focused.")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.context < 0 or (args.max_results is not None and args.max_results < 1):
        parser.error("Context must be non-negative and max-results positive.")
    batch = args.batch_json is not None or args.batch_file is not None
    try:
        if batch:
            raw = json.loads(args.batch_json if args.batch_json is not None else args.batch_file.read_text(encoding="utf-8-sig"))
            jobs = raw.get("requests") if isinstance(raw, dict) else raw
            if not isinstance(jobs, list) or not jobs or len(jobs) > 32:
                raise ValueError("Batch must contain 1 to 32 requests.")
        else:
            jobs = [dict(keywords=args.keyword, regex=args.regex, query=args.query, reference=args.reference, pages=args.pages,
                         index_only=args.index_only)]
        prepared = []
        for index, job in enumerate(jobs):
            if not isinstance(job, dict):
                raise ValueError("Each batch request must be an object.")
            selected = job.get("sources", job.get("source"))
            if selected is None:
                sources = args.source or bundled_sources()
            else:
                sources = [Path(p) for p in (selected if isinstance(selected, list) else [selected])]
            if not sources or any(not s.is_file() for s in sources):
                raise ValueError("No source PDFs found, or a source file is missing.")
            keywords = job.get("keywords", job.get("keyword"))
            if isinstance(keywords, str):
                keywords = [keywords]
            if keywords is not None and (not isinstance(keywords, list) or not keywords or
                                         any(not isinstance(k, str) or not k.strip() for k in keywords)):
                raise ValueError("Keywords must be a nonempty list of nonempty strings.")
            modes = [keywords is not None, job.get("regex") is not None, job.get("query") is not None,
                     job.get("reference") is not None, job.get("pages") is not None, bool(job.get("index_only"))]
            if sum(modes) != 1:
                raise ValueError("Each request must select exactly one search/page/index mode.")
            pages = job.get("pages")
            if pages is not None and (not isinstance(pages, list) or not pages or
                                      len(sources) != 1 or any(type(n) is not int or n < 1 for n in pages)):
                raise ValueError("Pages require one source and positive integer page numbers.")
            focused = job.get("focused", args.focused)
            maximum = job.get("max_results", args.max_results)
            maximum = (5 if focused else 25) if maximum is None else maximum
            context = job.get("context", args.context)
            if type(maximum) is not int or maximum < 1 or type(context) is not int or context < 0:
                raise ValueError("Invalid maximum or context.")
            if job.get("regex") is not None:
                re.compile(job["regex"])
            if job.get("query") is not None and not isinstance(job["query"], str):
                raise ValueError("Query must be a string.")
            if job.get("reference") is not None and not isinstance(job['reference'],str):
                raise ValueError('Reference must be a string.')
            if type(job.get('with_context',args.with_context)) is not bool:
                raise ValueError('with_context must be a boolean.')
            prepared.append((job, sources, dict(keywords=keywords, regex=job.get("regex"),
                            query=job.get("query"), reference=job.get('reference'), pages=pages, focused=focused,
                            context=context, max_results=maximum,
                            ignore_case=job.get("ignore_case", args.ignore_case),
                            full_page=job.get("full_page", args.full_page))))
            # Normalize the inherited setting once so whole-batch retries are identical.
            job['with_context'] = job.get('with_context', args.with_context)
    except (ValueError, TypeError, re.error, OSError) as error:
        parser.error(str(error))
    all_sources = {str(s.resolve()): s for _, sources, _ in prepared for s in sources}
    state = {'cache_wait_ms': 0, 'cache_busy_retries': 0, 'cache_recovered': False}
    backup = None
    try:
        cache_path = validated_cache_path(args.cache_path or CACHE_PATH, all_sources.values())
        with cache_safety.cache_lock(cache_path) as wait_ms:
            state['cache_wait_ms'] = wait_ms
            while True:
                try:
                    indexed, reused, responses = run_prepared(prepared, cache_path, args.rebuild, diagnostic)
                    break
                except sqlite3.DatabaseError as error:
                    if cache_safety.is_busy(error):
                        if state['cache_busy_retries'] >= cache_safety.BUSY_RETRIES:
                            raise cache_safety.CacheBusyError(
                                'Search index stayed busy after bounded retries. Try again shortly; no index files were reset.') from error
                        state['cache_busy_retries'] += 1
                        with stage_timer(diagnostic, 'cache_retry_wait'):
                            time.sleep(0.1 * state['cache_busy_retries'])
                    elif cache_safety.is_corrupt(error) and not state['cache_recovered']:
                        with stage_timer(diagnostic, 'cache_recovery'):
                            backup = cache_safety.quarantine_cache(cache_path)
                        state['cache_recovered'] = True
                    else:
                        raise
    except cache_safety.CacheBusyError as error:
        state['cache_wait_ms'] = max(state['cache_wait_ms'], error.wait_ms)
        parser.error(str(error))
    except (cache_safety.CacheRecoveryError, OSError, ValueError, re.error) as error:
        parser.error(str(error))
    except sqlite3.DatabaseError as error:
        if diagnostic is not None:
            diagnostic['cache_sqlite_error'] = cache_safety.primary_code(error)
        message = ('Search index recovery failed after one rebuild attempt; the original damaged index was preserved.'
                   if backup else 'Search index could not be used. No automatic reset was performed for this error.')
        parser.error(message)
    finally:
        if diagnostic is not None:
            diagnostic.update(state)
    payload = {"indexed_pages": indexed, "reused_sources": reused}
    if backup is not None:
        payload['cache_recovery'] = {'recovered': True, 'preserved_index': str(backup)}
    payload.update({"responses": responses} if batch else {"results": responses[0]["results"]})
    if not batch:
        payload.update({k:v for k,v in responses[0].items() if k not in ('id','results')})
    if any(options['focused'] or options['reference'] is not None for _,_,options in prepared):
        payload['retrieval_notice']='Ranked source leads, not an exhaustive applicability or exception determination. Review context_pages, unread_context_pages, tables/footnotes and necessary cross_references.'
    if diagnostic is not None:
        diagnostic.update(mode="batch" if batch else "index" if args.index_only else "pages" if args.pages else "reference" if args.reference else "query" if args.query is not None else "regex" if args.regex else "keywords",
                          request_count=len(prepared), source_count=len(all_sources),
                          indexed_pages=sum(indexed.values()), reused_sources=len(reused),
                          result_pages=sum(len(r["results"]) for r in responses))
    if args.json or batch:
        print(json.dumps(payload, ensure_ascii=False))
        return
    if indexed:
        print("Indexed: " + ", ".join(f"{Path(p).name} ({n} pages)" for p, n in indexed.items()))
    for result in responses[0]["results"]:
        print(f"{result['source']} — PDF page {result['pdf_page']}")
        if "text" in result:
            print(result["text"])
        for provision in result.get("provisions", []):
            print(provision["text"])
        for match in result["matches"]:
            if "excerpt" in match:
                print("  " + match["excerpt"])
    if not responses[0]["results"]:
        print("No matching pages found.")


if __name__ == "__main__":
    with diagnostic_run("search") as diagnostic:
        main(diagnostic)
