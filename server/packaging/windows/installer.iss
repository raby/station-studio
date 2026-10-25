; Inno Setup script for the Windows download: installs the PyInstaller
; build (dist\Station Server) for the current user, with no administrator
; password, and adds Station Server to the Start menu.
;
;   iscc /DVersion=0.3.0 /DSource="...\dist\Station Server" /DOutput=...\out installer.iss

#ifndef Version
  #error Pass /DVersion=...
#endif

[Setup]
AppId={{B3F1C9A2-5D47-4E8B-A6C3-7F2E1D9B4C58}
AppName=Station Server
AppVersion={#Version}
AppVerName=Station Server {#Version}
AppPublisher=Rob (raby)
AppPublisherURL=https://github.com/raby/station-studio
DefaultDirName={localappdata}\Programs\Station Server
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={#Output}
OutputBaseFilename=StationServer-{#Version}-windows-setup
SetupIconFile={#Source}\_internal\station_server\static\icon.ico
UninstallDisplayIcon={app}\Station Server.exe
UninstallDisplayName=Station Server
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; A running Station Server (and the stations it has on air) is closed so its
; files can be replaced; start it again from the last page.
CloseApplications=force
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: desktopicon; Description: "Put Station Server on the desktop"

[InstallDelete]
; Files from the version installed before, which the new one may not have.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\Station Server"; Filename: "{app}\Station Server.exe"
Name: "{userdesktop}\Station Server"; Filename: "{app}\Station Server.exe"; Tasks: desktopicon

[Registry]
; station-server://start: the installed app's window asks for this when
; Station Server isn't running (see __main__.URL_SCHEME). Per user.
Root: HKCU; Subkey: "Software\Classes\station-server"; ValueType: string; ValueName: ""; ValueData: "URL:Station Server"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\station-server"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\station-server\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\Station Server.exe"",0"
Root: HKCU; Subkey: "Software\Classes\station-server\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Station Server.exe"" app --no-window"

[Run]
Filename: "{app}\Station Server.exe"; Description: "Open Station Server now"; Flags: nowait postinstall skipifsilent
; Updating from inside Station Server runs this setup silently: start the
; new version again in the background (the page that's open reloads into
; it, and the stations that were on air go back on air).
Filename: "{app}\Station Server.exe"; Parameters: "app --no-window"; Flags: nowait; Check: WizardSilent

[UninstallRun]
; Stop starting at login.
Filename: "{app}\Station Server.exe"; Parameters: "remove-login"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveLogin"
; Then stop a running Station Server, so its files can be deleted.
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im ""Station Server.exe"""; Flags: runhidden waituntilterminated; RunOnceId: "StopServer"
