"""Send PDF operations to WZMH Code server and save excerpts beside the chat."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen


def request(endpoint, payload=None, content_type="application/json"):
    url = (os.environ.get("WZMH_CODE_SERVER_URL") or "http://10.10.125.22:8766").rstrip("/") + endpoint
    headers = {"Content-Type": content_type}
    token = os.environ.get("WZMH_CODE_SERVER_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if payload is None else (payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8"))
    with urlopen(Request(url, data=data, headers=headers), timeout=240) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    health = sub.add_parser("health")
    health.add_argument("--full", action="store_true")
    upload = sub.add_parser("upload")
    upload.add_argument("pdf", type=Path)
    for name in ("search", "highlight"):
        command = sub.add_parser(name)
        command.add_argument("--source-id", required=True)
        if name == "highlight":
            command.add_argument("--excerpt-output", required=True, type=Path)
    args, rest = parser.parse_known_args()
    if args.operation == "health":
        if rest:
            parser.error("Unexpected health arguments")
        result = request("/health/full" if args.full else "/health")
    elif args.operation == "upload":
        if rest:
            parser.error("Unexpected upload arguments")
        result = request("/upload", args.pdf.read_bytes(), "application/pdf")
    elif args.operation == "search":
        result = request("/search", {"source_id": args.source_id, "args": rest})
    else:
        result = request("/highlight", {"source_id": args.source_id, "args": rest})
        output = args.excerpt_output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            parser.error("Excerpt output already exists")
        pdf = base64.b64decode(result["pdf_base64"], validate=True)
        if hashlib.sha256(pdf).hexdigest() != result["sha256"]:
            raise ValueError("Downloaded excerpt checksum did not match")
        output.write_bytes(pdf)
        result = result["metadata"]
        result["preview"] = str(output)
        fragment = result["browser_url"].partition("#")[2]
        result["browser_url"] = output.as_uri() + ("#" + fragment if fragment else "")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
