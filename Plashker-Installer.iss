; ============================================================
;  Inno Setup — установщик Plashker для Windows 10 и 11
;  Один и тот же установщик работает на обеих версиях.
;
;  Перед сборкой:
;    1) собери приложение PyInstaller-ом -> появится папка dist\Plashker\
;    2) открой этот файл в Inno Setup и нажми Build > Compile (или F9)
;  На выходе: Output\Plashker-Setup.exe
; ============================================================

#define MyAppName "Plashker"
#define MyAppVersion "1.0"
#define MyAppPublisher "Greg"
#define MyAppExeName "Plashker.exe"

[Setup]
; Уникальный ID приложения (можно оставить как есть).
AppId={{B7E2B4A1-9C3D-4E5F-8A12-PLASHKER0001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Ставим в Program Files (обычная папка для программ).
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; Иконка самого установщика и папка вывода.
SetupIconFile=AppIcon.ico
OutputDir=Output
OutputBaseFilename=Plashker-Setup
Compression=lzma2
SolidCompression=yes
; Красивый мастер установки.
WizardStyle=modern
; Разрешаем ставить как для всех, так и без прав админа (для себя).
PrivilegesRequiredOverridesAllowed=dialog
; Только 64-битная Windows (PyInstaller собирает под разрядность Python).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Кладём ВСЮ папку сборки PyInstaller внутрь программы.
; recursesubdirs — вместе со всеми вложенными файлами.
Source: "dist\Plashker\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Иконка для файлов проекта .plshk
Source: "DocIcon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Ярлык в меню Пуск
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; Ярлык удаления
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
; Ярлык на рабочем столе (если поставили галочку)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; --- Ассоциация файлов .plshk с Plashker (двойной клик по проекту открывает приложение) ---
Root: HKA; Subkey: "Software\Classes\.plshk"; ValueType: string; ValueName: ""; ValueData: "Plashker.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Plashker.Project"; ValueType: string; ValueName: ""; ValueData: "Проект Plashker"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Plashker.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\DocIcon.ico"
Root: HKA; Subkey: "Software\Classes\Plashker.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
; Предложить запустить приложение сразу после установки.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
