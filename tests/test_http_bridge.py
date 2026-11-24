"""Exercise the HTTP boundary with a real PDF and the existing helpers."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class BridgeTest(unittest.TestCase):
    def test_uploaded_pdf_can_be_searched_and_highlighted(self):
        from code_server import create_server

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.pdf"
            output = Path(tmp) / "excerpt.pdf"
            sentence = "The minimum width of a public corridor shall be 1 100 mm."
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                page.insert_text((72, 72), sentence)
                pdf.save(source)
            server = create_server("127.0.0.1", 0, data_dir=Path(tmp) / "server")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = {"WZMH_CODE_SERVER_URL": f"http://127.0.0.1:{server.server_port}"}

                def client(*args):
                    return subprocess.run([sys.executable, str(ROOT / "scripts" / "code_client.py"), *args],
                                          capture_output=True, text=True, env={**__import__("os").environ, **env},
                                          check=True)

                token = json.loads(client("upload", str(source)).stdout)["source_id"]
                search = json.loads(client("search", "--source-id", token, "--query", "public corridor",
                                           "--focused", "--max-results", "2", "--with-context").stdout)
                self.assertTrue(search["results"])
                highlight = json.loads(client("highlight", "--source-id", token, "--page", "1",
                                              "--text", sentence, "--excerpt-output", str(output)).stdout)
                self.assertTrue(highlight["verified"])
                self.assertEqual(highlight["preview"], str(output))
                with pymupdf.open(output) as pdf:
                    self.assertEqual(len(pdf), 1)
                    self.assertEqual(pdf[0].get_text(), pymupdf.open(source)[0].get_text())
                    self.assertTrue(any(d.get("fill") == (1.0, 1.0, 0.0) for d in pdf[0].get_drawings()))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
