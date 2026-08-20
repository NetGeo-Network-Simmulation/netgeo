## Ringkasan

<!-- 1–3 kalimat: apa yang diubah dan kenapa. -->

## Tipe perubahan

<!-- Centang salah satu. -->

- [ ] Bug fix (perubahan yang memperbaiki issue)
- [ ] Fitur baru
- [ ] Refactor / cleanup (tanpa perubahan perilaku)
- [ ] Docs / CI / tooling

## Slice terkait

<!-- Taut ke issue atau antrian live-plan. -->

- Issue / Taut: #
- Slice di `netgeo-next-plan.md` (vault): <!-- tulis nama slice -->

## Yang diuji

<!-- Centang yang dijalankan lokal. CI akan menjalankan semuanya. -->

- [ ] `cd backend && .venv/bin/python -m pytest -q`
- [ ] `cd frontend && npm run typecheck && npm run build`
- [ ] Tes manual di host dev (`make up`)

## Checklist

- [ ] Author = identitas kontributor sendiri (bukan orang lain)
- [ ] **Tanpa** `Co-Authored-By: AI` / `Generated with …`
- [ ] Tidak ada commit `docs/` (folder lokal-only)
- [ ] Tidak ada password / token / PAT / IP internal di diff
- [ ] Versi di-bump kalau menyentuh app (`backend/app/core/config.py` + `frontend/package.json`)
- [ ] Vault memory di-update kalau slice menyentuh next-plan / live-state / lesson baru
