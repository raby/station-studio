; Inno Setup script for the Windows download: installs the PyInstaller
; build (dist\Station Studio) for the current user, with no administrator
; password, and adds Station Studio to the Start menu (and the desktop).
;
;   iscc /DVersion=1.6.0 /DSource="...\dist\Station Studio" /DOutput=...\out installer.iss
;
; Stations, pools, playlists and exports live in %USERPROFILE%\.station-studio,
; which neither updating nor uninstalling touches.

#ifndef Version
  #error Pass /DVersion=...
#endif

[Setup]
AppId={{B3D7A2E1-5C4F-4E8B-9A61-2F0C7D9E4B15}
AppName=Station Studio
AppVersion={#Version}
AppVerName=Station Studio {#Version}
AppPublisher=Rob (raby)
AppPublisherURL=https://github.com/raby/station-studio
DefaultDirName={localappdata}\Programs\Station Studio
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={#Output}
OutputBaseFilename=StationStudio-{#Version}-windows-setup
SetupIconFile={#SourcePath}\..\icon.ico
UninstallDisplayIcon={app}\Station Studio.exe
UninstallDisplayName=Station Studio
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; A running Station Studio is closed so its files can be replaced.
CloseApplications=force
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: desktopicon; Description: "Put Station Studio on the desktop"

[InstallDelete]
; Files from the version installed before, which the new one may not have.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\Station Studio"; Filename: "{app}\Station Studio.exe"
Name: "{userdesktop}\Station Studio"; Filename: "{app}\Station Studio.exe"; Tasks: desktopicon

[Registry]
; station-studio://start: the installed app's window asks for this when
; Station Studio isn't running (see desktop.URL_SCHEME). Per user.
Root: HKCU; Subkey: "Software\Classes\station-studio"; ValueType: string; ValueName: ""; ValueData: "URL:Station Studio"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\station-studio"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\station-studio\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\Station Studio.exe"",0"
Root: HKCU; Subkey: "Software\Classes\station-studio\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Station Studio.exe"" --no-browser"
; Start at login (Settings › App) adds itself here; uninstalling takes it away.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Station Studio"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\Station Studio.exe"; Description: "Open Station Studio now"; Flags: nowait postinstall skipifsilent
; Updating from inside Station Studio runs this setup silently: start the
; new version again (the page that's open reloads into it).
Filename: "{app}\Station Studio.exe"; Parameters: "--no-browser"; Flags: nowait; Check: WizardSilent

[UninstallRun]
; Stop a running Station Studio, so its files can be deleted.
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im ""Station Studio.exe"""; Flags: runhidden waituntilterminated; RunOnceId: "StopStudio"
