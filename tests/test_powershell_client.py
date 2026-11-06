"""Exercise the no-Python PowerShell client against the HTTP protocol."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "scripts" / "code_client.ps1"
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


class Handler(BaseHTTPRequestHandler):
    requests = []
    corrupt = False
    unrelated = False

    def log_message(self, *args):
        pass

    def reply(self, body):
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.unrelated:
            body = b"<html>RFA Tool</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.reply({"status": "ok", "sources": ["obc-2024-v1"]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append((self.path, payload))
        if self.path == "/search":
            self.reply({"results": [{"page": 1, "text": "test sentence"}]})
        else:
            checksum = hashlib.sha256(PDF).hexdigest()
            if self.corrupt:
                checksum = "0" * 64
            self.reply({"metadata": {"verified": True, "browser_url": "file:///server/excerpt.pdf#page=1"},
                        "sha256": checksum, "pdf_base64": base64.b64encode(PDF).decode("ascii")})


class PowerShellClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("pwsh"):
            raise unittest.SkipTest("PowerShell 7 is unavailable")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.work = Path.cwd() / "work"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def client(self, *args, success=True, executable="pwsh"):
        env = os.environ.copy()
        env["WZMH_CODE_SERVER_URL"] = f"http://127.0.0.1:{self.server.server_port}"
        env.pop("WZMH_CODE_SERVER_TOKEN", None)
        # The client process cannot find Python through PATH.
        shell = shutil.which(executable)
        env["PATH"] = str(Path(shell).parent)
        command = [shell, "-NoProfile"]
        if executable == "powershell.exe":
            command.extend(["-ExecutionPolicy", "Bypass"])
        command.extend(["-File", str(CLIENT), *args])
        result = subprocess.run(command,
                                capture_output=True, text=True, env=env, timeout=15)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result

    def test_health_search_and_highlight_without_python(self):
        self.assertEqual(self.client("health")["status"], "ok")
        search = self.client("search", "--source-id", "obc-2024-v1", "--query", "corridor", "--focused")
        self.assertEqual(search["results"][0]["page"], 1)
        self.assertIn(("/search", {"source_id": "obc-2024-v1", "args": ["--query", "corridor", "--focused"]}), Handler.requests)
        output = self.work / ("excerpt-" + uuid.uuid4().hex + ".pdf")
        result = self.client("highlight", "--source-id", "obc-2024-v1", "--page", "1", "--text", "test sentence", "--excerpt-output", str(output))
        self.assertTrue(result["verified"])
        self.assertEqual(result["preview"], str(output))
        self.assertEqual(output.read_bytes(), PDF)

    def test_rejects_wrong_checksum_without_writing_pdf(self):
        Handler.corrupt = True
        try:
            output = self.work / ("corrupt-" + uuid.uuid4().hex + ".pdf")
            result = self.client("highlight", "--source-id", "obc-2024-v1", "--page", "1", "--text", "test sentence", "--excerpt-output", str(output), success=False)
            self.assertIn("checksum", result.stderr.lower())
            self.assertFalse(output.exists())
        finally:
            Handler.corrupt = False

    def test_health_rejects_unrelated_html_site(self):
        Handler.unrelated = True
        try:
            result = self.client("health", success=False)
            self.assertIn("health", result.stderr.lower())
        finally:
            Handler.unrelated = False

    def test_windows_powershell_can_search_and_save_excerpt(self):
        if not shutil.which("powershell.exe"):
            self.skipTest("Windows PowerShell is unavailable")
        search = self.client("search", "--source-id", "obc-2024-v1", "--query", "corridor",
                             executable="powershell.exe")
        self.assertTrue(search["results"])
        output = self.work / ("windows-" + uuid.uuid4().hex + ".pdf")
        result = self.client("highlight", "--source-id", "obc-2024-v1", "--page", "1",
                             "--text", "test sentence", "--excerpt-output", str(output),
                             executable="powershell.exe")
        self.assertTrue(result["verified"])
        self.assertEqual(output.read_bytes(), PDF)


if __name__ == "__main__":
    unittest.main()
