; ─────────────────────────────────────────────────────────────────────────────
; ICE-Setup.iss — the Windows installer for ICE JARVIS.
;
; Do not compile this by hand: `python installer\build_installer.py` freezes the
; app with PyInstaller first, then calls ISCC with the right defines. The only
; thing this file assumes is a finished payload in ..\dist\JARVIS.
;
; Design decisions worth knowing before you edit it:
;
;   * Per-user install ({localappdata}\Programs\JARVIS), no UAC prompt. JARVIS
;     writes config/, memory/ and logs/ beside its own exe, so it must live
;     somewhere the user can write. Program Files would also work only by
;     redirecting those paths into AppData, which is a bigger change than
;     installing per-user. Normal users install without an administrator.
;   * The payload contains no user data at all (build_installer.py strips
;     config/api_keys.json, config/certs/, logs/ and memory state), so an
;     upgrade can overwrite every shipped file and still touch nothing personal.
;   * The uninstaller asks before deleting settings and memory.
; ─────────────────────────────────────────────────────────────────────────────

#define MyAppName "ICE JARVIS"
#define MyAppPublisher "idkunknown657-cell"
#define MyAppURL "https://github.com/idkunknown657-cell/ICE-JARVIS"
#define MyAppExeName "JARVIS.exe"
#define SourceDir "..\dist\JARVIS"

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MyAppVersionQuad
  #define MyAppVersionQuad "0.0.0.0"
#endif

[Setup]
; A fixed AppId is what makes an upgrade an upgrade instead of a second install —
; never change it.
AppId={{9C1E4A73-5B2F-4C58-9E1D-3A7B6F0C2D84}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
AppComments=Runs entirely on your machine. No account, no telemetry.
VersionInfoVersion={#MyAppVersionQuad}
VersionInfoProductVersion={#MyAppVersionQuad}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoCopyright=MIT Licensed

DefaultDirName={localappdata}\Programs\JARVIS
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
LicenseFile=..\LICENSE
OutputDir=Output
OutputBaseFilename=ICE-Setup
SetupIconFile=..\assets\jarvis.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; A setup log in %TEMP% costs nothing and answers "the installer failed" — there
; is no console to watch.
SetupLogging=yes
; Ask Windows to close a running JARVIS before files are replaced, so an upgrade
; while the app is open is not a half-copied install.
CloseApplications=yes
RestartApplications=no
DisableWelcomePage=no
DisableReadyPage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "browserdeps"; Description: "Download browser automation components (Chromium + Firefox, about 450 MB, one time)"; GroupDescription: "Optional components:"; Flags: unchecked

[Files]
; The app itself. ignoreversion is right here: every payload file is shipped,
; none of them is user data, so the new build always wins.
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Excludes: "config\api_keys.json,config\certs\*,logs\*,__pycache__\*,*.pyc,update_staging\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

#ifdef WebView2Stub
; Microsoft's Evergreen bootstrapper (~1.8 MB), only run when WebView2 is absent.
; JARVIS has no UI at all without it, so it is worth carrying.
Source: "redist\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; \
    Flags: deleteafterinstall; Check: WebView2Missing
#endif

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "Voice AI assistant"
Name: "{group}\{#MyAppName} (debug console)"; Filename: "{app}\JARVIS-Debug.exe"; WorkingDir: "{app}"; Comment: "Same app with a console attached — use this when something goes wrong"
Name: "{group}\{#MyAppName} folder"; Filename: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
#ifdef WebView2Stub
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
    StatusMsg: "Installing the Microsoft Edge WebView2 runtime…"; \
    Check: WebView2Missing; Flags: waituntilterminated
#endif
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install-browser-deps"; \
    StatusMsg: "Downloading browser automation components (one time)…"; \
    Description: "Download browser automation components now"; \
    Flags: runhidden waituntilterminated; Tasks: browserdeps
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Build scratch that must never survive an uninstall. Settings and memory are
; handled in [Code] below, because removing them is the user's choice.
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\update_staging"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\.autostart_flag"
Type: files; Name: "{app}\apply_update.bat"

[Code]
const
  // WebView2's Evergreen runtime client ID — the same GUID Microsoft documents.
  WebView2Client = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

var
  RemoveUserData: Boolean;

function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebView2Client, 'pv', Version) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2Client, 'pv', Version) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2Client, 'pv', Version);
  // A present-but-empty "pv" means an uninstalled runtime, not an installed one.
  Result := Result and (Version <> '');
end;

function WebView2Missing(): Boolean;
begin
  Result := not WebView2Installed();
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  // Without WebView2 the window never appears, and the only sane explanation is
  // given before the install rather than after it.
  if WebView2Missing() then
  begin
    #ifndef WebView2Stub
    if MsgBox('ICE JARVIS draws its interface with the Microsoft Edge WebView2 runtime, '
              + 'which is not installed on this PC.' + #13#10 + #13#10
              + 'A quick machine-wide install is needed; otherwise the app will start and show no window.'
              + #13#10 + #13#10 + 'Continue anyway?', mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      Exit;
    end;
    #endif
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  // MB_DEFBUTTON2 so the *default* answer is "keep my data": a silent or
  // scripted uninstall must never destroy someone's keys and memory by
  // accident, and clicking straight through keeps them too.
  RemoveUserData :=
    MsgBox('Also delete your JARVIS settings, API keys, memory and logs?'
           + #13#10 + #13#10
           + 'Choose No to keep them — a later reinstall picks them straight back up.',
           mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and RemoveUserData then
  begin
    DelTree(ExpandConstant('{app}\config'), True, True, True);
    DelTree(ExpandConstant('{app}\memory'), True, True, True);
    DelTree(ExpandConstant('{app}\plugins'), True, True, True);
  end;
end;
