"""Annotate original PDF pages while preserving their text, graphics and geometry."""
import argparse
import json
from pathlib import Path
import re
import hashlib
from diagnostics import diagnostic_run
import reference_cache

MANAGED_TAG = "obc-reference-generated-v1"


def enforce_retention(output, source, limit):
    """Prune only marked, unpinned reference PDFs in the output's exact directory."""
    directory = output.resolve().parent
    managed = []
    warnings = []
    removed = []
    try:
        indexed = reference_cache.entries(directory, pymupdf)
        candidates = [(reference_cache.safe_path(directory, name), checksum) for name, checksum in indexed]
    except Exception:
        # Fail safely to the original ownership checks if the manifest is unavailable.
        candidates = [(p, None) for p in directory.glob('*.pdf')]
    for candidate, checksum in candidates:
        if candidate is None or not candidate.is_file():
            continue
        if (candidate.is_symlink() or candidate.resolve().parent != directory
                or candidate.resolve() == source.resolve()
                or candidate.with_suffix(candidate.suffix + ".keep").exists()):
            continue
        try:
            if checksum is None or reference_cache.digest(candidate) != checksum:
                with pymupdf.open(candidate) as pdf:
                    if (pdf.metadata or {}).get("keywords") != MANAGED_TAG:
                        continue
            managed.append(candidate)
        except Exception as error:
            warnings.append(f"Could not inspect {candidate.name}: {error}")
    managed.sort(key=lambda item: (item.stat().st_mtime_ns, item.name))
    # Never delete the just-created reference, even if another file has a future timestamp.
    victims = [item for item in managed if item.resolve() != output.resolve()]
    excess = max(0, len(managed) - limit)
    for candidate in victims:
        if not excess:
            break
        try:
            candidate.unlink()
            try:
                reference_cache.forget(directory, candidate.name, pymupdf)
            except Exception:
                pass
            removed.append(candidate.name)
            excess -= 1
        except OSError as error:
            warnings.append(f"Could not remove {candidate.name}: {error}")
    return {"limit": limit, "removed": removed, "warnings": warnings,
            "remaining_unpinned": len(managed) - len(removed)}

try:
    import pymupdf
except ImportError:
    raise SystemExit("Missing PyMuPDF. Install with: python -m pip install pymupdf")


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def exact_passage(page, supplied):
    """Match literal source text, allowing only whitespace and line-end hyphens.

    No spelling, punctuation, number or ordinary in-line hyphen is corrected.
    Resolve to the actual original source slice before coordinate search.
    """
    needle=normalize(supplied)
    pieces=[]
    for index,char in enumerate(needle):
        if index and char.isalpha() and needle[index-1].isalpha():
            pieces.append(r'(?:-[ \t]*\r?\n[ \t]*)?')
        if char==' ':pieces.append(r'\s+')
        elif char=='-':pieces.append(r'-(?:[ \t]*\r?\n[ \t]*)?')
        else:pieces.append(re.escape(char))
    raw=page.get_text()
    matches=list(re.finditer(''.join(pieces),raw)) if needle else []
    if len(matches)!=1:
        raise ValueError('Expected one exact occurrence on each highlighted source page.')
    actual=normalize(matches[0].group(0))
    # Default MuPDF search dehyphenates the page while the exact source gate
    # preserves its line-end hyphen. Use the same literal representation here.
    flags=pymupdf.TEXTFLAGS_SEARCH & ~pymupdf.TEXT_DEHYPHENATE
    quads=page.search_for(actual,quads=True,flags=flags)
    if not quads:
        raise ValueError('Highlight coordinates unavailable on a source page.')
    return actual,quads


def covers_highlights(page, quads):
    yellow = [d['rect'] for d in page.get_drawings() if d.get('fill') == (1.0, 1.0, 0.0)]
    return bool(quads) and all(any((rect & quad.rect).get_area() >= quad.rect.get_area() * .90
                                  for rect in yellow) for quad in quads)


def reusable_excerpt(directory, key, document, pages, highlighted_pages, expected_highlights=None):
    """Reuse only a matching generated PDF whose text, geometry and marks still verify."""
    if not directory.exists():
        return None
    try:
        indexed = reference_cache.candidate(directory, key, pymupdf)
        candidates = [indexed] if indexed else []
    except Exception:
        candidates = directory.glob('*.pdf')
    for candidate in candidates:
        if candidate.is_symlink() or candidate.resolve().parent != directory.resolve():
            continue
        try:
            with pymupdf.open(candidate) as cached:
                if (cached.metadata.get("keywords") != MANAGED_TAG
                        or cached.metadata.get("subject") != "reference-key:" + key
                        or len(cached) != len(pages)):
                    continue
                valid = True
                for index, number in enumerate(pages):
                    saved, original = cached[index], document[number - 1]
                    valid &= (saved.get_text() == original.get_text()
                              and saved.mediabox == original.mediabox
                              and saved.cropbox == original.cropbox
                              and saved.rotation == original.rotation
                              and not list(saved.annots()))
                    if number in highlighted_pages:
                        valid &= any(d.get("fill") == (1.0, 1.0, 0.0) for d in saved.get_drawings())
                        if expected_highlights is not None:
                            valid &= covers_highlights(saved, expected_highlights[number])
                if valid:
                    return candidate.resolve()
        except Exception:
            continue
    return None


def main(diagnostic=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--page", required=True, type=int, help="One-based PDF page")
    text_source = parser.add_mutually_exclusive_group(required=True)
    text_source.add_argument("--text", help="Exact sentence to highlight")
    text_source.add_argument("--text-file", type=Path, help="UTF-8 file containing the exact sentence")
    parser.add_argument("--output", type=Path, help="Optional full highlighted PDF copy")
    parser.add_argument("--preview-output", type=Path, help="Single-page excerpt made from --output")
    parser.add_argument("--excerpt-output", type=Path,
                        help="Fast path: create only a single highlighted reference-page excerpt")
    parser.add_argument("--context-page", type=int,
                        help="Optional adjacent one-based page to include in the excerpt (maximum two pages)")
    parser.add_argument("--context-text", help="Optional exact continuation to highlight on --context-page")
    parser.add_argument("--max-pdfs", type=int, default=200,
                        help="Keep this many unpinned generated excerpts in the same folder (default: 200)")
    args = parser.parse_args()
    if args.max_pdfs < 1:
        parser.error("--max-pdfs must be positive.")
    if diagnostic is not None:
        diagnostic.update(source=args.source.name, pdf_page=args.page,
                          mode="excerpt" if args.excerpt_output else "full")
    if not args.output and not args.excerpt_output:
        parser.error("Specify --output or --excerpt-output.")
    if args.preview_output and not args.output:
        parser.error("--preview-output requires --output.")
    if args.context_text and not args.context_page:
        parser.error("--context-text requires --context-page.")
    if args.context_page and not (args.excerpt_output or args.preview_output):
        parser.error("--context-page requires an excerpt output.")
    targets = [target for target in (args.output, args.preview_output, args.excerpt_output) if target]
    if len({target.resolve() for target in targets}) != len(targets):
        parser.error("Output paths must be distinct.")
    if any(target.resolve() == args.source.resolve() or target.exists() for target in targets):
        parser.error("Outputs must be new files, distinct from the source.")
    sentence = normalize(args.text if args.text is not None
                         else args.text_file.read_text(encoding="utf-8-sig"))
    if not sentence:
        parser.error("Sentence must not be empty.")
    with pymupdf.open(args.source) as document:
        if not 1 <= args.page <= len(document):
            parser.error("Page is outside the PDF.")
        if args.context_page and (not 1 <= args.context_page <= len(document)
                                  or abs(args.context_page - args.page) != 1):
            parser.error("Context page must be an adjacent page within the PDF.")
        excerpt_pages = sorted({args.page, args.context_page} - {None})
        expected_highlights = {}
        resolved_passages = {}
        for number, passage in [(args.page, sentence)] + ([(args.context_page, normalize(args.context_text))] if args.context_text else []):
            current = document[number - 1]
            try:
                resolved_passages[number],expected_highlights[number]=exact_passage(current,passage)
            except ValueError as error:
                parser.error(str(error))
        sentence=resolved_passages[args.page]
        continuation=resolved_passages.get(args.context_page,'')
        source_stat = args.source.stat()
        reference_key = hashlib.sha256(json.dumps([
            "baked-fit-v2", str(args.source.resolve()), source_stat.st_size,
            source_stat.st_mtime_ns, args.page, sentence, args.context_page,
            continuation, reference_cache.digest(args.source)
        ], ensure_ascii=False).encode("utf-8")).hexdigest()
        if args.excerpt_output and not args.output:
            cached = reusable_excerpt(args.excerpt_output.parent, reference_key, document,
                                      excerpt_pages, set(expected_highlights), expected_highlights)
            if cached:
                display_page = excerpt_pages.index(args.page) + 1
                retention = enforce_retention(cached, args.source, args.max_pdfs)
                if diagnostic is not None:
                    diagnostic.update(reused=True, pages=len(excerpt_pages),
                                      pruned=len(retention["removed"]),
                                      retention_warnings=len(retention["warnings"]))
                print(json.dumps({"output": None, "preview": str(cached), "pdf_page": args.page,
                                  "source_pages": excerpt_pages, "verified": True, "reused": True,
                                  "browser_url": cached.as_uri() + f"#page={display_page}&view=Fit",
                                  "retention": retention}))
                return
        page = document[args.page - 1]
        quads = expected_highlights[args.page]
        annotation = page.add_highlight_annot(quads)
        annotation.set_colors(stroke=(1, 1, 0))
        annotation.set_opacity(0.35)
        annotation.set_info(title="OBC Reference", content=sentence)
        annotation.update()
        if args.context_text:
            context_page = document[args.context_page - 1]
            continuation_quads = expected_highlights[args.context_page]
            extra = context_page.add_highlight_annot(continuation_quads)
            extra.set_colors(stroke=(1, 1, 0))
            extra.set_opacity(0.35)
            extra.set_info(title="OBC Reference", content=continuation)
            extra.update()
        excerpt_pages = sorted({args.page, args.context_page} - {None})
        excerpt_originals = [
            (document[n - 1].get_text(), document[n - 1].read_contents(),
             document[n - 1].mediabox, document[n - 1].cropbox, document[n - 1].rotation)
            for n in excerpt_pages
        ]
        original_text = page.get_text()
        original_rect = page.rect
        original_mediabox = page.mediabox
        original_cropbox = page.cropbox
        original_rotation = page.rotation
        original_content = page.read_contents()
        original_count = len(document)
        # Persist the cited page for file-card opens that discard URL fragments.
        # A fixed zoom avoids requesting a Fit/Auto layout.
        if args.output:
            document.xref_set_key(document.pdf_catalog(), "OpenAction",
                                  f"[{page.xref} 0 R /XYZ null null 1]")
        if args.excerpt_output:
            args.excerpt_output.parent.mkdir(parents=True, exist_ok=True)
            with pymupdf.open() as preview:
                preview.insert_pdf(document, from_page=excerpt_pages[0] - 1, to_page=excerpt_pages[-1] - 1)
                preview.set_metadata({"title": f"OBC reference excerpt - original PDF page {args.page}",
                                      "keywords": MANAGED_TAG,
                                      "subject": "reference-key:" + reference_key})
                preview.xref_set_key(preview.pdf_catalog(), "OpenAction",
                                     f"[{preview[excerpt_pages.index(args.page)].xref} 0 R /Fit]")
                # Bake highlight appearances into vector page content to avoid
                # host preview text-layer glitches over markup annotations.
                preview.bake(annots=True, widgets=False)
                preview.save(args.excerpt_output, garbage=3, deflate=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            document.save(args.output, garbage=3, deflate=True)
    if args.output:
        with pymupdf.open(args.output) as result:
            marked_page = result[args.page - 1]
            assert len(result) == original_count, "Page count changed"
            assert marked_page.get_text() == original_text, "Page text changed"
            assert marked_page.read_contents() == original_content, "Original page graphics changed"
            assert marked_page.mediabox == original_mediabox, "Page MediaBox changed"
            assert marked_page.cropbox == original_cropbox, "Page CropBox changed"
            assert marked_page.rotation == original_rotation, "Page rotation changed"
            kind, destination = result.xref_get_key(result.pdf_catalog(), "OpenAction")
            assert kind == "array" and destination.startswith(f"[{marked_page.xref} 0 R"), "Opening page incorrect"
            assert any(a.info.get("content") == sentence and a.type[0] == 8
                       for a in marked_page.annots()), "Highlight missing"
            if args.preview_output:
                args.preview_output.parent.mkdir(parents=True, exist_ok=True)
                with pymupdf.open() as preview:
                    preview.insert_pdf(result, from_page=excerpt_pages[0] - 1, to_page=excerpt_pages[-1] - 1)
                    preview.set_metadata({"title": f"OBC reference excerpt - original PDF page {args.page}",
                                          "subject": "Highlighted reference excerpt; full context is in the companion volume."})
                    preview.save(args.preview_output, garbage=3, deflate=True)
    preview_path = args.excerpt_output or args.preview_output
    if preview_path:
        with pymupdf.open(preview_path) as preview:
            assert len(preview) == len(excerpt_pages), "Excerpt page count changed"
            for index, expected in enumerate(excerpt_originals):
                current = preview[index]
                assert (current.get_text(), current.mediabox, current.cropbox, current.rotation) == (
                    expected[0], expected[2], expected[3], expected[4]), "Excerpt text or geometry changed"
                if not args.excerpt_output:
                    assert current.read_contents() == expected[1], "Page graphics changed"
            preview_page = preview[excerpt_pages.index(args.page)]
            assert preview_page.get_text() == original_text
            assert preview_page.rect == original_rect, "Preview page dimensions changed"
            assert preview_page.mediabox == original_mediabox, "Preview MediaBox changed"
            assert preview_page.cropbox == original_cropbox, "Preview CropBox changed"
            assert preview_page.rotation == original_rotation, "Preview rotation changed"
            if args.excerpt_output:
                assert not any(a.type[0] == 8 for a in preview_page.annots()), "Highlight layer was not baked"
                assert any(d.get("fill") == (1.0, 1.0, 0.0) for d in preview_page.get_drawings()), "Baked yellow highlight missing"
                for number, expected in expected_highlights.items():
                    assert covers_highlights(preview[excerpt_pages.index(number)], expected), "Complete highlight coverage missing"
            else:
                assert any(a.type[0] == 8 and a.info.get("content") == sentence
                           for a in preview_page.annots()), "Preview highlight missing"
            if args.context_text and not args.excerpt_output:
                assert any(a.type[0] == 8 and a.info.get("content") == continuation
                           for a in preview[excerpt_pages.index(args.context_page)].annots()), "Continuation highlight missing"
    display_path = preview_path or args.output
    display_page = excerpt_pages.index(args.page) + 1 if preview_path else args.page
    cache_warning = None
    if args.excerpt_output:
        try:
            reference_cache.register(args.excerpt_output, reference_key, pymupdf)
        except Exception as error:
            cache_warning = type(error).__name__
    retention = enforce_retention(args.excerpt_output, args.source, args.max_pdfs) if args.excerpt_output else None
    if diagnostic is not None:
        diagnostic.update(reused=False, pages=len(excerpt_pages) if preview_path else original_count,
                          verified=True, pruned=len(retention["removed"]) if retention else 0,
                          retention_warnings=len(retention["warnings"]) if retention else 0)
    print(json.dumps({"output": str(args.output.resolve()) if args.output else None, "pdf_page": args.page,
                      "browser_url": display_path.resolve().as_uri() + f"#page={display_page}&view=Fit",
                      "source_pages": excerpt_pages if preview_path else None,
                      "highlight_quads": len(quads), "opening_page_verified": bool(args.output),
                      "preview": str(preview_path.resolve()) if preview_path else None,
                      "retention": retention,
                      "cache_warning": cache_warning,
                      "reused": False,
                      "verified": True}))


if __name__ == "__main__":
    with diagnostic_run("highlight") as diagnostic:
        main(diagnostic)
