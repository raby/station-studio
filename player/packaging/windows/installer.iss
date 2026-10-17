; Inno Setup script for the Windows download: installs the PyInstaller
; build (dist\Station Player) for the current user, with no administrator
; password, and adds Station Player to the Start menu.
;
;   iscc /DVersion=0.9.1 /DSource="...\dist\Station Player" /DOutput=...\out installer.iss

#ifndef Version
  #error Pass /DVersion=...
#endif

[Setup]
AppId={{6E0B1F4C-7A52-4C1D-9C1E-3D5B8F2A9E47}
AppName=Station Player
AppVersion={#Version}
AppVerName=Station Player {#Version}
AppPublisher=Rob (raby)
AppPublisherURL=https://github.com/raby/station-studio
DefaultDirName={localappdata}\Programs\Station Player
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={#Output}
OutputBaseFilename=StationPlayer-{#Version}-windows-setup
SetupIconFile={#Source}\_internal\station_player\static\icon.ico
UninstallDisplayIcon={app}\Station Player.exe
UninstallDisplayName=Station Player
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; A running Station Player is closed so its files can be replaced.
CloseApplications=force
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: desktopicon; Description: "Put Station Player on the desktop"

[InstallDelete]
; Files from the version installed before, which the new one may not have.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\Station Player"; Filename: "{app}\Station Player.exe"
Name: "{userdesktop}\Station Player"; Filename: "{app}\Station Player.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Station Player.exe"; Description: "Open Station Player now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Takes Station Player out of Open With and start-at-login.
Filename: "{app}\Station Player.exe"; Parameters: "--remove-shortcuts"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveShortcuts"
; Then stop a running player, so its files can be deleted.
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im ""Station Player.exe"""; Flags: runhidden waituntilterminated; RunOnceId: "StopPlayer"
