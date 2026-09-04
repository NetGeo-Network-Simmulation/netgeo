# Contributing to NetGeo

Terima kasih sudah mau berkontribusi. Dokumen ini memuat semua yang kamu butuhkan untuk mengirim PR pertama.

## Setup dev

Butuh Python 3.12+ dan Node 20+.

```bash
git clone https://github.com/NetGeo-Network-Simmulation/netgeo.git
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
.venv/bin/ruff check .

# Frontend
cd frontend && npm run typecheck && npm run build
```

`ruff` ada di `backend/requirements.txt` (bagian dev/test), jadi `pip install -r requirements.txt` di atas sudah mencakupnya.

CI di GitHub Actions menjalankan hal yang sama di tiap PR — PR yang gagal CI tidak di-merge.

### Tes jalur emulasi NOS (marker `podman`)

Tes yang menyentuh `PodmanAdaptor` (`tests/test_podman_adaptor.py` dst.) ditandai `@pytest.mark.podman` dan otomatis **di-skip** kalau socket rootless Podman tidak aktif — mesin tanpa Podman tetap hijau. Untuk benar-benar menjalankannya (dibutuhkan kalau kerja di `emul/`):

```bash
systemctl --user enable --now podman.socket   # sekali saja, per user, tanpa sudo
cd backend && .venv/bin/python -m pytest -m podman tests/test_podman_adaptor.py -q
```

## Branch & PR

- Tiap kontributor kerja di **branch sendiri** (bukan langsung ke `main`). Pola nama: `<area>/<slug>`, awalan `area` sesuai `CODEOWNERS`: `proto/` (protokol di `netstack/protocols/`), `rf/` (RF/wireless), `emul/` (jalur emulasi NOS), `ui/` (frontend), `docs/`, `fix/` (bugfix lintas-area). Contoh: `proto/isis-lsp-refresh`, `ui/rack-panel-resize`.
- **Satu PR = satu slice.** Jangan gabung beberapa perubahan tak berkaitan dalam satu PR — memudahkan review dan revert.
- Push branch → buka PR ke `main`. PR **wajib** lewat review Surya (satu-satunya maintainer).
- **Squash merge** — selalu, bukan rekomendasi; histori branch pribadi dihapus otomatis setelah merge.
- **Branch `main` dilindungi**: tidak bisa push langsung, tidak bisa force-push.

## Aturan commit

- Pesan: `<scope>: <imperative>` — scope ringkas (engine, frontend, rf, tests, docs, ci), kalimat dalam Bahasa Indonesia atau Inggris, tanpa titik akhir.
- Author: identitas kontributor masing-masing (nama + email yang sama dengan akun GitHub-nya). **Jangan pakai author orang lain.**
- **Tanpa jejak AI**: tidak ada trailer `Co-Authored-By:` untuk asisten AI, tidak ada baris "Generated with …". Commit harus tampil sebagai karya kontributor manusianya.
- Bump versi kalau slice menyentuh app (bukan hanya docs/CI): edit `backend/app/core/config.py` + `frontend/package.json`, naik `v1.<minor>.<NNN>` (NNN jalan terus, jangan reset). Tag `v1.<minor>.<NNN>` di commit bump.

## Yang tidak boleh di-commit

- `docs/` (folder lokal-only, di `.gitignore`): berisi catatan internal, screenshot, hostname/IP, kredensial. `dev-docs/` (di root, di luar `docs/`) adalah folder terpisah yang justru **wajib** ter-track — dokumentasi kontributor publik.
- File apa pun berisi password, token, PAT, IP host internal.
- Hasil tes lokal (`__pycache__/`, `node_modules/`, `.pytest_cache/` sudah masuk `.gitignore`).

## Dokumentasi arsitektur

Sebelum menyentuh kode, baca [`dev-docs/`](dev-docs/): `ARCHITECTURE.md`
(orientasi, tiga jalur eksekusi engine), `ENGINE-GUIDE.md` (model objek
netstack + kontrak determinisme), `ADDING-A-PROTOCOL.md` (resep tambah
protokol), `TESTING.md` (struktur test + aturan timer), `FRONTEND-GUIDE.md`
(struktur `frontend/src/`). Dokumen di bawah ini melengkapi, bukan
menduplikasi — bagian "Kode ada di mana" dan peringatan choke-point tetap
di sini.

## Kode ada di mana

- `backend/engine/netstack/` — engine protokol (device, frames, routing, `protocols/*.py`). Simulasi paket sesungguhnya.
- `backend/app/` — API FastAPI + service layer di atas engine.
- `frontend/src/` — UI React.

Dua file adalah **choke-point yang disengaja**, bukan area kerja bebas: `backend/app/services/netlab.py` (satu-satunya jembatan `app/` ↔ `engine/netstack/` — protokol baru wajib didaftarkan di sini) dan `frontend/src/api/client.ts` (satu-satunya API client — endpoint baru wajib nambah baris di sini). Keduanya selalu butuh review pemilik area (lihat `.github/CODEOWNERS`), jangan kaget kalau PR yang menyentuhnya diminta perubahan.

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
