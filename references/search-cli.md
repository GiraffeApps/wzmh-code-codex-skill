# Focused and batched PDF retrieval

All results come from the local PDF text cache. Existing commands retain their meanings and result fields.

```text
python scripts/search_pdf.py --source "path/to/code.pdf" --keyword "public corridor" --keyword "minimum width" --focused --max-results 5 --json
python scripts/search_pdf.py --source "path/to/code.pdf" --pages 311 312 --json
python scripts/search_pdf.py --source "path/to/code.pdf" --query "Does an emergency generator room need a fire separation?" --focused --max-results 3 --with-context --json
python scripts/search_pdf.py --source "path/to/code.pdf" --reference "Division B 3.6.2.1.(6)" --with-context --json
```

For a PDF available only in the current chat, use its readable local path and a chat-local index, for example `python scripts/search_pdf.py --source "path/to/chat.pdf" --cache-path "outputs/references/pdf-text-index.sqlite3" --query "question" --focused --json`. The `--cache-path` option changes only that invocation's SQLite index location. A chat Sources entry without a local path is not directly usable by the script. The highlighter accepts the same PDF path; for tables it accepts repeatable `--table-text` and, on an adjacent page, repeatable `--context-table-text` selections.

`--focused` ranks whole source blocks using lexical coverage, heading relevance, FTS candidate order and structural provenance. Natural-language FTS candidates are limited to 120 pages; exact reference routing is not subject to that bound. It returns one most-relevant `provisions` block per result page, unchanged PDF text with `start`/`end` offsets, source Division when identified, continuation `context_pages`, and `cross_references`. Missing/unrecognized headings are `null`, not invented citations. Ranking and metadata do not establish applicability or exhaustive coverage. Inspect complete conditions and necessary cross-references; `--full-page` retains complete page text.

`--reference` finds structurally established Article/Sentence locations, not pages merely mentioning a number. `--query ... --focused` automatically takes the same route for explicit numbers. A requested Sentence prioritizes its containing page and excludes Divisions where that Sentence was not found. An explicit `Division A/B/C` narrows the route. `requested_references` and `unresolved_references` expose missing or result-cap-omitted references. If parsing cannot establish a heading, use a targeted regex and full-page verification rather than treating absence as a legal conclusion.

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

## Regression checks

```text
python -m unittest discover -s tests -v
```

Tests create isolated fixture PDFs, indexes and excerpts. They do not edit the bundled library or depend on external services. Tests cover lazy imports, legacy commands, batch equivalence, ranked PDF text, source changes, excerpt quality, exact highlight coverage, indexed reuse, tampering/missing outputs, manifest failure, two-page continuation and retention.
