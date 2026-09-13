*Bahasa: Indonesia | [English](README.en.md)*

<div align="center">

# NetGeo

**Platform simulasi jaringan, perencanaan RF/GIS, dan digital twin — self-hosted**

*Kamu gambar topologi. Di baliknya ada netstack asli yang jalan — routing table, pemilihan DR/BDR,
capture pcapng. Kalau hasil simulasi beda dengan router FRR beneran, itu bug yang harus dibenerin,
bukan selisih yang kami maklumi.*

[![CI](https://github.com/NetGeo-Network-Simmulation/netgeo/actions/workflows/backend.yml/badge.svg)](https://github.com/NetGeo-Network-Simmulation/netgeo/actions)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Version](https://img.shields.io/badge/version-1.2.123-brightgreen)
![Channel](https://img.shields.io/badge/channel-beta-blueviolet)
![Python](https://img.shields.io/badge/python-3.12+-blue)
![React](https://img.shields.io/badge/react-18-61dafb)

</div>

---

## Apa ini

NetGeo dibuat oleh insinyur jaringan yang bosan menatap "diagram topologi" yang ternyata cuma
kotak dan garis. Satu aplikasi, tiga peran: simulator, perencana RF/fiber, dan digital twin dari
config yang kamu import. Gambar topologinya sendiri, atau langsung import config Cisco/MikroTik
yang asli. Netstack-nya deterministik dan benar-benar jalan — VLAN, OSPF, BGP, NAT, ACL — lengkap
dengan capture paket. Tanya "apakah A bisa menjangkau B", kamu dapat jawaban yang disertai bukti
keputusan routing-nya. Bukan tebakan.

Cocok buat mahasiswa jaringan, dosen yang menilai praktikum, dan insinyur ISP yang mau
merencanakan atau memvalidasi topologi tanpa harus menyentuh perangkat produksi.

---

## Cara pasang

NetGeo dirilis dalam lima bentuk, tapi baru dua yang benar-benar bisa dipakai hari ini. Baca dulu tabelnya
sebelum milih. Ukuran filenya memang besar, dan itu disengaja — window native-nya asli, jadi ikut
membawa runtime Qt sendiri. Itu bukan bug yang perlu "dioptimasi".

| # | Bentuk | Frontend | Backend | Peta | Status |
|---|---|---|---|---|---|
| 1 | Native, sepenuhnya offline | window native | lokal | tile open-source | **belum** — window-nya nyata, tapi tile peta masih ambil dari internet secara default |
| 2 | Native + Google Maps | window native | lokal | Google Maps API | **belum dibangun** — integrasinya belum ada |
| 3 | Native + backend remote | window native | server sendiri | online | **belum dibangun** — launcher selalu menjalankan backend lokal |
| 4 | Headless | browser | lokal | online atau lokal | **sudah jalan** — `--no-window` (lihat di bawah) |
| 5 | Online penuh | browser | server sendiri | online | **sudah jalan** — ini instalasi Docker di bawah |

### Unduh rilis (jalur tercepat)

Ambil aset dari tag terbaru di [Releases](https://github.com/NetGeo-Network-Simmulation/netgeo/releases/tag/v1.2.123):

| Aset | Ukuran | Apa isinya |
|---|---|---|
| `NetGeo-x86_64.AppImage` | 231.6 MB | Window Qt native, Linux, tanpa langkah instalasi — `chmod +x`, jalankan |
| `netgeo-1.2.123-setup.exe` | 157.9 MB | Installer Windows, window native — **sudah dibangun, belum pernah diuji jalan di Windows asli** |
| `netgeo-linux-x86_64.tar.gz` | 253.5 MB | Bundle onedir, Linux, extract lalu jalankan `netgeo` |

Ketiganya sama-sama lewat jalur window native (bentuk #1/#2/#3 di atas, minus bagian yang belum
dibangun). Jadi hari ini perilakunya persis bentuk #4: backend lokal, peta online. Isi tiap aset
dan cara buildnya ada di `packaging/README.md`.

### Instalasi Docker / server (bentuk #5 — online penuh)

Ini yang kamu perlukan untuk instance bersama yang diakses lewat browser.

**Prasyarat:** Git, Docker + Docker Compose, port **8090** yang bebas (override dengan
`HTTP_PORT`). Di Linux installer otomatis memasang Docker (Fedora, Ubuntu, Debian, RHEL, Arch); di
Windows/macOS pasang Docker Desktop dulu.

```bash
curl -fsSL https://raw.githubusercontent.com/NetGeo-Network-Simmulation/netgeo/main/bootstrap.sh | bash
```

atau manual:

```bash
git clone https://github.com/NetGeo-Network-Simmulation/netgeo.git
cd netgeo
./install.sh          # Linux / macOS
.\install.ps1         # Windows PowerShell
```

Installer membuat secret, build stack-nya (PostgreSQL + FastAPI + React di belakang nginx),
menunggu `/api/health`, lalu mencetak:

```
On this machine  ->  http://localhost:8090
On the network   ->  http://<LAN-IP>:8090
API docs         ->  http://<LAN-IP>:8090/docs
```

| Perintah | Efek |
|---|---|
| `./install.sh --rebuild` | Rebuild paksa, tanpa cache |
| `./install.sh --down` | Hentikan stack |
| `./install.sh --reset` | Hentikan dan hapus semua data |
| `HTTP_PORT=9000 ./install.sh` | Pakai port lain |
| `./uninstall.sh` | Uninstall, data + config sistem tetap ada |
| `./uninstall.sh --purge` | Bersih total — volume data, image lokal, update-watcher, aturan firewall, `/var/lib/netgeo` |

> Sudah terlanjur hapus folder repo-nya? `--purge` tetap bisa menemukan jejak Docker NetGeo lewat
> nama:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/NetGeo-Network-Simmulation/netgeo/main/uninstall.sh | sudo bash -s -- --purge --yes
> ```

<details>
<summary>Jalankan backend / frontend langsung (development)</summary>

```bash
# Backend
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000   # http://localhost:8000/docs
pytest -q

# Frontend
cd frontend
npm install
npm run dev                                 # http://localhost:5180
```

</details>

### Mode headless (bentuk #4)

Backend jalan lokal, UI kebuka di browser biasa, bukan window native — pilihan jujur kalau kamu
tidak mau dependensi desktop, atau lagi di mesin tanpa display sama sekali:

```bash
python packaging/launcher.py --no-window
NETGEO_NO_WINDOW=1 python packaging/launcher.py   # same thing, for systemd units
```

### Region peta offline

Backend bisa menyajikan tile basemap dari file `.mbtiles` lokal, jadi tidak perlu ambil dari
internet (`GET /api/maps/tiles/{z}/{x}/{y}`). Frontend otomatis pakai file itu kalau ada, dan balik
ke tile online begitu filenya tidak ada. Tidak ada region siap-pakai yang kami hosting — kamu yang
bawa filenya sendiri, atau kasih URL untuk mengambilnya:

```bash
./install.sh --offline-map=/path/to/region.mbtiles
./install.sh --offline-map-url=https://example.org/region.mbtiles
./install.sh --no-offline-map          # skip, no prompt
```

Installer Windows menanyakan hal yang sama di halaman wizard. Untuk menambah atau menghapus peta
belakangan, cukup taruh atau hapus filenya — tidak perlu jalankan installer lagi:

```bash
mkdir -p ~/.config/netgeo
cp my-region.mbtiles ~/.config/netgeo/offline-map.mbtiles   # install
rm ~/.config/netgeo/offline-map.mbtiles                      # remove, back to online tiles
```

Detail lengkap (lokasi file, aturan fallback, cara wizard Windows-nya dirakit) ada di
[`packaging/README.md`](packaging/README.md).

---

## Kenapa engine ini layak dipercaya

Kebanyakan simulator cuma konsisten dengan dirinya sendiri — logikanya diperiksa pakai logika yang
sama, jadi apapun hasilnya pasti "benar" menurut dirinya sendiri. NetGeo juga menjalankan **oracle
test**: topologi yang sama persis dijalankan dua kali, sekali di engine Python deterministik kami,
sekali lagi di container **FRR 10.7.0** asli. Hasilnya dibandingkan langsung dengan ketentuan
RFC — bukan format CLI, bukan timing.

| Perbandingan | Ketentuan RFC | Hasil |
|---|---|---|
| Pemilihan DR/BDR OSPF (RFC 2328 §9.4/§7.3), non-preemptive | Siapa yang terpilih, dan router berprioritas lebih tinggi yang datang belakangan tidak menggantikan yang sudah menjabat | **Cocok** |
| BGP best-path — AS-path terpendek | Rute dengan AS-path lebih pendek menang | **Cocok** |
| BGP best-path — eBGP over iBGP | Rute hasil eBGP diutamakan atas rute hasil iBGP, kalau yang lain sama | **Cocok** |

Nol mismatch dari semua kasus yang sudah diuji. Lihat
[`backend/tests/ORACLE_HARNESS.md`](backend/tests/ORACLE_HARNESS.md) untuk cara sebuah kasus
dibangun.

Tie-break yang tersisa — local-pref, ORIGIN, MED — sempat mandek lama. Bukan gara-gara
harness-nya: engine ini memang belum punya cara untuk mengatur atribut-atribut itu, jadi logika
best-path-nya sudah ada tapi tidak bisa disentuh siapa pun yang memakainya. `add_neighbor(...,
local_pref_in=N, med_out=N)` dan `advertise_network(..., origin=...)` menutup celah itu di
v1.2.123, jadi kasus oracle untuk ketiganya sekarang tinggal soal waktu, bukan lagi mustahil
dikerjakan. Begitu MED bisa diatur, langsung ketahuan satu bug nyata: nilainya bocor lintas
batas eBGP, tersembunyi selama ini karena nilainya memang selalu nol.

Selebihnya, ini engine pure-Python (tanpa dependensi native — jalan di Linux, Windows, ARM) yang
menggerakkan L2 (MAC learning, 802.1Q, STP, LACP), L3 (longest-prefix routing, NAT44, ACL, DHCP,
DNS), OSPF multi-area, BGP dengan route-reflector dan community, VRRP, dual-stack IPv4+IPv6,
antrean QoS berbasis DSCP, CLI ala Cisco/MikroTik per device, export pcapng, sampai digital twin
hasil import config lengkap dengan reachability engine yang menjawab "apakah A bisa menjangkau B"
pakai jejak keputusan routing yang benar-benar dieksekusi sebagai bukti. Daftar fitur lengkapnya ada di
[`dev-docs/ARCHITECTURE.md`](dev-docs/ARCHITECTURE.md).

Start di bawah 3 detik, idle di bawah 300 MB RAM.

---

## Yang belum ada

Sengaja ditulis terus terang: fitur yang belum ada tapi kamu tahu, jauh lebih baik daripada fitur
yang diklaim ada tapi ternyata rusak di tanganmu:

- Tidak ada IS-IS, MPLS, Segment Routing, atau EVPN.
- Tidak ada QoS traffic shaping di luar classify/mark/queue berbasis DSCP (tidak ada policer,
  tidak ada hierarki shaper).
- Tidak ada state machine TCP yang lengkap — netstack ini mensimulasikan reachability dan routing,
  bukan transport stack yang akurat sampai level byte.
- Tidak ada DNS64/NAT64.
- BGP local-pref/ORIGIN/MED sudah bisa dikonfigurasi sejak v1.2.123, tapi belum dicek-silang
  lawan FRR — kasus oracle untuk itu ditulis berikutnya. Default-nya tetap local-pref 100, origin
  IGP, MED 0.
- Installer Windows (`netgeo-1.2.123-setup.exe`) belum pernah dijalankan di mesin Windows asli —
  sudah dibangun dan diperiksa, belum diverifikasi end-to-end.
- Tidak ada isolasi multi-tenant di bentuk full-online (#5) — satu instance bersama, satu set data
  untuk semua.
- Bentuk distribusi #1 (peta yang benar-benar offline), #2 (Google Maps), #3 (backend remote untuk window
  native) belum ada — lihat tabel di atas.

---

## Teknologi yang dipakai

**Backend:** Python 3.12+, FastAPI (async), Pydantic, PostgreSQL, Pytest.
**Frontend:** React 18 + TypeScript, Vite, Zustand, React Flow, Tailwind CSS.
**Infra:** Docker + Docker Compose di belakang nginx; packaging native lewat PyInstaller + Qt
(pywebview).

---

## Kontribusi

Mulai dari **[CONTRIBUTING.md](CONTRIBUTING.md)** — setup lokal, perintah yang dijalankan CI, alur
branch/PR. Untuk arsitektur, kontrak determinisme engine, cara menambah protokol, dan struktur
frontend, lihat **[dev-docs/](dev-docs/)**.

Kerja di branch (`<scope>/<intent>`), jaga `main` tetap hijau, buka PR. `main` diproteksi — check
`test` (lint backend + pytest) dan `build` (typecheck + build frontend) wajib lolos sebelum merge.

Bug dan ide: [Issues](https://github.com/NetGeo-Network-Simmulation/netgeo/issues).

---

## Lisensi

[Apache-2.0](LICENSE) © Muhammad Surya Ragasin — Politeknik Negeri Sriwijaya, D4 Teknik
Telekomunikasi.
