; Built by build.py: iscc /DAppVersion=<version> installer\TOTStatsMonitor.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "TOTStatsMonitor"
#define AppExe "TOTStatsMonitor.exe"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
; Never change the AppId, it is how Windows recognises an update of this program.
AppId={{2E06C4F3-CC63-4B50-B8F3-F3D3C1D825F8}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=OutlastTrialsStats
AppPublisherURL=https://outlasttrialsstats.com
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=TOTStatsMonitor-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
VersionInfoVersion={#AppVersion}

[Tasks]
; Ticked by default. Autostart is the user's decision, made here, and nowhere else.
Name: "autostart"; Description: "Start {#AppName} when I sign in to Windows"; GroupDescription: "Startup:"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Registry]
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\{#AppExe}"" --silent"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--silent"; Description: "Start {#AppName} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; settings.json, monitor.log and the cached trial list.
Type: filesandordirs; Name: "{localappdata}\{#AppName}"

[Code]
procedure StopRunningInstances();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningInstances();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    // Earlier versions copied themselves into %LOCALAPPDATA%\TOTStatsMonitor and wrote the
    // autostart entry unasked. Remove that copy and both old entries; the task above writes a
    // fresh entry only if the user kept it ticked.
    RegDeleteValue(HKCU, '{#RunKey}', '{#AppName}');
    RegDeleteValue(HKCU, '{#RunKey}', 'OutlastTrialsMonitor');
    DeleteFile(ExpandConstant('{localappdata}\{#AppName}\{#AppExe}'));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // The tray toggle can write this value after the installer ran, so uninsdeletevalue alone
  // is not enough.
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKCU, '{#RunKey}', '{#AppName}');
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningInstances();
  Result := True;
end;
