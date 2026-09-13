# NetGeo .rpm — repackages the PyInstaller onedir bundle, does not compile
# anything. Paths come in via `rpmbuild --define` (see build-rpm.sh):
#   _app_version, _bundle_dir, _desktop_file, _icons_dir
#
# ponytail: no %prep/%build, no Source0 tarball — %install copies straight
# from the already-built bundle path. Debuginfo/strip/auto-deps are
# disabled because /opt/netgeo ships PyInstaller's own pinned .so files;
# rpm's default post-build policy (strip binaries, scan for Requires: via
# ldd) would either corrupt them or invent bogus system dependencies.
%global debug_package %{nil}
%global __os_install_post %{nil}
%global __requires_exclude_from ^/opt/netgeo/.*$
%global __provides_exclude_from ^/opt/netgeo/.*$

Name: netgeo
Version: %{_app_version}
Release: 1%{?dist}
Summary: Network simulation + GIS/digital-twin platform
License: Apache-2.0
URL: https://github.com/suryaex/netgeo
BuildArch: x86_64
# Fedora/RHEL names for the same Qt/QtWebEngine runtime libs apt installs
# under Debian/Ubuntu names in .github/workflows/desktop.yml.
Requires: mesa-libGL, mesa-libEGL, libxkbcommon, xcb-util-cursor, nss, libXcomposite, libXdamage, libXrandr, libXtst, alsa-lib, at-spi2-core

%description
NetGeo simulates IP/routing networks with a GIS/digital-twin overlay for
physical plant, rack layout, and RF planning.

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/netgeo
cp -r %{_bundle_dir}/. %{buildroot}/opt/netgeo/
mkdir -p %{buildroot}/usr/bin
ln -s /opt/netgeo/netgeo %{buildroot}/usr/bin/netgeo
mkdir -p %{buildroot}/usr/share/applications
install -m644 %{_desktop_file} %{buildroot}/usr/share/applications/netgeo.desktop
for size in 128 256 512; do
    mkdir -p %{buildroot}/usr/share/icons/hicolor/${size}x${size}/apps
    install -m644 %{_icons_dir}/netgeo-${size}.png %{buildroot}/usr/share/icons/hicolor/${size}x${size}/apps/netgeo.png
done

%files
/opt/netgeo
/usr/bin/netgeo
/usr/share/applications/netgeo.desktop
/usr/share/icons/hicolor/*/apps/netgeo.png

%post
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications || :
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || :

%postun
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications || :
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || :
