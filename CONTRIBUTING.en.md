*Indonesian version: [CONTRIBUTING.md](CONTRIBUTING.md)*

# Contributing to NetGeo

Thank you for wanting to contribute. This document has everything you need to send your first PR.

## Dev setup

Requires Python 3.12+ and Node 20+.

```bash
git clone https://github.com/NetGeo-Network-Simmulation/netgeo.git
cd netgeo

# Backend — venv is created manually (the Docker installer doesn't create it)
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# Frontend
cd frontend && npm ci && cd ..
```

To run the full stack (Postgres, Redis, nginx) with Docker: `make install`, then `make up`. That's not required to run the tests.

## Verify before a PR

```bash
# Backend — no database needed
cd backend && .venv/bin/python -m pytest -q -W ignore::DeprecationWarning
.venv/bin/ruff check .

# Frontend
cd frontend && npm run typecheck && npm run build
```

`ruff` is in `backend/requirements.txt` (dev/test section), so the `pip install -r requirements.txt` above already covers it.

GitHub Actions CI runs the same checks on every PR — a PR that fails CI doesn't get merged.

### NOS emulation path tests (`podman` marker)

Tests touching `PodmanAdaptor` (`tests/test_podman_adaptor.py` and friends) are marked `@pytest.mark.podman` and get **skipped** automatically if the rootless Podman socket isn't active — a machine without Podman still stays green. To actually run them (needed if you're working in `emul/`):

```bash
systemctl --user enable --now podman.socket   # once, per user, no sudo needed
cd backend && .venv/bin/python -m pytest -m podman tests/test_podman_adaptor.py -q
```

## Branch & PR

- Every contributor works on **their own branch** (never directly on `main`). Naming pattern: `<area>/<slug>`, with `area` matching `CODEOWNERS`: `proto/` (protocols under `netstack/protocols/`), `rf/` (RF/wireless), `emul/` (NOS emulation path), `ui/` (frontend), `docs/`, `fix/` (cross-area bugfix). Example: `proto/isis-lsp-refresh`, `ui/rack-panel-resize`.
- **One PR = one slice.** Don't bundle unrelated changes into one PR — it keeps review and revert easy.
- Push the branch → open a PR against `main`. Every PR **must** go through review by Surya (the sole maintainer).
- **Squash merge** — always, not a suggestion; your personal branch history is discarded automatically after merge.
- **`main` is protected**: no direct pushes, no force-pushes.

## Commit rules

- Message: `<scope>: <imperative>` — a short scope (engine, frontend, rf, tests, docs, ci), the sentence in Indonesian or English, no trailing period.
- Author: each contributor's own identity (name + email matching their GitHub account). **Never use someone else's author identity.**
- **No AI trace**: no `Co-Authored-By:` trailer for an AI assistant, no "Generated with …" line. A commit must read as the human contributor's own work.
- Bump the version if the slice touches the app (not just docs/CI): edit `backend/app/core/config.py` + `frontend/package.json`, bump `v1.<minor>.<NNN>` (NNN keeps counting up, never resets). Tag `v1.<minor>.<NNN>` on the bump commit.

## Developer Certificate of Origin (DCO)

NetGeo is Apache-2.0 licensed. To keep future licensing options clean, every
commit must be signed (`Signed-off-by:`) as a statement that you have the
right to submit that contribution:

```bash
git commit -s -m "scope: commit message"
```

`-s` appends a `Signed-off-by: Your Name <your@email>` line at the end of
the commit message — use the same name + email as the commit author (see
"Commit rules" above). Forgot `-s`? Add it after the fact with
`git commit --amend -s` (before pushing) without changing the commit's content.

By signing off, you certify (official DCO 1.1 text, developercertificate.org):

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.

Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

There's no bot/CI check yet that rejects a PR without sign-off — automated
enforcement will follow if Surya decides to add it. For now this is
documentation only, so the current PR flow doesn't suddenly get blocked.

## What must never be committed

- `docs/` (local-only folder, in `.gitignore`): holds internal notes, screenshots, hostnames/IPs, credentials. `dev-docs/` (at the repo root, outside `docs/`) is a separate folder that **must** be tracked — public contributor documentation.
- Any file containing a password, token, PAT, or internal host IP.
- Local test artifacts (`__pycache__/`, `node_modules/`, `.pytest_cache/` are already in `.gitignore`).

## Architecture documentation

Before touching code, read [`dev-docs/`](dev-docs/): `ARCHITECTURE.md`
(orientation, the engine's three execution paths), `ENGINE-GUIDE.md`
(netstack object model + determinism contract), `ADDING-A-PROTOCOL.md`
(recipe for adding a protocol), `TESTING.md` (test structure + timer rules),
`FRONTEND-GUIDE.md` (`frontend/src/` structure). What follows complements
that, it doesn't duplicate it — the "Where the code lives" section and the
choke-point warning stay here.

## Where the code lives

- `backend/engine/netstack/` — the protocol engine (device, frames, routing, `protocols/*.py`). The actual packet simulation.
- `backend/app/` — FastAPI API + service layer on top of the engine.
- `frontend/src/` — the React UI.

Two files are **deliberate choke points**, not free-for-all work areas: `backend/app/services/netlab.py` (the only bridge between `app/` and `engine/netstack/` — a new protocol must be registered here) and `frontend/src/api/client.ts` (the only API client — a new endpoint must add a line here). Both always need review from the area owner (see `.github/CODEOWNERS`) — don't be surprised if a PR touching them gets change requests.

## Slice scope

The principle is minimal and lean: one slice = one goal. Reach for stdlib before adding a dependency — if a few lines already do it, don't pull in a new library. If one PR touches more than 3 unrelated engine areas, split it into smaller PRs.

## Questions / discussion

Open an issue with the template (`bug` or `feature`) — Surya reviews it.

---

## Vault memory (optional — for regular contributors)

NetGeo has a **vault memory**: a separate private repo holding project-direction notes, a work queue, and cross-session technical lessons. **You don't need vault access to contribute** — everything required is in `README.md`, this document, and the code itself.

The vault is useful once you're a regular contributor and want to know *why* something was decided that way. Access is granted per-person by Surya; ask via an issue or direct contact.

If you already have access:

- **Read first**: `MEMORY.md` (index) → `netgeo-dev-workflow.md` → `netgeo-next-plan.md` (current work queue).
- **Write rules**: `.claude/vault-rules.md` — some notes can only be edited by Surya.
- **Sync**: the vault is a git repo; `git pull` before reading, `git push` after writing.
