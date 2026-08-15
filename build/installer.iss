; Riplox Desktop installer (Inno Setup 6)
; Build the app first:  pyinstaller build\riplox.spec --noconfirm

#define AppName      "Riplox"
#define AppVersion   "1.3.0"
#define AppPublisher "XniperBuilds"
#define AppURL       "https://xniperbuilds.com"
#define AppExe       "Riplox.exe"
; Native messaging. The extension id is fixed by the "key" in the extension's
; manifest, so it stays the same wherever the folder is loaded from - which is
; what lets this file name it in advance.
#define HostName     "com.xniperbuilds.riplox"
#define ExtensionId  "eceoennjnigbildembfcpdlmiaahocnm"

[Setup]
AppId={{9C2F41B7-6E4A-4C58-9B21-7D3E5A0F81C4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=..\dist_installer
OutputBaseFilename=Riplox_Setup_v{#AppVersion}
SetupIconFile=..\src\static\img\riplox.ico
UninstallDisplayIcon={app}\{#AppExe}
; Shown as a page the user has to accept before anything is written. The file
; is installed alongside the app as well, so it can be read again later.
LicenseFile=..\TERMS.txt
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; Per-user install by default: no admin prompt, and the app can update its
; own download engine afterwards.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; Inno calls this page "License Agreement", which reads like a purchase.
; It is a set of terms about what the program does and what it does not
; promise, so it should say that.
[Messages]
WizardLicense=Terms and Conditions
LicenseLabel=Please read the following terms before continuing.
LicenseLabel3=Please read the following Terms and Conditions. You must accept them before Riplox can be installed.
LicenseAccepted=I &accept the terms
LicenseNotAccepted=I &do not accept the terms

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; bin is excluded here and added from the repo below, so it is not compressed
; into the installer twice - the dist copy exists only for local test runs.
Source: "..\dist\Riplox\*"; DestDir: "{app}"; Excludes: "bin\*"; Flags: ignoreversion recursesubdirs createallsubdirs
; The browser extension, shipped unpacked so it can be loaded from here. It is
; a few kilobytes of text and icons, and keeping it beside the app is what makes
; "load this folder" an instruction anyone can follow.
Source: "..\browser-extension\*"; DestDir: "{app}\browser-extension"; Flags: ignoreversion recursesubdirs createallsubdirs
; ffmpeg is a shared build - the exe and its DLLs must stay in one folder.
; recursesubdirs because yt-dlp ships as a folder now (yt-dlp.exe beside its
; _internal): the single-file build unpacked itself into temp on every run,
; which was 1.4 seconds before any request went out.
Source: "..\bin\*"; DestDir: "{app}\bin"; Flags: ignoreversion recursesubdirs createallsubdirs
; The terms the user accepted, and the licence they were told about, both
; readable after the fact rather than only during setup.
Source: "..\TERMS.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[InstallDelete]
; Wipe the previous payload before writing the new one. Leftover files from an
; older build have caused boot crashes in past releases of our other apps.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\*.pyd"
Type: files; Name: "{app}\*.dll"
; The single-file engine this build replaced. Left behind it is 17 MB of a
; yt-dlp nothing runs any more.
Type: files; Name: "{app}\bin\yt-dlp.exe"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; HKA rather than HKCU throughout: Inno writes to HKLM when Riplox was
; installed for everyone and to HKCU when it was installed for one person, so
; the registration reaches exactly as far as the install did. Written per-user
; on a shared PC, a second Windows account would have found Riplox in Program
; Files with neither of these routes working and no way to tell why.

; riplox:// - what the browser extension hands a link over with, and what opens
; Riplox when it is closed.
Root: HKA; Subkey: "Software\Classes\riplox"; ValueType: string; ValueName: ""; ValueData: "URL:Riplox"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\riplox"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\riplox\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\riplox\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""

; Native messaging: where each browser looks for the description of a program it
; is allowed to talk to. Chrome asks the user before opening a riplox:// link
; and newer versions no longer offer "always allow", so that route costs a click
; on every download. This one costs none - the browser was told at install time.
Root: HKA; Subkey: "Software\Google\Chrome\NativeMessagingHosts\{#HostName}"; ValueType: string; ValueName: ""; ValueData: "{app}\native-host.json"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\{#HostName}"; ValueType: string; ValueName: ""; ValueData: "{app}\native-host.json"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\{#HostName}"; ValueType: string; ValueName: ""; ValueData: "{app}\native-host.json"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\RiploxDesktop"
Type: files; Name: "{app}\native-host.json"

[Code]
{ The window is WebView2-based. It is present on Windows 11 and on any Windows
  10 with a current Edge, but not guaranteed - so say so plainly rather than
  letting the user meet a blank window. }
function WebView2Installed(): Boolean;
var
  Value: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value);
end;

{ The description Chrome reads before it will talk to RiploxHost.exe. Written
  here rather than shipped as a file because it has to carry the install path,
  which is not known until now. JSON needs its backslashes doubled. }
procedure WriteNativeHostManifest();
var
  Path, Json: String;
begin
  Path := ExpandConstant('{app}\RiploxHost.exe');
  StringChangeEx(Path, '\', '\\', True);
  Json :=
    '{' + #13#10 +
    '  "name": "{#HostName}",' + #13#10 +
    '  "description": "Riplox",' + #13#10 +
    '  "path": "' + Path + '",' + #13#10 +
    '  "type": "stdio",' + #13#10 +
    '  "allowed_origins": [ "chrome-extension://{#ExtensionId}/" ]' + #13#10 +
    '}' + #13#10;
  SaveStringToFile(ExpandConstant('{app}\native-host.json'), Json, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteNativeHostManifest();
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
  begin
    if MsgBox('Riplox needs the Microsoft Edge WebView2 Runtime, which is not installed on this PC.' + #13#10 + #13#10 +
              'It is a free Microsoft component. Install it from microsoft.com (search "WebView2 Runtime"), then run this setup again.' + #13#10 + #13#10 +
              'Continue anyway?', mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;
