# "PDF Extractor.exe" 빌드 — pywebview 화면(app.py)을 단일 실행파일로 묶는다.
#
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1
#
# 왜 C:\pnxb 에서 빌드하나
#   PyInstaller 는 작업폴더에 수만 개 임시파일을 쓴다. Dropbox 안에서 하면
#   동기화가 파일을 잠가 빌드가 무작위로 실패한다. 그래서 Dropbox 밖에서
#   빌드하고 결과물만 리포로 옮긴다.
#
# pywebview 를 묶을 때 챙겨야 하는 것
#   · pythonnet / clr_loader — 윈도우에서 WebView2 를 부르는 다리. hidden import
#     로 잡히지 않아 명시해야 한다.
#   · webview 패키지의 데이터파일(js 브릿지 등) — collect-data 로 통째로.
#   · WebView2 런타임 자체는 윈도우 10/11 에 이미 들어 있다(엣지와 함께). 따로
#     넣지 않는다.
# 무겁고 안 쓰는 것들은 뺀다 — 넣으면 exe 가 수백 MB 로 붇는다.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
# 사용자 폴더 이름에 한글이 들어 있다. 스크립트 안에 한글 경로를 그대로 적으면
# PowerShell 5.1 이 파일 인코딩을 ANSI 로 읽어 경로가 깨진다 → 환경변수로 조립한다.
$py   = Join-Path $env:USERPROFILE "pnxvenv\Scripts\python.exe"
# 빌드 폴더는 **리포와 같은 드라이브**의 Dropbox 밖에 둔다.
# 드라이브가 다르면 PyInstaller 가 spec 폴더 기준 상대경로를 만들지 못해 죽는다
#   ValueError: path is on mount 'D:', start on mount 'C:'
# (리포가 C: 인 PC 에서는 예전처럼 C:\pnxb 가 된다)
$work = (Split-Path -Qualifier $repo) + "\pnxb"

if (-not (Test-Path $py)) { throw "파이썬을 찾지 못했다: $py" }
New-Item -ItemType Directory -Force -Path $work | Out-Null

$entry = Join-Path $repo "pubnexus\app.py"
if (-not (Test-Path $entry)) { throw "화면 파일이 없다: $entry" }

# exe 안에 동봉할 설정. PyInstaller 는 --add-data 를 spec 폴더 기준 상대경로로 바꾸는데,
# spec 은 C:\pnxb 이고 리포는 D: 라 드라이브가 달라 상대경로 계산이 실패한다
# (ValueError: path is on mount 'D:', start on mount 'C:'). 빌드 폴더로 복사해서 넘긴다.
$cfgSrc = Join-Path $repo "pubnexus\config.yaml"
$cfgTmp = Join-Path $work "config.yaml"
Copy-Item $cfgSrc $cfgTmp -Force

Push-Location (Join-Path $repo "pubnexus")
try {
    $args = @(
        "-m", "PyInstaller", "--noconfirm",
        # onefile 이 아니라 **onedir** 이다. onefile 은 실행할 때 자기 안을 임시
        # 폴더에 풀어 돌리는데, 그 동작이 악성코드와 구조가 같아 백신이 잡는다.
        # 실제로 Defender 가 Trojan:Win32/Wacatac.H!ml 로 지웠다(2026-07-26).
        # 배포는 이 폴더를 Inno Setup 으로 묶은 설치프로그램 하나로 한다
        # (tools\installer.iss). 받는 쪽이 보는 것은 여전히 파일 하나다.
        "--onedir", "--windowed",
        "--name", "PDF Extractor",
        "--distpath", "$work\dist", "--workpath", "$work\work", "--specpath", $work,
        "--paths", "src",
        # 설정을 exe 안에 동봉한다 — exe 를 아무 폴더에 혼자 놔도 실행되게.
        # (utils.load_config 가 exe 옆 → 프로젝트 루트 → 동봉본 순으로 찾는다)
        "--add-data", "$cfgTmp;.",
        "--collect-submodules", "pubnexus",
        "--collect-all", "webview",
        "--hidden-import", "clr_loader",
        "--hidden-import", "pythonnet",
        "--hidden-import", "bottle",
        # 안 쓰는 무거운 것들 — 넣으면 exe 가 수백 MB 로 붇는다
        "--exclude-module", "torch",
        "--exclude-module", "transformers",
        "--exclude-module", "onnxruntime",
        "--exclude-module", "sentence_transformers",
        "--exclude-module", "lancedb",
        "--exclude-module", "scipy",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pandas",
        "--exclude-module", "IPython",
        "--exclude-module", "notebook",
        "--exclude-module", "tkinter",
        "app.py"
    )
    & $py @args
    if ($LASTEXITCODE -ne 0) { throw "빌드 실패 (exit $LASTEXITCODE)" }
} finally { Pop-Location }

$app = "$work\dist\PDF Extractor\PDF Extractor.exe"
if (-not (Test-Path $app)) { throw "결과물이 없다: $app" }

# ── 설치프로그램으로 묶는다 ─────────────────────────────────────────────
# 리포에는 exe 를 두지 않는다. 배포는 Releases 의 설치프로그램 하나로 한다
# (50MB 짜리를 커밋에 얹지 않고, 백신에 지워질 파일을 리포에 남기지 않는다).
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    $mb = [math]::Round(((Get-ChildItem (Split-Path $app) -Recurse -File |
           Measure-Object Length -Sum).Sum / 1MB), 1)
    "완료 — 프로그램 폴더 ($mb MB): $(Split-Path $app)"
    "  설치프로그램을 만들려면 Inno Setup 6 이 필요하다:"
    "  winget install --id JRSoftware.InnoSetup"
    return
}

& $iscc "/DAppDir=$(Split-Path $app)" "/O$work" (Join-Path $repo "tools\installer.iss")
if ($LASTEXITCODE -ne 0) { throw "설치프로그램 만들기 실패 (exit $LASTEXITCODE)" }

$setup = "$work\PDF-Extractor-Setup.exe"
if (-not (Test-Path $setup)) { throw "설치프로그램이 없다: $setup" }

# 결과물은 리포 폴더에 둔다 — 원장이 여기서 바로 집는다. git 에는 올리지 않는다
# (.gitignore). 빌드는 Dropbox 밖에서 하고 결과만 옮긴다(동기화가 파일을 잠근다).
$final = Join-Path $repo "PDF-Extractor-Setup.exe"
Copy-Item $setup $final -Force

$mb = [math]::Round((Get-Item $final).Length / 1MB, 1)
"완료 — PDF-Extractor-Setup.exe ($mb MB)"
"  $final"
"  올리기:  gh release upload v1.0 `"$final`" --clobber"
