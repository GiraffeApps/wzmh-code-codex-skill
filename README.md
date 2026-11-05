# WZMH Code AI

A Codex skill for researching the bundled Ontario Building Code 2024 and National Building Code 2020 PDFs, citing exact provisions, and displaying yellow-highlighted PDF excerpts. The Python search and highlight helpers run on an HTTP server; Codex uses a small client to send requests and save verified excerpts beside the chat.

## Bundled sources

| Source ID | PDF |
| --- | --- |
| `obc-2024-v1` | Ontario Building Code 2024, Volume 1 |
| `obc-2024-v2` | Ontario Building Code 2024, Volume 2 |
| `nbc-2020-p1` | National Building Code of Canada 2020, Part 1 |

The original PDFs are in `assets/source_documents/`. See [references/library.md](references/library.md) for coverage and checksums.

## Run a local test server

Use a Python environment with PyMuPDF and SQLite FTS5. From the repository root:

```bash
python scripts/code_server.py --host 127.0.0.1 --port 8765
```

The server stays on loopback for local testing. In another shell:

```bash
export WZMH_CODE_SERVER_URL=http://127.0.0.1:8765
python scripts/code_client.py health
python scripts/code_client.py search --source-id obc-2024-v1 --reference '3.6.2.1.(6)' --max-results 3 --with-context
python scripts/code_client.py highlight --source-id obc-2024-v1 --page 311 --text 'Exact supporting source sentence.' --excerpt-output outputs/references/example.pdf
```

The highlight command verifies the excerpt on the server, checks its transfer checksum, and saves it at the requested local path. The result's `preview` path is the one to open in Codex and link in the answer. Choose a new filename for each excerpt. To use a chat-supplied PDF, run `python scripts/code_client.py upload /path/to/file.pdf` and pass the returned `source_id` to search and highlight commands. Upload IDs expire when the server restarts.

## Later server deployment

For a tested Ubuntu 22.04 setup over SSH, follow [DEPLOY_UBUNTU_22_04.md](DEPLOY_UBUNTU_22_04.md). It includes the current deployment archive, a systemd service, an SSH tunnel, and an end-to-end skill test.

Set `WZMH_CODE_SERVER_URL` to the provided server address, such as `http://<server-ip>:8765`. The server refuses a non-loopback bind without `WZMH_CODE_SERVER_TOKEN`; set the same token in the client environment. Place the server on a trusted network or behind an HTTPS proxy because plain HTTP does not encrypt requests, tokens, or PDFs. The server must have the skill repository, original PDFs, and a Python environment with PyMuPDF and SQLite FTS5. The client only needs Python's standard library.

The server stores uploaded PDFs in its data directory until an operator removes them. Its generated excerpt file is temporary; downloaded chat excerpts stay in `outputs/references/` until the user removes them. The HTTP client does not currently enforce the earlier local 200-PDF retention policy.

## Install and use the skill

Install this repository root as `wzmh-code-skill`, then explicitly select the skill or invoke `$wzmh-code-skill` in a Code research chat. Configure `WZMH_CODE_SERVER_URL` in the client environment first. The skill confirms the Code and edition, searches the selected PDF, checks provisions and exceptions, and returns concise citations with highlighted excerpts. It is not automatically activated for unrelated requests.

## Verify

```bash
python -m unittest discover -s tests
```

Run the test suite with a Python environment containing PyMuPDF and modern SQLite error-code support. The HTTP integration test binds a loopback port. The skill's source PDFs and citation checks remain the evidence boundary; search hits alone do not establish applicability.
