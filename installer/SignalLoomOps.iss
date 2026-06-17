#define AppName "SignalLoomOps"
#define AppVersion "1.0.0"
#define Publisher "SALT19 LLC"
#define AppExe "SignalLoomOps.exe"

[Setup]
AppId={{C81D2F7B-9FC9-43A3-B7BC-9A3E9C4172A7}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=SignalLoomOps_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\signalloom.ico
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile=license.txt
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\dist\SignalLoomOps\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SignalLoomOps"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\SignalLoomOps"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch SignalLoomOps"; Flags: nowait postinstall skipifsilent
