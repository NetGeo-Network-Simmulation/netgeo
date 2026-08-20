# Contributing to NetGeo

Terima kasih sudah mau berkontribusi. Dokumen ini memuat semua yang kamu butuhkan untuk mengirim PR pertama.

## Setup dev

Butuh Python 3.12+ dan Node 20+.

```bash
git clone https://github.com/suryaex/netgeo.git
cd netgeo

# Backend — venv dibuat manual (installer Docker tidak membuatnya)
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# Frontend
cd frontend && npm ci && cd ..
```

Untuk menjalankan stack lengkap (Postgres, Redis, nginx) pakai Docker: `make install`, lalu `make up`. Itu tidak diperlukan untuk menjalankan tes.

## Verifikasi sebelum PR

```bash
# Backend — tidak butuh database
cd backend && .venv/bin/python -m pytest -q -W ignore::DeprecationWarning

# Frontend
cd frontend && npm run typecheck && npm run build
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
- **Tanpa jejak AI**: tidak ada trailer `Co-Authored-By:` untuk asisten AI, tidak ada baris "Generated with …". Commit harus tampil sebagai karya kontributor manusianya.
- Bump versi kalau slice menyentuh app (bukan hanya docs/CI): edit `backend/app/core/config.py` + `frontend/package.json`, naik `v1.<minor>.<NNN>` (NNN jalan terus, jangan reset). Tag `v1.<minor>.<NNN>` di commit bump.

## Yang tidak boleh di-commit

- `docs/` (folder lokal-only — lihat `docs/README.md`): berisi catatan internal, screenshot, hostname/IP, kredensial.
- File apa pun berisi password, token, PAT, IP host internal.
- Hasil tes lokal (`__pycache__/`, `node_modules/`, `.pytest_cache/` sudah masuk `.gitignore`).

## Scope slice

Prinsipnya minimal dan lean: satu slice = satu tujuan. Utamakan stdlib sebelum menambah dependency — kalau beberapa baris sudah cukup, jangan tarik library baru. Kalau satu PR menyentuh lebih dari 3 area engine yang tak berkaitan, pecah jadi beberapa PR kecil.

## Tanya / diskusi

Buka issue dengan template (`bug` atau `feature`) — Surya review.

---

## Vault memory (opsional — untuk kontributor tetap)

NetGeo punya **vault memory**: repo privat terpisah berisi catatan arah proyek, antrean kerja, dan pelajaran teknis lintas-sesi. **Kamu tidak butuh akses vault untuk berkontribusi** — semua yang wajib ada di `README.md`, dokumen ini, dan kode itu sendiri.

Vault berguna kalau kamu jadi kontributor tetap dan ingin tahu *kenapa* sesuatu diputuskan begitu. Aksesnya diberikan per-orang oleh Surya; minta lewat issue atau kontak langsung.

Kalau sudah punya akses:

- **Baca dulu**: `MEMORY.md` (indeks) → `netgeo-dev-workflow.md` → `netgeo-next-plan.md` (antrean kerja terkini).
- **Aturan tulis**: `.claude/vault-rules.md` — sebagian catatan hanya boleh diubah Surya.
- **Sinkron**: vault adalah repo git; `git pull` sebelum membaca, `git push` setelah menulis.
