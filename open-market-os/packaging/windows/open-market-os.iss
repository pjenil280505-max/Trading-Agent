; Windows installer for Open Market OS (Inno Setup 6).
; Per-user install (no administrator rights needed) into %LOCALAPPDATA%\Programs\Open Market OS.
; User data lives separately in %LOCALAPPDATA%\OpenMarketOS and is kept on uninstall unless the user
; chooses to delete it.
; Build: ISCC.exe /DAppVersion=0.2.0 packaging\windows\open-market-os.iss   (after packaging\build_app.py)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Open Market OS"
#define AppExe "Open Market OS.exe"

[Setup]
AppId={{6B1E3C52-9A4D-4E8B-8F1E-2C7A5D0B9E41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Open Market OS contributors
AppPublisherURL=https://github.com/pjenil280505-max/Trading-Agent
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\releases
OutputBaseFilename=OpenMarketOS-{#AppVersion}-windows-x64-setup
SetupIconFile=..\icons\omos.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
LicenseFile=..\..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start Open Market OS in the background when I sign in (keeps bots and alerts running)"; GroupDescription: "Background:"; Flags: unchecked

[Files]
Source: "..\..\dist\Open Market OS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Charts, research, strategy testing, paper trading and bots"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\omos.exe"; Parameters: "autostart on --quiet"; Flags: runhidden; Tasks: autostart; StatusMsg: "Setting up start at sign-in..."
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\omos.exe"; Parameters: "stop --quiet"; Flags: runhidden waituntilterminated; RunOnceId: "StopApp"
Filename: "{app}\omos.exe"; Parameters: "autostart off --quiet"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveAutostart"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  { An update replaces files the running app uses: stop it cleanly first (bots resume on the next start). }
  if FileExists(ExpandConstant('{app}\omos.exe')) then
    Exec(ExpandConstant('{app}\omos.exe'), 'stop --quiet', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\OpenMarketOS');
    if DirExists(DataDir) and not UninstallSilent() then
      if MsgBox('Open Market OS was removed.' + #13#10 + #13#10 +
                'Your data (strategies, paper accounts, settings, backups and any downloaded AI models) is still in:' + #13#10 +
                DataDir + #13#10 + #13#10 +
                'Delete it too? Choose No to keep it for a later reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
