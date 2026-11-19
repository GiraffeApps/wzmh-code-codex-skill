"""Small HTTP host for the existing PDF search and excerpt helpers."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SOURCES = {
    "obc-2024-v1": ROOT / "assets/source_documents/2024 OBC.Volume 1.pdf",
    "obc-2024-v2": ROOT / "assets/source_documents/2024 OBC.Volume 2.pdf",
    "nbc-2020-p1": ROOT / "assets/source_documents/NBC 2020 Part 1.pdf",
}
SEARCH_VALUE = {"--keyword", "--regex", "--query", "--reference", "--batch-json",
                "--context", "--max-results"}
SEARCH_FLAG = {"--focused", "--full-page", "--with-context", "--ignore-case"}
HIGHLIGHT_VALUE = {"--page", "--text", "--table-text", "--context-page", "--context-text",
                   "--context-table-text", "--max-pdfs"}


def checked_args(args, values, flags):
    if not isinstance(args, list) or len(args) > 100 or any(not isinstance(x, str) for x in args):
        raise ValueError("Invalid argument list")
    result = []
    i = 0
    while i < len(args):
        flag = args[i]
        if flag in flags:
            result.append(flag)
            i += 1
        elif flag in values and i + 1 < len(args):
            result.extend(args[i:i + 2])
            i += 2
        elif flag == "--pages" and values is SEARCH_VALUE:
            result.append(flag)
            i += 1
            while i < len(args) and args[i].isdigit():
                result.append(args[i])
                i += 1
        else:
            raise ValueError("Unsupported or incomplete argument: " + flag)
    return result


def create_server(host="127.0.0.1", port=8765, data_dir=None, token=None):
    if host not in ("127.0.0.1", "localhost", "::1") and not token:
        raise ValueError("A bearer token is required when listening beyond loopback")
    data_dir = Path(data_dir or os.environ.get("WZMH_CODE_SERVER_DATA") or
                    Path(tempfile.gettempdir()) / "wzmh-code-server").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    uploads = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path not in ("/health", "/health/full"):
                return self.reply(404, {"error": "Unknown endpoint"})
            if token and self.headers.get("Authorization") != "Bearer " + token:
                return self.reply(401, {"error": "Unauthorized"})
            if self.path == "/health/full":
                result = subprocess.run([sys.executable, str(SCRIPTS / "health_check.py"), "--json"],
                                        capture_output=True, text=True, timeout=180)
                try:
                    report = json.loads(result.stdout)
                except json.JSONDecodeError:
                    return self.reply(500, {"error": result.stderr.strip()[-600:] or "Health check failed"})
                return self.reply(200 if result.returncode == 0 else 503, report)
            self.reply(200, {"status": "ok", "sources": list(SOURCES)})

        def do_POST(self):
            if token and self.headers.get("Authorization") != "Bearer " + token:
                return self.reply(401, {"error": "Unauthorized"})
            if self.path not in ("/upload", "/search", "/highlight"):
                return self.reply(404, {"error": "Unknown endpoint"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 200 * 1024 * 1024:
                    raise ValueError("Request size must be between 1 byte and 200 MiB")
                body = self.rfile.read(length)
                if self.path == "/upload":
                    if not body.startswith(b"%PDF-"):
                        raise ValueError("Upload must be a PDF")
                    source_id = "upload-" + uuid.uuid4().hex
                    path = data_dir / (source_id + ".pdf")
                    path.write_bytes(body)
                    uploads[source_id] = path
                    return self.reply(200, {"source_id": source_id})
                request = json.loads(body)
                source_id = request["source_id"]
                source = SOURCES.get(source_id) or uploads.get(source_id)
                if source is None or not source.is_file():
                    raise ValueError("Unknown source_id")
                args = request.get("args", [])
                if self.path == "/search":
                    args = checked_args(args, SEARCH_VALUE, SEARCH_FLAG)
                    if "--batch-json" in args:
                        batch = json.loads(args[args.index("--batch-json") + 1])
                        jobs = batch.get("requests", []) if isinstance(batch, dict) else batch
                        if not isinstance(jobs, list) or any(not isinstance(j, dict) or
                                "source" in j or "sources" in j for j in jobs):
                            raise ValueError("Batch sources must use the selected source_id")
                    command = [sys.executable, str(SCRIPTS / "search_pdf.py"), "--source", str(source),
                               "--json", *args]
                    if source_id.startswith("upload-"):
                        command.extend(["--cache-path", str(data_dir / (source_id + ".sqlite3"))])
                    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
                    if result.returncode:
                        raise ValueError(result.stderr.strip()[-600:] or "Search failed")
                    return self.reply(200, json.loads(result.stdout))
                args = checked_args(args, HIGHLIGHT_VALUE, set())
                with tempfile.TemporaryDirectory(dir=data_dir) as tmp:
                    excerpt = Path(tmp) / "excerpt.pdf"
                    command = [sys.executable, str(SCRIPTS / "highlight_sentence.py"), str(source),
                               *args, "--excerpt-output", str(excerpt)]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
                    if result.returncode:
                        raise ValueError(result.stderr.strip()[-600:] or "Highlight failed")
                    meta = json.loads(result.stdout)
                    if not meta.get("verified") or not excerpt.is_file():
                        raise ValueError("Excerpt verification failed")
                    pdf = excerpt.read_bytes()
                    return self.reply(200, {"metadata": meta,
                                            "sha256": hashlib.sha256(pdf).hexdigest(),
                                            "pdf_base64": base64.b64encode(pdf).decode("ascii")})
            except (ValueError, KeyError, json.JSONDecodeError) as error:
                self.reply(400, {"error": str(error)})
            except subprocess.TimeoutExpired:
                self.reply(504, {"error": "Helper timed out"})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.data_dir,
                           os.environ.get("WZMH_CODE_SERVER_TOKEN"))
    print(f"WZMH Code server listening on http://{args.host}:{server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
