; NetGeo Windows installer (Inno Setup) — packages the PyInstaller onedir
; bundle (packaging/dist/netgeo/) into a per-user installer with Start Menu
; + optional Desktop shortcuts and a proper uninstaller.
;
; ponytail: Inno Setup chosen over NSIS — one declarative .iss file covers
; install/uninstall/shortcuts/registry for a plain onedir copy, no scripting
; needed. PrivilegesRequired=lowest + a per-user install dir means no admin
; prompt and no elevation dance.
;
; Build (on Windows, with Inno Setup 6 installed / ISCC.exe on PATH):
;   iscc packaging\windows\netgeo.iss
; Output: packaging\windows\dist-installer\netgeo-<version>-setup.exe
;
; NOT YET TESTED on a real Windows machine — see packaging/README.md.

#define MyAppName "NetGeo"
#define MyAppVersion "1.2.99"
#define MyAppExeName "netgeo.exe"
#define MyBundleDir "..\dist\netgeo"

[Setup]
AppId={{6C9C6F2B-6E7B-4A2F-9E7E-3D0F6B9E9A7A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; No admin rights required: installs under the current user's LOCALAPPDATA.
PrivilegesRequired=lowest
OutputDir=dist-installer
OutputBaseFilename=netgeo-{#MyAppVersion}-setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=..\icons\netgeo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Onedir bundle contents (netgeo.exe + _internal/) — must be built first via
; `pyinstaller netgeo.spec` (see packaging/README.md).
Source: "{#MyBundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

; ponytail: no registry Run key / autostart, no file associations, no
; per-machine install — out of scope per the task brief (auto-start is a
; deferred desktop feature; multi-user machine installs aren't requested).
