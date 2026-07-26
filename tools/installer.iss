; PDF Extractor 설치프로그램 (Inno Setup 6)
;
;   build_exe.ps1 이 부른다. 직접 부를 때는 프로그램 폴더를 넘긴다:
;     ISCC.exe /DAppDir=D:\pnxb\dist\PDF Extractor tools\installer.iss
;
; 왜 설치프로그램인가
;   PyInstaller --onefile 로 만든 exe 를 Defender 가 Trojan:Win32/Wacatac.H!ml
;   로 지웠다(2026-07-26). `!ml` 은 서명 일치가 아니라 머신러닝 추정이고,
;   onefile 이 실행할 때 자기 안을 임시폴더에 풀어 돌리는 동작이 악성코드와
;   구조가 같아서 늘 걸린다. onedir + 설치프로그램으로 그 동작을 없앤다.
;   받는 쪽이 받는 것은 여전히 파일 하나다.
;
; 관리자 권한을 요구하지 않는다(lowest). 사용자 폴더에 깔고, 분석 엔진도
; %LOCALAPPDATA% 에 받는다 — 병원 PC 처럼 권한이 막힌 곳에서도 깔린다.

#ifndef AppDir
  #define AppDir "..\..\..\pnxb\dist\PDF Extractor"
#endif

#define AppName "PDF Extractor"
#define AppVer "1.1"
#define AppExe "PDF Extractor.exe"

[Setup]
AppId={{8F3C1A62-5D4E-4B77-9E2A-0C6B1F7D3A94}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=Jung Min Bae
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputBaseFilename=PDF-Extractor-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
; 설치 중 프로그램이 떠 있으면 파일을 못 바꾼다. 닫아 달라고 알린다.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 작업:"

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "지금 실행"; Flags: nowait postinstall skipifsilent

; 지울 때: 프로그램만 지운다. 뽑아 둔 정본·그림(%LOCALAPPDATA%\PDF Extractor)과
; 분석 엔진은 남긴다 — 다시 깔면 그대로 이어서 쓴다. 사용자가 뽑은 결과물을
; 설치 관리자가 말없이 지우는 일은 없어야 한다.
