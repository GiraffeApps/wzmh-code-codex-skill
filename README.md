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

### First use

Before its first Code lookup, WZMH Code AI checks that setup is ready. If Python, PyMuPDF or another required component is unavailable, it explains what failed and asks the user to have IT install or configure it before continuing. PyMuPDF is a Python package; no separate Adobe/browser PDF plug-in is required. Python found at another location produces a path-configuration fix, not a request to reinstall it.

After a successful check, the skill saves `%LOCALAPPDATA%\WZMH\CodeAI\setup\ready.json`. The same Windows user can then use it across chats without another routine installation question or full setup check. A new chat reads this small record once; ordinary follow-ups do not repeat the guard. Successful readiness checks are silent in normal Code answers. IT can seed the record by running the full health check in the actual user's session during deployment.

The record contains setup/file identities, not conversation instructions or project data. Python/dependency/source-package changes invalidate it for a fresh check; an actual failure is reported with the specific fix. Working software is not repeatedly requested for installation. Normal PDF retrieval, source verification and highlight checks still run as required.

## Installation health check

After deployment, IT can type this in Codex:

> Use $wzmh-code-skill to run the WZMH Code AI installation health check for this user. Report failures and the required fixes. Do not install packages or change configuration.

Or run this in PowerShell for a standard installation:

```powershell
$CodeHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
& (Join-Path $CodeHome 'skills/wzmh-code-skill/scripts/health_check.cmd')
```

Run in the user's Windows session. For a custom deployment, use the actual skill folder. The native Windows launcher does not require `.ps1` execution permission. It uses `WZMH_CODE_PYTHON` when configured, then the same managed/per-user Python locations as the skill. Add `--json` for machine-readable results. Normal invocation forces a fresh full check; `--ensure-ready --json` performs the first-use guard and reuses valid setup confirmation. Exit code `0` means setup is ready; `1` means a failure or incomplete check.

If that Python path is missing or invalid, the setup checker looks in PATH, Windows Python registrations, common installation folders and the Python launcher's installed-runtime list. It verifies detected executables and checks PyMuPDF/SQLite in a bounded diagnostic pass. A verified alternative is reported as **Python installed, but the skill points to the wrong location**, with the actual path and the value IT should configure for `WZMH_CODE_PYTHON`. If dependencies are missing, those failures are listed separately. If nothing can be verified, it reports that discovery was inconclusive rather than claiming Python is not installed. Zero-length Store aliases are skipped. Discovery does not scan entire drives, install anything, change environment variables permanently or switch the skill's interpreter. Configure the verified path through IT, restart Codex and rerun all eight checks. Discovery is limited to setup failures; verified ordinary lookups do not run it.

The full check runs during first-use setup or requested troubleshooting. It opens the PDFs and checks their SHA-256 values without modifying them or building indexes. It creates missing user-local folders, tests cache/log access with temporary files, removes its probes and saves setup confirmation only after success. It does not install packages, change settings, test Revit connectivity or measure the PDF viewer. A valid cached guard does not re-import PyMuPDF or rerun those full checks.

### Expected result

After IT deploys the updated skill and dependencies, a user who runs the health-check prompt should receive a pass report when all eight checks succeed. A typical status summary is:

```text
Installation health check passed: 8/8 checks successful.
- Python and PyMuPDF available
- SQLite FTS5 working
- All bundled OBC 2024 and NBC 2020 PDFs verified by SHA-256 and opened successfully
- Local cache and log-folder access working
No failures or fixes required. No packages or configuration were changed.
```

Codex wording and reported version numbers may vary. If a check fails or cannot complete, the report lists the issue and required fix instead of declaring success. The full status report appears when explicitly requested; ordinary first use reports only a setup problem and otherwise continues silently. It does not appear at Codex startup or before every question. A pass confirms installation readiness; it does not validate Code-answer accuracy, rendered highlights or browser-panel behavior.

## Install

In a Codex chat, send:

```text
Use $skill-installer to install the skill from https://github.com/GiraffeApps/wzmh-code-codex-skill, branch master, repository root. Install it as wzmh-code-skill.
```

Once installation is complete, use it on your next turn or start a new chat. If it does not appear, restart Codex.

## Use

WZMH Code AI activates only when selected or explicitly invoked with `$wzmh-code-skill`. It is not selected automatically just because a question mentions building codes. Related follow-ups can continue in that chat without repeating the invocation.

Its confidence labels, disclaimer, report link and PDF panel apply only to its active workflow. Other chats do not inherit the selected Code or response format. Unrelated requests in the same chat, including editing or discussing this skill, use normal responses. IT should deploy this skill folder as a skill, not paste its instructions into global settings, `AGENTS.md` or organization-wide chat instructions.

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
| Verified first-use setup | `%LOCALAPPDATA%\WZMH\CodeAI\setup\ready.json` |
| Highlighted excerpts | The chat's `outputs/references/` folder |

Logs record script-stage timings, cache status and verification outcomes without questions, search terms, quotations, full paths or PDF contents. Rotation keeps approximately 256 KiB across the current log and one backup. Timings do not measure model reasoning or PDF viewer loading.

Each chat keeps up to 200 unpinned generated excerpts. References explicitly marked to keep are exempt. Once an older excerpt is removed, its previous link will no longer open; the reference can be regenerated from the source.

## Repository contents

- [SKILL.md](SKILL.md): Codex instructions and answering safeguards.
- [scripts](scripts): Search, highlighting, setup checks, cache and diagnostics helpers.
- [references](references): Source library details and supported search commands.
- [assets/source_documents](assets/source_documents): The three bundled PDFs.

## Feedback and limitations

[Report a problem](https://forms.cloud.microsoft/r/QxdFc08ETL)

The bundled documents do not establish the latest amendments or coverage beyond the listed editions and volumes. Confirm the applicable edition and project conditions before relying on a result.

*AI-assisted reference only—not a compliance determination. Verify applicable Code requirements and project conditions before use.*
