# GTD4Teams · Self-Hosted Multi-Team GTD Workspace

[简体中文](README.md) · [Environment Rebuild Guide](doc/REBUILD.md)

A self-hosted GTD workspace for **small IT operations teams**. The shared team space handles work assignment, inspections, on-call shifts and resource ledgers; the personal space follows the five GTD steps (capture → clarify → organize → engage → reflect). The methodology combines lightweight ITIL ticket types (incident / request / change / problem), Kanban with explicit flow and WIP limits, and GTD.

**Zero dependencies, no cloud platform**: accounts, storage, row-level permissions and the AI channel are all self-hosted. Copy it to any directory with Python 3 and run — no `pip install`, no Node, no build step.

---

## Quick Start

```bash
python3 server.py
```

Open the address printed in the terminal (default <http://127.0.0.1:8787/>).

> Access it over HTTP. Do not double-click `index.html` — without a server, all APIs are unavailable (the page says so explicitly).

| Prerequisite | Requirement |
|---|---|
| Python | 3.8+ (standard library only; developed and tested on 3.13) |
| Third-party packages | **None** |
| Node / build tools | **None** (native ES modules served directly by Python) |
| Database | **No external service** (single SQLite file) |
| Network | Works fully offline |

---

## Common Commands

```bash
python3 server.py                        # Start (127.0.0.1:8787, data in ./data)
python3 server.py --port 9000            # Custom port
python3 server.py --host 0.0.0.0         # Allow LAN access
python3 server.py --open                 # Open browser on start
python3 server.py --no-signup            # Disable self-registration
python3 server.py --signup-code ops2026  # Require a signup code
python3 server.py --check                # Self-check
python3 server.py --export backup.json   # Full export (contains password hashes — protect it)
python3 server.py --import backup.json   # Full restore
python3 server.py --set-password you@example.com  # Reset a password
```

The data directory can be relocated with `--data-dir` or `GTD_DATA_DIR`.

---

## Key Capabilities

- **One `work_items` table for both domains**: `team_id IS NULL` means a personal GTD item; non-null means a team work item. A unified 10-state machine, with personal labels overlaid.
- **Server-side authorization**: `pyapp/policy.py` implements select / insert / update.using / update.check per table. It is the only security boundary — the frontend never filters by ownership.
- **Mandatory password-change gate**: accounts on an initial password can only change it on first login; the business UI is not rendered at all.
- **TLS certificate management**: upload / inspect / delete, asynchronous apply with fingerprint assertions. Private keys stay server-side; plaintext is never echoed or logged.
- **Structured access logs**: JSONL with 9 fields, automatic path redaction, `/api/health` excluded.
- **Local integration domain (REST + MCP)**: disabled by default, loopback-only. Tokens are stored as SHA-256 only; scopes separate data permissions (`write:*`) from the transport capability (`mcp`).

### Integration Domain Example

```bash
# Add to .env, then restart
GTD_API_ENABLED=1
GTD_API_PORT=8790
GTD_MCP_ENABLED=1
GTD_MCP_PORT=8722

curl http://127.0.0.1:8790/v1/health
# {"ok":true,"version":"20261118b","build":"..."}
```

Tokens are issued in the system administration page. Requests carrying an `Origin` header are always rejected with 403, and only Bearer authentication is accepted — cookies are not.

---

## Docker & Blue-Green Releases

```bash
cp deploy/env.example .env
sudo bash deploy/install.sh          # First install (aborts if smoke tests fail)
sudo bash deploy/release.sh --status # Show slot status
sudo bash deploy/release.sh          # Zero-downtime release
sudo bash deploy/rollback.sh         # Rollback
sudo bash deploy/backup.sh /var/backups/gtd4teams 14
```

Topology: two application slots (`app-blue` / `app-green`) behind a persistent Caddy entry point. Switching traffic rewrites Caddy's upstream order and calls `caddy reload` — Caddy is never restarted. The build ID is verified before switching to avoid "port is up but still running the old build".

---

## Tests

```bash
# Backend: 207 tests
python3 -m unittest tests.test_api tests.test_integration \
  tests.test_security_integration tests.test_dependencies tests.test_markdown_tables

# Frontend: 440 checks (requires Node.js)
for s in contract-test version-contract render-test auth-flow-test app-boot-test; do
  node .smoke/$s.mjs || break
done
```

All of the above are part of the `release.sh` gate. Any failure aborts the release before production is touched.

End-to-end API / MCP demonstration (temporary database; production data untouched):

```bash
python3 .workbuddy/demo_integration.py
```

---

## Layout

| Path | Contents |
|---|---|
| `server.py`, `pyapp/` | Backend (zero-dependency Python standard library) |
| `index.html`, `js/`, `css/` | Frontend (native ES modules, no build chain) |
| `tests/`, `.smoke/` | Backend and frontend tests |
| `deploy/` | Docker, Caddy, release / rollback / backup scripts |
| `doc/` | Full requirement, design, test, security and operations documents |
| `doc/REBUILD.md` | Steps and caveats for rebuilding on a new machine |

---

## Security Notes

- This repository contains **no real data**: `data/` (SQLite database, password hashes, token hashes), `.env` and `deploy/run/` are not committed. A new environment starts from an empty database.
- `deploy/*.sh` must use **LF line endings**. If edited on Windows and converted to CRLF, `set -Eeuo pipefail\r` fails with `invalid option name` on Linux — and `bash -n` does not catch it.
- After changing frontend code, increment the version string at all 8 source locations and verify with `node .smoke/version-contract.mjs`. That test guarantees values match; incrementing itself is a release discipline.

---

## Documentation

Full design and acceptance records are in [`doc/`](doc/), starting with [`doc/REBUILD.md`](doc/REBUILD.md) and [`doc/process/README.md`](doc/process/README.md).
