# Focused and batched PDF retrieval

All results come from the local PDF text cache. The Windows default is `%LOCALAPPDATA%/WZMH/CodeAI/cache/pdf_text_index.sqlite3`, shared across the same user's chats, not stored in the installed skill. A normal search automatically creates a missing index or refreshes a changed source; no manual indexing step is required. `--cache-path` still selects a separate index for that invocation, including chat-local PDFs. `--index-only` remains available for explicitly requested maintenance. Existing commands retain their meanings and result fields.

```text
python scripts/search_pdf.py --source "path/to/code.pdf" --keyword "public corridor" --keyword "minimum width" --focused --max-results 5 --json
python scripts/search_pdf.py --source "path/to/code.pdf" --pages 311 312 --json
python scripts/search_pdf.py --source "path/to/code.pdf" --query "Does an emergency generator room need a fire separation?" --focused --max-results 3 --with-context --json
python scripts/search_pdf.py --source "path/to/code.pdf" --reference "Division B 3.6.2.1.(6)" --with-context --json
```

For a PDF available only in the current chat, use its readable local path and a chat-local index, for example `python scripts/search_pdf.py --source "path/to/chat.pdf" --cache-path "outputs/references/pdf-text-index.sqlite3" --query "question" --focused --json`. The `--cache-path` option changes only that invocation's SQLite index location. A chat Sources entry without a local path is not directly usable by the script. The highlighter accepts the same PDF path; for tables it accepts repeatable `--table-text` and, on an adjacent page, repeatable `--context-table-text` selections.

`--focused` ranks whole source blocks using lexical coverage, heading relevance, FTS candidate order and structural provenance. Natural-language FTS candidates are limited to 120 pages; exact reference routing is not subject to that bound. It returns one most-relevant `provisions` block per result page, unchanged PDF text with `start`/`end` offsets, source Division when identified, continuation `context_pages`, and `cross_references`. Missing/unrecognized headings are `null`, not invented citations. Ranking and metadata do not establish applicability or exhaustive coverage. Inspect complete conditions and necessary cross-references; `--full-page` retains complete page text.

`--reference` finds structurally established Article/Sentence locations, not pages merely mentioning a number. `--query ... --focused` automatically takes the same route for explicit numbers. A requested Sentence prioritizes its containing page and excludes Divisions where that Sentence was not found. An explicit `Division A/B/C` narrows the route. `requested_references` and `unresolved_references` expose missing or result-cap-omitted references. SA-1 objectives mappings do not establish the Articles they reference; both orders of the objectives/functional-statements heading are recognized. Focused fallback results may be labeled `objective_mapping` with a caution; legacy keyword/regex/page access remains available. A lookup scoped only to the bundled OBC Volume 2 for a Part/Article can return `source_routing_notice` identifying Volume 1 as the requirement source. This is a notice, not an automatic source switch, and is not applied to attached or NBC PDFs. If parsing cannot establish a heading, use a targeted regex and full-page verification rather than treating absence as a legal conclusion.

`--with-context` adds up to two established continuation/table-governing pages per result, skipping already-represented article pages. `unread_context_pages` reports any remaining pages; required conditions must still be reviewed. Whole blocks are not sentence-fragment snippets. Separate nonconsecutive references still need separate PDFs.

Legacy commands default to 25 matching pages and preserve their original page order. Focused commands default to five ranked pages. `--max-results` still overrides the default. Keyword phrases retain exact AND matching. `--query` is an explicit lexical alternative, not semantic search, and may return weak matches that need review.

## Batch schema

Pass the following object as a single `--batch-json` argument, or read it from an existing UTF-8 file using `--batch-file`. Do not create a temporary Python script to build a batch. The top-level `--source` supplies the default library; a request may specify `source` or `sources` to override it.

```json
{
  "requests": [
    {"id": "corridor", "keywords": ["public corridor", "minimum width"], "focused": true, "max_results": 5},
    {"id": "room", "reference": "3.6.2.1.(6)", "with_context": true},
    {"id": "doors", "regex": "3\\.3\\.1\\.10\\.", "full_page": true, "max_results": 3},
    {"id": "context", "pages": [311, 312]}
  ]
}
```

Each request selects exactly one of `keywords`, `regex`, `query`, `reference`, `pages`, or `index_only`. Optional settings: `focused`, `full_page`, `with_context`, `max_results`, `context`, `ignore_case`. Batches contain 1–32 requests. Invalid requests cause a nonzero exit; they are not silently omitted. Existing keyword AND, regex, page, index, full-page and batch commands remain supported; legacy unfocused defaults and page order are unchanged. The focused CLI default stays five; the skill's normal initial request explicitly uses three.

Output retains `indexed_pages` and `reused_sources`, followed by `responses: [{id, results}]`. Results retain exact `path` and one-based `pdf_page`. Multiple excerpts still remain separate one- or two-page PDFs; open only the primary reference automatically.

## Search-index contention and recovery

CLI operations sharing one cache use an OS lock on `<cache>.lock`. The guard waits at most five seconds; a stale empty lock file is harmless because the operating system releases locks when a process exits. Separate cache paths do not share this guard. Reads are coordinated as well as writes so recovery cannot move a database being read by another cooperating lookup. SQLite operations use a 250 ms busy timeout, with at most two whole-request retries and 100/200 ms backoff. These bounds apply to waiting, not to PDF extraction or the complete lookup duration. A timed-out lookup exits nonzero without a partial answer or a reset.

Only SQLite `CORRUPT`/`NOTADB` error codes trigger recovery. After closing the failed connection, the helper preserves the main database and any remaining WAL/SHM/journal sidecars beside it as `<cache>.corrupt-<unique-id>.bak*`, then attempts one fresh lookup, indexing only the selected sources as needed. An unsuccessful preservation is rolled back where possible and stops recovery. Backups are never overwritten or automatically deleted. Successful JSON/batch output adds `cache_recovery: {recovered: true, preserved_index: <backup path>}`; ordinary output fields and command syntax remain unchanged. Source hashes and all provision/citation checks still apply after rebuilding. Original PDFs, saved excerpts and their manifest are not reset.

Busy/locked errors, permission errors, disk-full errors and unclassified schema errors are not treated as corruption. Keep their nonzero exit as a lookup failure; do not answer using stale or partial results. Cache paths that alias a selected/bundled PDF, use a `.pdf` extension or lie inside the bundled source library are rejected. Recovery metadata in the local diagnostics records guard wait time, busy retry count and recovery status without recording backup paths or PDF text. Inspect preserved backups only when troubleshooting; do not delete them automatically.

Each CLI run also records `stages_ms` in the existing per-user diagnostics log: `cache_open` (connection/schema), `source_validation` (freshness/source checks), `indexing` (PDF engine/extraction/index writes), `cache_commit`, `retrieval` (including derived structural metadata/ranking) and `context_loading` (the extra `--with-context` reads). Recovery/backoff add `cache_recovery` and `cache_retry_wait` when entered. Values are cumulative milliseconds across batch requests and retries, including failed stage attempts. A warm search has no `indexing` stage unless a selected source needs refresh. Stage times cover selected work and do not sum to the whole process or measure chatbot/viewer latency. They are absent from normal search result JSON. No questions, search terms, PDF text or paths are added by the timing fields.

## Regression checks

```text
python -m unittest discover -s tests -v
```

Tests create isolated fixture PDFs, indexes and excerpts. They do not edit the bundled library or depend on external services. Tests cover lazy imports, legacy commands, batch equivalence, ranked PDF text, source changes, excerpt quality, exact highlight coverage, indexed reuse, tampering/missing outputs, manifest failure, two-page continuation and retention.
