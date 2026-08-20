# Contributing to NetGeo

Terima kasih sudah mau berkontribusi. Dokumen ini singkat — detail lengkap ada di vault memory proyek (lihat §"Vault bersama").

## Setup dev

```bash
git clone https://github.com/suryaex/netgeo.git
cd netgeo
make install          # atau: bash install.sh
```

Butuh Python 3.11+ dan Node 20+ di mesin lokal (untuk tes cepat tanpa Docker).

## Verifikasi sebelum PR

```bash
# Backend
cd backend && .venv/bin/python -m pytest -q -W ignore::DeprecationWarning

# Frontend
cd frontend && npm install && npm run typecheck && npm run build
```

CI di GitHub Actions menjalankan hal yang sama di tiap PR — PR yang gagal CI tidak di-merge.

## Branch & PR

- Tiap kontributor kerja di **branch sendiri** (bukan langsung ke `main`). Pola nama: `<scope>/<intent>` — mis. `fix/rf-fallback-flag`, `feat/intent-sr`, `docs/refresh-engine-notes`.
- Push branch → buka PR ke `main`. PR **wajib** lewat review Surya (satu-satunya maintainer).
- Squash merge direkomendasikan; histori branch pribadi dihapus otomatis setelah merge.
- **Branch `main` dilindungi**: tidak bisa push langsung, tidak bisa force-push.

## Aturan commit

- Pesan: `<scope>: <imperative>` — scope ringkas (engine, frontend, rf, tests, docs, ci), kalimat dalam Bahasa Indonesia atau Inggris, tanpa titik akhir.
- Author: identitas kontributor masing-masing (nama + email yang sama dengan akun GitHub-nya). **Jangan pakai author orang lain.**
- **Tanpa jejak AI**: tidak ada `Co-Authored-By: Claude/Codex/…`, tidak ada `Generated with [nama AI]`. Lihat [[commit-no-ai-trace]] di vault.
- Bump versi kalau slice menyentuh app (bukan hanya docs/CI): edit `backend/app/core/config.py` + `frontend/package.json`, naik `v1.<minor>.<NNN>` (NNN jalan terus, jangan reset). Tag `v1.<minor>.<NNN>` di commit bump.

## Yang tidak boleh di-commit

- `docs/` (folder lokal-only — lihat `docs/README.md`): berisi catatan internal, screenshot, hostname/IP, kredensial.
- File apa pun berisi password, token, PAT, IP host internal.
- Hasil tes lokal (`__pycache__/`, `node_modules/`, `.pytest_cache/` sudah masuk `.gitignore`).

## Scope slice

Ikuti prinsip [[ponytail]] (skill `ponytail` di vault): minimal, lean, satu slice = satu tujuan. Kalau satu PR menyentuh >3 area engine yang tak berkaitan, pecah jadi PR kecil.

## Tanya / diskusi

Buka issue dengan template (`bug` atau `feature`) — Surya review.

---

## Vault bersama (untuk kontributor NetGeo)

NetGeo punya **vault memory kolaboratif** (lihat `netgeo-dev-workflow` di vault) yang jadi ingatan bersama lintas-kontributor:

- **Baca wajib** sebelum kontribusi pertama: `MEMORY.md` → `netgeo-dev-workflow.md` → `netgeo-next-plan.md` → `design.md`.
- **Tulis**: lihat `.claude/vault-rules.md` di vault untuk siapa boleh tulis apa (Mode 2 = kolaboratif).
- **Sinkron**: vault adalah repo git — `git pull` di awal sesi, `git push` di akhir (skill `lanjut-dev` §9).
