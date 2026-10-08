#ifndef Edition
  #error Edition required
#endif
[Setup]
AppId=ShirOS-Desktop-{#Edition}
AppName=ShirOS {#Edition}
AppVersion=0.5.0
DefaultDirName={localappdata}\Programs\ShirOS-{#Edition}
DefaultGroupName=ShirOS {#Edition}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Output}
OutputBaseFilename=ShirOS-0.5.0-{#Edition}-Setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\ShirOS.exe
CloseApplications=yes
SetupLogging=yes
[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\ShirOS {#Edition}"; Filename: "{app}\ShirOS.exe"
Name: "{userdesktop}\ShirOS {#Edition}"; Filename: "{app}\ShirOS.exe"
Name: "{group}\使用说明书"; Filename: "{app}\使用说明书.md"
[Run]
Filename: "{app}\ShirOS.exe"; Description: "启动 ShirOS"; Flags: nowait postinstall skipifsilent
