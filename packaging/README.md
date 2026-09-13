*English version: [README.en.md](README.en.md)*

# Paket desktop NetGeo (kerangka C1-a)

Launcher native all-in-one untuk edisi D1 (lihat
`docs/design/13-DISTRIBUTION-PLAN.md`, lokal-only, tidak ada di listing repo
ini). Ini **kerangka** — hal paling sederhana yang jalan, bukan produk jadi.

## Isi folder ini

- `launcher.py` — menjalankan aplikasi FastAPI yang sudah ada
  (`backend/app/main.py`) di port localhost bebas, me-mount `frontend/dist`
  sebagai static files di port yang sama, membukanya di jendela native
  pywebview (WebKitGTK di Linux). Satu proses, satu port. Terverifikasi
  jalan baik sebagai script biasa maupun sebagai binary PyInstaller di
  bawah (frontend serve 200, `/api/health` 200, shutdown bersih). Lihat
  "System prerequisites" di bawah untuk perilaku fallback jendela native.
- `icons/` — `netgeo.ico` (multi-size 16/32/48/64/128/256, untuk Windows)
  dan `netgeo-{128,256,512}.png` (Linux), dirasterisasi dari
  `frontend/public/netgeo.svg` dengan ImageMagick (`magick`).
- `netgeo.spec` — spec PyInstaller onedir yang membundel launcher +
  `backend/app` (termasuk file data non-Python-nya, `app/data/*.json`) +
  `frontend/dist` + `icons/` + ikon Windows. Build dan jalan sukses di
  mesin ini (`pyinstaller netgeo.spec`, output di `packaging/dist/netgeo/`,
  di-gitignore).
- `requirements.txt` — mem-pin `pyinstaller==6.22.2` (hanya untuk build)
  dan `pywebview==6.2.1` (dependency runtime `launcher.py`; jangan
  tambahkan keduanya ke `backend/requirements.txt`).
- `linux/` — integrasi desktop per-user: `netgeo.desktop` (entry XDG),
  `install.sh` / `uninstall.sh`, `build-in-container.sh` (build Linux
  portable via rootless Podman — lihat "Installing" di bawah, **pakai
  ini, jangan `pyinstaller netgeo.spec` langsung di mesin ini**),
  `build-appimage.sh` (membungkus bundle itu jadi satu file
  `NetGeo-x86_64.AppImage` via `linuxdeploy` + `linuxdeploy-plugin-appimage`,
  diunduh sesuai kebutuhan ke `packaging/linux/.appimage-tools/`,
  di-gitignore).
- `windows/netgeo.iss` — script Inno Setup yang menghasilkan
  `netgeo-<version>-setup.exe` per-user (shortcut Start Menu + Desktop
  opsional, uninstaller, tanpa hak admin).
- `../.github/workflows/desktop.yml` — build bundle onedir unsigned di
  `windows-latest` + `ubuntu-22.04` (dipin, bukan `ubuntu-latest` — lihat
  komentar di workflow-nya untuk alasannya), lalu membungkusnya jadi
  artifact siap-install per platform (Windows: `.exe` Inno Setup; Linux:
  tarball dari bundle + `linux/`), upload keduanya. Trigger:
  `workflow_dispatch` dan tag `v*` saja (bukan tiap push). *Signing*
  Windows (terpisah dari installer) adalah stub step yang dimatikan
  (`if: false`) — tidak ada kredensial jenis apa pun di repo ini.

## Backend jendela native: Qt/PySide6 (bundled), GTK (sistem, fallback)

`launcher.py` membuka NetGeo di jendela native lewat `pywebview`. Sejak
slice native-window, build yang dipaketkan (PyInstaller/AppImage/installer)
memaksa `PYWEBVIEW_GUI=qt` dan membawa runtime Qt-nya sendiri.
`packaging/requirements.txt` menarik `pywebview[pyside6]` (PySide6 +
QtPy), dan `netgeo.spec` mendaftarkan submodule `PySide6.QtWebEngine*`
yang konkret sebagai `hiddenimports`. Efeknya, PyInstaller menjalankan
`hook-PySide6.QtWebEngineCore.py` bawaannya sendiri, yang mengumpulkan
binary helper QtWebEngineProcess, resource Qt, dan file terjemahan
langsung ke dalam bundle onedir. Jalur ini tidak butuh system package
apa pun — jendelanya benar-benar self-contained, bukan dependency
sistem yang harus diinstal user.

**Kenapa bukan GTK (percobaan sebelumnya)?** Ini jalan buntu yang
terbukti, bukan dugaan. PyInstaller tidak punya satu pun hook untuk
`gi`/PyGObject — tidak ada apa pun di pipeline ini yang mengumpulkan
file `.typelib` atau pohon shared-library WebKitGTK. Jadi meski `gi`
bisa di-import saat build, bundle-nya tetap harus me-resolve itu dari
system path saat runtime, dan itu beda-beda tergantung distro dan versi
minor CPython (lihat investigasi lama di bawah). Qt/PySide6 tidak punya
masalah itu: wheel-nya self-contained, dan hook bawaan PyInstaller
(terverifikasi ada di direktori `hooks/` milik `pyinstaller==6.22.2`)
sudah tahu cara mengumpulkannya secara lengkap. Alasan yang sama kenapa
hampir semua tutorial PyInstaller+"native window" pakai Qt, bukan GTK.

GTK tetap ada sebagai **fallback sekunder yang disediakan sistem**.
`setdefault` pada `PYWEBVIEW_GUI` di `_try_webview()` masih memungkinkan
dev yang run dari source meng-override ke `gtk`. Kalau Qt sendiri gagal
start (lib dasar X11/OpenGL tidak ada di sistem yang sangat minimal),
guilib pywebview sendiri masih mencoba GTK berikutnya sebelum menyerah —
di titik itu `launcher.py` jatuh ke browser sistem persis seperti
sebelumnya, dan mencatat alasannya:

```bash
# Fedora
sudo dnf install webkit2gtk4.1 python3-gobject gtk3

# Ubuntu / Debian
sudo apt install libwebkit2gtk-4.1-0 python3-gi gir1.2-webkit2-4.1 libgtk-3-0
```

`gir1.2-webkit2-4.1` (typelib introspection) adalah yang paling sering
kelewat — tanpa itu PyGObject bisa import `gi` dengan baik tapi tidak
bisa menjangkau WebKit2.

**Kalau salah satu dari ini hilang, NetGeo tidak crash.** `launcher.py`
menangkap kegagalannya, mencetak package apa yang perlu diinstal, dan
membuka app di browser default sistem sebagai gantinya — URL sama, app
sama, cuma bukan di jendelanya sendiri. Jalur fallback ini diuji di
`backend/tests/test_launcher.py` (dengan `webview` di-stub/tidak ada
supaya CI tidak pernah butuh WebKitGTK terinstal) dan sudah diverifikasi
langsung di mesin dev ini — lihat "Installing" di bawah untuk apa yang
benar-benar dijalankan.

## Installing

### Linux — JANGAN build bundle rilis di mesin dev ini

**`pyinstaller netgeo.spec` yang dijalankan langsung di mesin ini (Fedora
44, glibc 2.43) menghasilkan binary yang tidak akan jalan di distro yang
lebih tua.** Ini terbukti, bukan cuma teori: bundle hasil build Fedora
yang dikirim ke VM Ubuntu 24.04 baru (glibc 2.39) gagal dengan
`GLIBC_ABI_GNU2_TLS' not found (required by libpython3.14.so.1.0)` —
repro lengkap ada di `docs/qa/launcher-vm-ubuntu-2026-08-28.md`
(lokal-only). Sebabnya: PyInstaller me-link glibc *host build*-nya
secara statis, dan glibc cuma jalan maju — glibc lebih tua ke distro
lebih baru itu aman, tapi glibc lebih baru ke distro lebih tua itu
tidak.

**Perbaikan: build di dalam `packaging/linux/build-in-container.sh`.**
Script ini menjalankan `pyinstaller netgeo.spec` yang sama, tapi di
dalam container Podman rootless berbasis `ubuntu:22.04` (glibc 2.35,
python3.11 — yang terbaru yang tersedia di sana). Hasilnya, glibc floor
binary jadi 2.35, bukan apa pun yang kebetulan berjalan di mesin ini.
Tidak ada dependency baru: Podman sudah terinstal dan sudah dipakai di
mesin ini (lihat vault `research/spike-frr-podman.md`).

```
./packaging/linux/build-in-container.sh          # → packaging/dist-container/dist/netgeo
cd packaging/linux && ./install.sh                # per-user, tanpa root
```

`frontend/dist` di-build di host duluan (Node ada di sini, tidak
diinstal di container), lalu di-bind-mount masuk. Named volume Podman
(`netgeo-build-apt-cache`, `netgeo-build-pip-cache`) menyimpan unduhan
apt/pip lintas re-run. Egress environment ini cuma ~115 KB/s, jadi
tanpa cache ini, mengunduh ulang semuanya tiap retry bakal berat
sekali.

**Terverifikasi jalan, dua arah, 2026-08-28:**
- Di-build di container, dijalankan **di mesin ini (Fedora 44)**: `/` →
  200, `/api/health` → 200, JS asset hasil build → 200.
- Binary yang sama, dikirim ke **VM Ubuntu 24.04.4** baru (headless,
  1 vCPU/3.3 GB): `install.sh` sebagai non-root, `NETGEO_NO_BROWSER=1`,
  `/` → 200, `/api/health` →
  `{"status":"ok","app":"NetGeo","version":"1.2.99","channel":"beta"}`
  (versi app saat tes, bukan versi saat ini — lihat `APP_VERSION` di
  `backend/app/core/config.py`), JS asset hasil build
  (`/assets/index-BTJLwYj6.js`) → 200. `uninstall.sh` dijalankan dua kali
  dengan bersih (idempotent), VM tersisa nol jejak `netgeo`.
- `objdump -T` di semua `.so` yang dibundel mentok di `GLIBC_2.35` —
  cocok persis dengan base container-nya, tidak ada yang lebih tinggi
  bocor masuk.

Belum diuji: Debian 12, Ubuntu 20.04 atau lebih lama (glibc 2.31, di
bawah floor 2.35 build ini — butuh base image yang lebih tua lagi),
distro apa pun selain Fedora/Ubuntu.

Uninstall: `packaging/linux/uninstall.sh` (salinan di
`~/.local/share/netgeo/` tidak disimpan — jalankan lagi yang dari
checkout/tarball).

### Linux — AppImage (format installer pertama)

Keputusan + perbandingan lengkap (AppImage vs Flatpak vs .deb/.rpm vs
tarball): `docs/qa/2026-08-30-format-installer-linux.md` (lokal-only).
Versi singkat: satu binary, tanpa build per-distro, CI-friendly.
WebKitGTK **tidak** dibundel — lihat "System prerequisites" di atas;
fallback yang sama berlaku.

```
./packaging/linux/build-in-container.sh   # → packaging/dist-container/dist/netgeo
./packaging/linux/build-appimage.sh       # → packaging/NetGeo-x86_64.AppImage
```

`build-appimage.sh` mengunduh `linuxdeploy` + `linuxdeploy-plugin-appimage`
di jalan pertamanya (continuous release GitHub, total ~36 MB, di-cache
di `packaging/linux/.appimage-tools/` — gitignored). Dari situ dia
membangun `AppDir` secara manual di sekitar bundle onedir: script
`AppRun` tinggal meng-exec binary `netgeo` yang sudah ada di tempatnya.
Lib milik onedir itu sendiri sudah dipin ke glibc-2.35 dan
self-contained, jadi pengejaran dependency oleh `linuxdeploy` sengaja
dilewati — kalau dipaksakan, itu cuma berisiko menimpanya dengan `.so`
sistem yang tidak cocok. Langkah terakhir, `--appimage-extract-and-run`
milik plugin-nya dipakai untuk mengompresnya, karena environment ini
tidak punya jaminan FUSE mount untuk AppImage bersarang (rootless,
tanpa sudo) — extract-and-run menghindari itu.

**Ukuran output: 27 MB** (`NetGeo-x86_64.AppImage`, 28 023 288 byte).

**Tiga pertanyaan yang di design doc masih BELUM TERVERIFIKASI — ditutup
di sini dengan build nyata + dua run nyata (desktop Fedora 44, VM
headless Ubuntu 24.04):**

1. *Apakah PyInstaller/AppImage membundel `gi` + typelib WebKit2, atau
   tetap bergantung pada `.so` sistem?* **Tetap bergantung —
   dikonfirmasi, bukan dugaan.**
   `find packaging/dist-container/dist/netgeo -iname '*.typelib'` tidak
   mengembalikan apa pun, dan extension `_gi*.so` juga tidak dibundel.
   Analisis statis PyInstaller cuma menarik stub `gi/__init__.py` yang
   pure-Python — file .py itu portable jadi ikut terbawa — bukan binary
   introspection yang sudah dikompilasi atau file data `.typelib` apa
   pun. Menjalankan binary hasil build mereproduksi ini persis sama,
   baik di host Fedora (yang *punya* `webkit2gtk4.1` + `python3-gobject`
   terinstal) maupun VM Ubuntu headless (yang tidak punya):
   `ImportError: cannot import name '_gi' from partially initialized
   module 'gi'`.
2. *Apakah `python3-gi` dari apt Ubuntu 22.04 (dibangun untuk python3.10
   default-nya) benar-benar bisa di-import dari venv
   versi-minor-berbeda lewat `--system-site-packages`?* **Tidak —
   dikonfirmasi salah, dan root cause-nya ketemu: mismatch ABI
   C-extension CPython, bukan kelalaian packaging.** Diisolasi di
   container sekali-pakai: `python3.10 -m venv --system-site-packages`
   bisa import `gi` dan resolve `WebKit2-4.1.typelib` dari system path
   tanpa masalah (`OK 3.10 venv: <IntrospectionModule 'WebKit2' from
   '/usr/lib/x86_64-linux-gnu/girepository-1.0/WebKit2-4.1.typelib'>`).
   Tapi package apt yang *sama*, kalau di-import dari `python3.11 -m
   venv --system-site-packages`, malah gagal dengan `ImportError`
   `_gi` yang identik dengan yang muncul di bundle sungguhan.
   `build-in-container.sh` build pakai python3.11 (yang terbaru di
   repo `ubuntu:22.04` sendiri — lihat komentar header-nya), selisih
   satu versi minor dari build python3.10 milik `python3-gi` apt.
   Selisih itu saja sudah fatal untuk jendela native, lepas dari soal
   bundling typelib. Ini sengaja tidak diubah di sini: mengganti build
   ke python3.10 untuk menutup celah ini memang perbaikan nyata dan
   bertarget, tapi instruksinya jangan memaksakan bundling yang rapuh
   cuma supaya kelihatan selesai — fallback browser yang sudah ada
   sudah menutupinya dengan benar.
3. *Ukuran akhir AppImage?* **27 MB**, lihat di atas.

**Terverifikasi jalan, dua arah, 2026-08-30 (pola dua-mesin yang sama
seperti tarball di atas):**
- **Fedora 44 (desktop Wayland, `webkit2gtk4.1`/`gtk3`/`python3-gobject`
  terinstal)**: AppImage-nya FUSE-mount langsung (tidak butuh
  `--appimage-extract-and-run` — FUSE mount sungguhan tersedia di
  sini), `netgeo-bundle/netgeo` start, `_try_webview` gagal karena
  alasan ABI di atas dan mencetak hint instalasi Fedora,
  `webbrowser.open()` membuka **tab Firefox sungguhan** — `GET /` →
  200, JS/CSS asset → 200, `GET /api/auth/setup` → 200.
- **VM Ubuntu 24.04 (headless, `superadmin@100.72.83.91`,
  `webkit2gtk`/`libgtk-3-0`/`gir1.2-webkit2-4.1` tidak terinstal,
  `python3-gi` ada untuk python3.12 miliknya sendiri)**: disalin lewat
  scp, FUSE-mount dengan cara yang sama, mencetak hint instalasi
  **Ubuntu/Debian** yang benar (bukan yang Fedora — code path
  `launcher.py` yang sama memilih pesan yang tepat, ini bukan khusus
  AppImage), backend melayani `/api/health` → 200 dan `/` → 200 tanpa
  jendela (headless, sesuai ekspektasi).

Belum diuji: distro dengan `webkit2gtk`/`gtk3`/`python3-gobject` ada
*dan* python3 default-nya di 3.11 (butuh supaya cek ABI di atas
benar-benar lolos) — tidak ada yang tersedia di sesi ini.

### Windows — sudah ditulis, BELUM diuji (tidak ada mesin Windows yang terjangkau dari sini)

```
cd packaging && pyinstaller netgeo.spec
cd windows && iscc netgeo.iss
```

Menghasilkan `packaging/windows/dist-installer/netgeo-<version>-setup.exe`,
di mana `<version>` adalah `APP_VERSION` milik
`backend/app/core/config.py`. CI mengirimnya lewat
`iscc /DMyAppVersion=...`; kalau `iscc netgeo.iss` dijalankan manual
tanpa override, dia jatuh ke konstanta di `netgeo.iss` yang bisa saja
basi — jadi kirim `/DMyAppVersion=` secara eksplisit untuk build manual.
Installer-nya terinstal ke `%LOCALAPPDATA%\NetGeo`, shortcut Start Menu
+ Desktop opsional, mendaftarkan uninstaller, `PrivilegesRequired=lowest`
(tanpa prompt admin). CI sekarang build ini di `windows-latest` (Inno
Setup sudah terpasang di image runner itu) dan mengunggahnya sebagai
`netgeo-installer-windows-unsigned`. **Surya perlu menguji ini di mesin
Windows sungguhan** — double-click, konfirmasi alur SmartScreen "Run
anyway", entry Start Menu, pembersihan uninstall — sebelum ini dianggap
terverifikasi.

## Postgres / Redis — sudah diselidiki, BUKAN blocker

`backend/app/core/config.py` mendeklarasikan `DATABASE_URL` dan
`REDIS_URL`, tapi keduanya tidak dibaca di mana pun lagi di `app/` —
sudah di-grep, nol hit di luar `config.py`. `get_repo()` di
`app/store/__init__.py` selalu mengembalikan `MemoryRepository`
in-memory; `app/store/postgres.py` ada tapi tidak pernah di-import. Hook
`lifespan` FastAPI di `main.py` tidak membuka koneksi DB atau Redis apa
pun. Jadi backend-nya **sudah jalan standalone**: tidak butuh Docker,
Postgres, atau Redis untuk D1. Ini dikonfirmasi dengan menjalankan
`launcher.py` (dan binary PyInstaller-nya) langsung di mesin ini tanpa
satu pun infra service yang hidup.

State persistence: `NETGEO_STATE_STORE` (default
`~/.config/netgeo/auth.json`, bersebelahan dengan `state.json`) sudah
mem-persist `MemoryRepository` ke file JSON lintas restart kalau di-set —
ini mekanisme S2 PERSIST-01 yang sudah ada, bukan sesuatu yang
ditambahkan di slice ini.

Env var `NETGEO_NO_BROWSER=1` melewati langkah auto-buka-browser
sepenuhnya (dipakai untuk smoke test non-interaktif yang tidak perlu
membuka apa pun — tanpa jendela, tanpa browser, API saja). Perilaku
unset/default tidak berubah.

## Mode headless (varian distribusi #4)

Dari lima bentuk distribusi NetGeo (native full-offline, native+Google
Maps, native+remote backend, **headless**, full-online — lihat vault
`netgeo-distribusi-lima-bentuk`, lokal-only), headless itu: backend
jalan lokal persis seperti biasa, tapi UI-nya terbuka di **browser**
sistem, bukan di jendela native pywebview. Sebelum flag ini ada, itu
cuma jalur fallback yang kebetulan — yang terjadi kalau WebKitGTK/Qt
gagal start. Sekarang ini pilihan eksplisit:

```
python packaging/launcher.py --no-window     # flag
NETGEO_NO_WINDOW=1 python packaging/launcher.py   # env var — untuk systemd
                                                   # unit/container yang
                                                   # merepotkan untuk
                                                   # passing argv, konvensi
                                                   # on/off yang sama
                                                   # seperti NETGEO_NO_BROWSER
```

Baris log-nya membedakan *kenapa* jendelanya dilewati, dengan sengaja:

- Diminta: `[netgeo-launcher] headless: native window skipped by
  request (--no-window) — opening system browser.`
- WebKitGTK/Qt benar-benar gagal start (tidak diminta): pesan
  `_webview_unavailable` yang sudah ada — "jendela aplikasi asli tidak
  tersedia (...)" ditambah hint instalasi per-distro.

Meminta `--no-window`/`NETGEO_NO_WINDOW=1` sama sekali tidak pernah
mencoba jendela native, jadi kegagalan webview sungguhan tidak pernah
tersamar sebagai "user yang minta ini." `NETGEO_NO_BROWSER=1` tetap
menang kalau keduanya di-set — dia melewati pembukaan apa pun (jendela
atau browser), yang memang diinginkan CI/smoke test.

## Wilayah peta offline (OFFLINE-MAP-3)

Backend bisa melayani basemap tile dari file MBTiles lokal, bukan dari
internet (`backend/app/services/offline_maps.py`, `GET /api/maps/status`
+ `/api/maps/tiles/{z}/{x}/{y}`). Frontend berpindah ke situ otomatis
begitu ada yang terinstal, dan fallback ke tile online kapan pun file
itu tidak ada (lihat docstring modul itu untuk aturan fallback
persisnya — file hilang atau rusak, dua-duanya berarti "online").

**Lokasi file yang dibaca backend secara default:**
`~/.config/netgeo/offline-map.mbtiles` (`NETGEO_OFFLINE_MAP_PATH` di
`backend/app/core/config.py`; path yang sama di Windows, di-resolve
lewat `%USERPROFILE%\.config\netgeo\offline-map.mbtiles` — `~` di sana
juga cuma expand ke home user).

**Tidak ada paket wilayah kurasi yang diunduh otomatis.** Belum ada
hosting untuk itu hari ini — mengarang-ngarang download server di sini
itu tidak jujur, dan janji yang tidak ditepati lebih buruk daripada
tidak punya fitur sama sekali. Kedua installer sebagai gantinya
menawarkan dua hal yang memang benar-benar nyata sekarang:

- **Bawa file sendiri** — kamu sudah punya `.mbtiles` (dibuat dengan
  `mbutil`/`tippecanoe`/`planetiler`/export QGIS, atau dikasih orang
  lain) dan mau installer-nya menaruhnya.
- **URL yang kamu sediakan sendiri** — installer-nya fetch persis URL
  itu (`curl` di Linux, `Invoke-WebRequest` di Windows) dan tidak ada
  yang lain; tidak pernah menghubungi host bawaan apa pun.

**CLI Linux** (`packaging/linux/install.sh`):

```
./install.sh --offline-map=/path/to/region.mbtiles   # salin file yang sudah kamu punya
./install.sh --offline-map-url=https://example.org/region.mbtiles
./install.sh --no-offline-map                         # skip, tanpa prompt
./install.sh                                          # prompt interaktif
                                                        # kalau dijalankan di terminal;
                                                        # dilewati diam-diam
                                                        # (default aman) kalau
                                                        # dijalankan non-interaktif
                                                        # (tanpa tty, misalnya CI)
```

**GUI Windows** (`packaging/windows/netgeo.iss`, Inno Setup): halaman
wizard berjudul "Peta Offline (Opsional)" menawarkan tiga pilihan yang
sama (skip / file lokal / URL), default ke skip. Sintaksnya konsisten
dengan sisa script-nya (`CreateInputOptionPage`/`CreateInputFilePage`/
`CreateInputQueryPage`, fungsi pendukung standar Inno Pascal Script) —
**belum diuji-jalan**, karena Inno Setup cuma jalan di Windows dan tidak
ada yang terjangkau dari mesin dev ini (limitasi yang sama seperti sisa
installer, lihat di atas).

**Memasang file sendiri belakangan, atau menghapusnya:** tidak butuh
installer — cukup taruh atau hapus file-nya di path di atas:

```
mkdir -p ~/.config/netgeo
cp my-region.mbtiles ~/.config/netgeo/offline-map.mbtiles   # install
rm ~/.config/netgeo/offline-map.mbtiles                      # hapus -> balik ke tile online
```

`uninstall.sh` sengaja tidak pernah menyentuh `~/.config/netgeo/` (itu
data user, sama seperti project/state tersimpan), jadi peta offline yang
terpasang selamat dari siklus uninstall/reinstall kecuali dihapus
manual.

## Belum dikerjakan (sengaja di luar scope)

- Belum ada `.deb`/`.rpm` untuk Linux (AppImage sudah ada sekarang,
  lihat "Linux — AppImage" di atas), belum ada build macOS.
- Belum ada code signing (installer dan binary Windows unsigned; lihat
  step yang dimatikan di `desktop.yml` dan
  `docs/qa/code-signing-native-distribution`).
- Belum ada tray icon, belum ada auto-start, belum ada wiring
  auto-update.
- Belum ada mode onefile (onedir dipilih untuk startup lebih cepat /
  debugging lebih mudah).
- `launcher.py` selalu memilih port bebas secara acak; tidak ada flag
  `--port`, tidak ada file config, tidak ada single-instance lock.
