; Inno Setup script for DocCipher Breaker.
; Build the exe first (build.ps1), then compile this with Inno Setup 6:
;   iscc installer\setup.iss

#define AppName        "DocCipher Breaker"
#define AppVersion     "1.0.1"
#define AppPublisher   "Achu Vijayakumar"
#define AppExeName     "DocCipherBreaker.exe"
#define AppCopyright   "Copyright (C) 2026 Achu Vijayakumar"
#define EduNotice      "FOR EDUCATIONAL PURPOSES ONLY"

[Setup]
AppId={{8E2A4C61-5F3D-4B7A-9C18-2D6E1F0A7B34}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright={#AppCopyright}
VersionInfoDescription={#AppName} - {#EduNotice}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
SetupIconFile=..\assets\icon.ico
OutputDir=..\dist_installer
OutputBaseFilename=DocCipherBreaker_Setup_{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; Skull branding shown throughout the wizard.
WizardImageFile=..\assets\wizard_large.bmp
WizardSmallImageFile=..\assets\wizard_small.bmp

; The educational notice must be accepted before anything is installed.
LicenseFile=EULA.txt

; x64 only: the PyInstaller build is 64-bit.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-machine install so the context menu is available to all users.
PrivilegesRequired=admin
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; Reinforce the notice in the wizard chrome itself.
BeveledLabel={#AppName} {#AppVersion}  |  Created by {#AppPublisher}  |  {#EduNotice}

[Tasks]
Name: "desktopicon";  Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "contextmenu";  Description: "Add ""Unlock with DocCipher Breaker"" to the .docx and .pdf right-click menus"; GroupDescription: "Integration:"

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";          DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "EULA.txt";              DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\logo.svg";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\logo.png";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\icon.ico";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE";            DestDir: "{app}"; Flags: ignoreversion
; update.bat reads version.txt to decide whether a newer release exists, so the
; two must be installed together or the updater cannot run.
Source: "..\update.bat";         DestDir: "{app}"; Flags: ignoreversion
Source: "..\version.txt";        DestDir: "{app}"; Flags: ignoreversion
; Optional: drop a qpdf build into tools\qpdf\ before compiling and it is
; bundled and preferred for PDFs. PyMuPDF is compiled into the executable and
; handles PDFs on its own, so this is not required.
Source: "..\tools\qpdf\*";       DestDir: "{app}\tools\qpdf"; \
    Flags: ignoreversion recursesubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}";             Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{group}\License and Disclaimer"; Filename: "{app}\EULA.txt"
Name: "{group}\Uninstall {#AppName}";   Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";       Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Registry]
; Scope the context menu to Word documents only -- registering under "*" puts
; the entry on every file type on the system, which is user-hostile.
Root: HKLM; Subkey: "SOFTWARE\Classes\SystemFileAssociations\.docx\shell\DocCipherBreaker"; \
    ValueType: string; ValueName: ""; ValueData: "Unlock with DocCipher Breaker"; \
    Flags: uninsdeletekey; Tasks: contextmenu
Root: HKLM; Subkey: "SOFTWARE\Classes\SystemFileAssociations\.docx\shell\DocCipherBreaker"; \
    ValueType: string; ValueName: "Icon"; ValueData: "{app}\icon.ico"; \
    Tasks: contextmenu
Root: HKLM; Subkey: "SOFTWARE\Classes\SystemFileAssociations\.docx\shell\DocCipherBreaker\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; \
    Flags: uninsdeletekey; Tasks: contextmenu

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
    Flags: postinstall nowait skipifsilent

; Note: history and unlocked output live under each user's %LOCALAPPDATA%.
; The uninstaller runs elevated, so it cannot reliably reach the profile of the
; user who actually ran the app -- those files are deliberately left in place.
