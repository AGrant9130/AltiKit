; Inno Setup script for the VW3A1111/VW3A1121 Language File Updater.
; Built by the "build-windows-installer" GitHub Actions workflow, which
; runs PyInstaller first (producing dist\VW3ALanguageUpdater\) and then
; compiles this script with ISCC.exe (bundled on GitHub's windows-latest
; runners, so no extra install step is needed in CI).
;
; To build locally instead: run PyInstaller yourself first (see
; packaging/windows/README.md), then open this file in Inno Setup and
; click Compile - paths below are relative to this script's location.

#define MyAppName "VW3A Language Updater"
#define MyAppVersion GetEnv("APP_VERSION")
#if MyAppVersion == ""
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppExeName "VW3ALanguageUpdater.exe"
#define MyDistDir "..\..\dist\VW3ALanguageUpdater"

[Setup]
; Fixed AppId so upgrades/reinstalls are recognized as the same app
; rather than installing side-by-side.
AppId={{26C4BA8A-A02F-4CD0-8E74-09F1BF701837}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\installer_output
OutputBaseFilename=VW3ALanguageUpdater-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; No admin rights required - with PrivilegesRequired=lowest, Inno Setup 6
; switches to a per-user install and remaps the "auto*" constants below
; (AppData\Local\Programs instead of Program Files, etc.), so this works
; on locked-down work laptops without an admin prompt.
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checked

[Files]
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
