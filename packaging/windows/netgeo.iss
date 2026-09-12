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
; CI passes the real version via `iscc /DMyAppVersion=<x.y.z> netgeo.iss`,
; read from backend/app/core/config.py (the source of truth) — see
; .github/workflows/desktop.yml. This fallback only fires on a manual build
; with no /D flag; keep it reasonably current, but it is never what a
; release ships (CI always overrides it).
#ifndef MyAppVersion
  #define MyAppVersion "1.2.122"
#endif
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

; --- Offline map region choice (OFFLINE-MAP-3) --------------------------
; NetGeo can read basemap tiles from a local MBTiles file instead of the
; internet (see backend/app/services/offline_maps.py). There is no hosted,
; curated "region list" to offer here — building one would mean inventing a
; download server that doesn't exist. So the wizard page below offers the
; two honest options: point at a .mbtiles file the user already has, or
; type in a URL they choose themselves (nothing hardcoded). Destination is
; %USERPROFILE%\.config\netgeo\offline-map.mbtiles — the same path
; Path("~/.config/netgeo/offline-map.mbtiles").expanduser() resolves to on
; Windows (NETGEO_OFFLINE_MAP_PATH default, backend/app/core/config.py).
[Files]
Source: "{code:GetOfflineMapSourceFile}"; DestDir: "{%USERPROFILE}\.config\netgeo"; DestName: "offline-map.mbtiles"; Flags: external skipifsourcedoesntexist; Check: OfflineMapFileChosen

[Code]
var
  MapChoicePage: TInputOptionWizardPage;
  MapFilePage: TInputFileWizardPage;
  MapUrlPage: TInputQueryWizardPage;

const
  MapChoiceSkip = 0;
  MapChoiceFile = 1;
  MapChoiceUrl = 2;

procedure InitializeWizard;
begin
  MapChoicePage := CreateInputOptionPage(wpSelectTasks,
    'Peta Offline (Opsional)',
    'Pilih sumber peta offline (.mbtiles), atau lewati untuk memakai peta online.',
    'Belum ada paket region siap-unduh yang kami sediakan otomatis. Pakai ' +
    'berkas .mbtiles yang sudah kamu punya, atau URL yang kamu tentukan ' +
    'sendiri. Tanpa pilihan, NetGeo memakai peta online (default aman).',
    True, False);
  MapChoicePage.Add('Lewati - pakai peta online (default)');
  MapChoicePage.Add('Pasang dari berkas .mbtiles lokal');
  MapChoicePage.Add('Unduh dari URL yang saya tentukan sendiri');
  MapChoicePage.SelectedValueIndex := MapChoiceSkip;

  MapFilePage := CreateInputFilePage(MapChoicePage.ID,
    'Pilih Berkas Peta Offline',
    'Pilih berkas .mbtiles yang akan dipasang.',
    'Berkas ini akan disalin ke %USERPROFILE%\.config\netgeo\offline-map.mbtiles');
  MapFilePage.Add('Berkas .mbtiles:', 'MBTiles files|*.mbtiles|All files|*.*', '.mbtiles');

  MapUrlPage := CreateInputQueryPage(MapFilePage.ID,
    'URL Paket Peta Offline',
    'Masukkan URL langsung ke berkas .mbtiles.',
    'NetGeo hanya menghubungi URL yang kamu masukkan di sini sendiri - ' +
    'tidak ada server bawaan yang dipanggil.');
  MapUrlPage.Add('URL (.mbtiles):', False);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = MapFilePage.ID) and (MapChoicePage.SelectedValueIndex <> MapChoiceFile) then
    Result := True;
  if (PageID = MapUrlPage.ID) and (MapChoicePage.SelectedValueIndex <> MapChoiceUrl) then
    Result := True;
end;

function OfflineMapFileChosen: Boolean;
begin
  Result := (MapChoicePage.SelectedValueIndex = MapChoiceFile) and (MapFilePage.Values[0] <> '');
end;

function GetOfflineMapSourceFile(Param: String): String;
begin
  if OfflineMapFileChosen then
    Result := MapFilePage.Values[0]
  else
    Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DestDir, DestFile, Url: String;
  ResultCode: Integer;
begin
  if (CurStep = ssPostInstall) and (MapChoicePage.SelectedValueIndex = MapChoiceUrl) then
  begin
    Url := Trim(MapUrlPage.Values[0]);
    if Url <> '' then
    begin
      DestDir := ExpandConstant('{%USERPROFILE}\.config\netgeo');
      ForceDirectories(DestDir);
      DestFile := DestDir + '\offline-map.mbtiles';
      Exec(ExpandConstant('{cmd}'), '/C powershell -NoProfile -Command "Invoke-WebRequest -Uri ''' + Url + ''' -OutFile ''' + DestFile + '''"',
        '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;
