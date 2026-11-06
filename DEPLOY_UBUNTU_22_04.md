# Deploy WZMH Code HTTP API on Ubuntu 22.04

This guide uses `user@host` for your SSH login. Replace it with the real account and host. Package the checked-out HTTP branch, including all three bundled PDFs, before copying it to the server.

## 1. Copy the tested package

Run on the Codex computer from this repository:

```bash
cd /home/sparkbird/Downloads/wzmh-code-codex-skill
tar -czf /tmp/wzmh-code-http-deploy.tar.gz SKILL.md README.md DEPLOY_UBUNTU_22_04.md agents/openai.yaml references scripts/*.py scripts/code_client.ps1 scripts/health_check.cmd assets/source_documents tests/test_hyphenation.py tests/test_phase1.py tests/test_retrieval.py tests/test_http_bridge.py tests/test_powershell_client.py requirements-cloud.txt
sha256sum /tmp/wzmh-code-http-deploy.tar.gz
scp /tmp/wzmh-code-http-deploy.tar.gz user@host:/tmp/
```

Record the printed SHA-256 so you can compare it on the server.

## 2. Install the server runtime

SSH into the Ubuntu server:

```bash
ssh user@host
sudo apt update
sudo apt install -y python3.11 python3.11-venv curl
sudo useradd --system --create-home --home-dir /var/lib/wzmh-code --shell /usr/sbin/nologin wzmh-code
sudo install -d -o wzmh-code -g wzmh-code -m 0750 /var/lib/wzmh-code
sudo install -d -m 0755 /opt/wzmh-code
sha256sum /tmp/wzmh-code-http-deploy.tar.gz
sudo tar -xzf /tmp/wzmh-code-http-deploy.tar.gz -C /opt/wzmh-code
sudo python3.11 -m venv /opt/wzmh-code/.venv
sudo /opt/wzmh-code/.venv/bin/python -m pip install -r /opt/wzmh-code/requirements-cloud.txt
```

Compare the `sha256sum` result with the value recorded in step 1 before extracting. If the `python3.11` packages are unavailable, enable Ubuntu's `universe` repository and retry the install. [Ubuntu lists Python 3.10 as the 22.04 default and Python 3.11 as available](https://ubuntu.com/developers/docs/reference/availability/python/); this skill's SQLite error handling needs Python 3.11 or newer.

Run the full server health check as the service account:

```bash
sudo -u wzmh-code env XDG_STATE_HOME=/var/lib/wzmh-code/state /opt/wzmh-code/.venv/bin/python /opt/wzmh-code/scripts/health_check.py --json
```

Expect `"ok": true` and eight `PASS` checks. This verifies PyMuPDF, SQLite FTS5, the three PDF checksums, and writable cache/log folders.

## 3. Keep the API running

Create a root-owned `/etc/wzmh-code.env` containing a random `WZMH_CODE_SERVER_TOKEN=<64-hex-character-token>` and set its mode to `0600`. Create `/etc/systemd/system/wzmh-code.service` with:

```ini
[Unit]
Description=WZMH Code PDF HTTP API
After=network.target

[Service]
Type=simple
User=wzmh-code
Group=wzmh-code
WorkingDirectory=/opt/wzmh-code
EnvironmentFile=/etc/wzmh-code.env
Environment=XDG_STATE_HOME=/var/lib/wzmh-code/state
ExecStart=/opt/wzmh-code/.venv/bin/python /opt/wzmh-code/scripts/code_server.py --host 127.0.0.1 --port 8765 --data-dir /var/lib/wzmh-code/uploads
Restart=on-failure
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/wzmh-code

[Install]
WantedBy=multi-user.target
```

Start it and verify that it listens on loopback:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wzmh-code
sudo systemctl status wzmh-code --no-pager
```

An authenticated `GET http://127.0.0.1:8765/health` should list `obc-2024-v1`, `obc-2024-v2`, and `nbc-2020-p1`. The service rejects requests without the bearer token.

## 4. Test through SSH

On the Codex computer, make sure the skill loaded by Codex is this updated checkout. For this workspace, if `~/.codex/skills/wzmh-code-skill` does not already exist, link it with:

```bash
mkdir -p ~/.codex/skills
ln -s /home/sparkbird/Downloads/wzmh-code-codex-skill ~/.codex/skills/wzmh-code-skill
```

If that path already exists, inspect its `SKILL.md` and update the installation rather than replacing it blindly. Restart Codex if the skill does not appear after installation. Installing from GitHub `master` alone would install the older local-execution version until these HTTP changes are published there.

On the Codex computer, keep this tunnel open in a separate terminal:

```bash
ssh -N -L 8766:127.0.0.1:8765 user@host
```

Port `8766` on this computer now reaches the server's loopback port `8765`. From this repository on the Codex computer:

```bash
export WZMH_CODE_SERVER_URL=http://127.0.0.1:8766
export WZMH_CODE_SERVER_TOKEN=<token-from-server-administrator>
python scripts/code_client.py health --full
python scripts/code_client.py search --source-id obc-2024-v1 --reference 'Division B 3.6.2.1.(6)' --max-results 3 --with-context
python scripts/code_client.py highlight --source-id obc-2024-v1 --page 360 --text 'Electrical equipment that is required to be located in a service room by a regulation made under the Electricity Act, 1998, shall be installed in a service room separated from the remainder of the building by a fire separation having a fire-resistance rating not less than 1 h.' --excerpt-output outputs/references/OBC-2024-3.6.2.1-6-test.pdf
```

The full health check should report eight passes. Search should find PDF page 360. Highlight should return `"verified": true` and a local `preview` path. Open that PDF in Codex and check that the complete sentence is highlighted in yellow. The output filename must be new; remove or rename the previous test file before repeating the highlight command.

Finally, test the skill in Codex with an explicit prompt:

```text
Use $wzmh-code-skill from this repository. The WZMH Code server URL is http://127.0.0.1:8766. Under OBC 2024, show Division B Sentence 3.6.2.1.(6), cite its original PDF page, and open the highlighted excerpt.
```

Codex should use the HTTP client, give the OBC 2024 citation, link the downloaded PDF, and open that same file in the side panel. The shell environment above applies only to that shell; if Codex runs commands in a different process, provide the URL in the prompt or configure `WZMH_CODE_SERVER_URL` in Codex's process environment.

## Direct IP through nginx

The current WZMH deployment keeps the Python service on `127.0.0.1:8765` and exposes nginx at `10.10.125.22:8766`. The separate nginx server block proxies all paths to loopback, forwards `Authorization`, allows `10.10.0.0/16`, and rejects other source addresses. UFW permits TCP port `8766` from that same private range. The existing sites on ports 80 and 443 are unchanged. Keep the bearer token in the client environment and set `WZMH_CODE_SERVER_URL=http://10.10.125.22:8766`. Plain HTTP does not encrypt the token or PDFs, so use this endpoint only on the trusted private network or move it behind HTTPS.

On a Windows client without Python, the same server works through PowerShell:

```powershell
$env:WZMH_CODE_SERVER_URL = 'http://10.10.125.22:8766'
# Set WZMH_CODE_SERVER_TOKEN in this environment from the server administrator.
pwsh -NoProfile -File scripts/code_client.ps1 health --full
pwsh -NoProfile -File scripts/code_client.ps1 search --source-id obc-2024-v1 --reference 'Division B 3.6.2.1.(6)' --max-results 3 --with-context
```

Use `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/code_client.ps1` if PowerShell 7 is unavailable. Replace `search` with `highlight` and the same arguments shown above to download a verified PDF excerpt.

The server keeps uploaded PDFs in `/var/lib/wzmh-code/uploads` until an operator removes them. The client keeps downloaded excerpts in each chat's `outputs/references/` directory; HTTP mode does not currently apply the older local 200-PDF cleanup policy.
