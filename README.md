# WZMH Code AI

A Codex skill for researching building-code questions from PDF source documents. It provides concise answers, precise citations and yellow-highlighted PDF excerpts in the right-side browser panel.

The skill is installed as `wzmh-code-skill` and used in a Codex chat. Python scripts handle PDF search, indexing, excerpt creation and verification locally on the user's computer.

## Included documents

| Code | Document |
| --- | --- |
| Ontario Building Code 2024 | Volume 1 |
| Ontario Building Code 2024 | Volume 2 |
| National Building Code of Canada 2020 | Part 1 |

All three original PDFs are included in [assets/source_documents](assets/source_documents). See the [library reference](references/library.md) for filenames, coverage and checksums.

## What it does

- Confirms the Code and edition at the start of a chat and names it in each answer.
- Searches PDF text using keywords, regular expressions, natural-language lexical queries and direct Article/Sentence references.
- Checks supporting provisions, conditions, exceptions and necessary cross-references before answering.
- Provides precise provision and page citations, with a Strong, Moderate or Limited confidence level.
- Highlights complete supporting sentences, or relevant table regions and footnotes, in original-quality one- or two-page excerpts.
- Opens the primary excerpt in the right-side browser panel and links each supporting PDF in the answer.
- Reuses verified excerpts and cached indexes. Original source PDFs remain unchanged.

For broad questions, it asks for the details needed to establish scope before searching. It must identify missing evidence rather than invent a requirement or citation.

## Requirements

- Codex desktop with local script execution and the right-side browser panel available.
- Local Python with PyMuPDF and SQLite FTS5. The current tested environment uses Python 3.14 and PyMuPDF 2.0.
- Access to this repository when installing from GitHub.

IT can deploy the skill and Python environment together. Installing the skill alone does not install Python or PyMuPDF. Revit and pyRevit are not required for PDF research; any Revit MCP connection is configured separately.

## Install

In a Codex chat, send:

```text
Use $skill-installer to install the skill from https://github.com/GiraffeApps/wzmh-code-codex-skill, branch master, repository root. Install it as wzmh-code-skill.
```

Once installation is complete, use it on your next turn or start a new chat. If it does not appear, restart Codex.

## Use

Start with the Code and edition, followed by your question:

```text
Use $wzmh-code-skill. Follow OBC 2024. Find Division B Article 3.6.2.1. and explain its requirements and exceptions.
```

For NBC, specify `NBC 2020`. Continue with follow-up questions in the same chat; the selected Code stays active unless you change it.

To research another PDF, give Codex its full local file path and identify the Code and edition. The file must be readable locally; a chat attachment or Sources entry without an accessible file path is not sufficient. Its index stays with that chat's outputs.

## Automatic caching and local logs

The first lookup indexes the selected source as needed. Later questions and other chats under the same Windows user reuse the index. No manual pre-indexing is required.

| Data | Default Windows location |
| --- | --- |
| Bundled-source search index | `%LOCALAPPDATA%\WZMH\CodeAI\cache\pdf_text_index.sqlite3` |
| Troubleshooting log | `%LOCALAPPDATA%\WZMH\CodeAI\logs\diagnostics.jsonl` |
| Highlighted excerpts | The chat's `outputs/references/` folder |

Logs record script-stage timings, cache status and verification outcomes without questions, search terms, quotations, full paths or PDF contents. Rotation keeps approximately 256 KiB across the current log and one backup. Timings do not measure model reasoning or PDF viewer loading.

Each chat keeps up to 200 unpinned generated excerpts. References explicitly marked to keep are exempt. Once an older excerpt is removed, its previous link will no longer open; the reference can be regenerated from the source.

## Repository contents

- [SKILL.md](SKILL.md): Codex instructions and answering safeguards.
- [scripts](scripts): Search, highlighting, cache and diagnostics helpers.
- [references](references): Source library details and supported search commands.
- [assets/source_documents](assets/source_documents): The three bundled PDFs.

## Feedback and limitations

[Report a problem](https://forms.cloud.microsoft/r/QxdFc08ETL)

The bundled documents do not establish the latest amendments or coverage beyond the listed editions and volumes. Confirm the applicable edition and project conditions before relying on a result.

*AI-assisted reference only—not a compliance determination. Verify applicable Code requirements and project conditions before use.*
