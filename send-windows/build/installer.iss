; Riplox Send - installer.
;
; Per-user, like Riplox Desktop: no administrator rights, and nothing written
; outside the user's own profile.

#define AppName      "Riplox Send"
#define AppVersion   "1.0.1"
#define AppExe       "RiploxSend.exe"
#define Publisher    "XniperBuilds"

[Setup]
AppId={{B7C1A5E2-3F44-4D8B-9E17-5A6C2D0F91AB}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL=https://xniperbuilds.com
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist_installer
OutputBaseFilename=RiploxSend_Setup_v{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\src\ui\riploxsend.ico
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile=..\TERMS.txt

[Messages]
; Inno calls this page "License Agreement", which reads like something is
; being sold. It is terms, and it is free.
WizardLicense=Terms and Conditions
LicenseLabel=Please read the following before continuing.
LicenseLabel3=Read the terms below. You must accept them to install {#AppName}.
LicenseAccepted=I &accept the terms
LicenseNotAccepted=I &do not accept the terms

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start with Windows, so the shortcut always works"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\RiploxSend\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\TERMS.txt"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Wipe the previous payload first. Files left behind from an older build have
; caused boot crashes in past releases of ours.
Type: filesandordirs; Name: "{app}\_internal"

[Registry]
; The pairing link, handed to this app instead of to a browser. Opening it in a
; browser pairs the browser, and the code is single-use - so the app it was
; meant for gets nothing. HKCU only: per-user, no administrator, and removed
; with the app.
Root: HKCU; Subkey: "Software\Classes\riploxsend"; ValueType: string; ValueName: ""; ValueData: "URL:Riplox Send"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\riploxsend"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\riploxsend\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKCU; Subkey: "Software\Classes\riploxsend\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
