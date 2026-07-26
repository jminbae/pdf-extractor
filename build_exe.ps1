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
$work = "C:\pnxb"

if (-not (Test-Path $py)) { throw "파이썬을 찾지 못했다: $py" }
New-Item -ItemType Directory -Force -Path $work | Out-Null

$entry = Join-Path $repo "pubnexus\app.py"
if (-not (Test-Path $entry)) { throw "화면 파일이 없다: $entry" }

Push-Location (Join-Path $repo "pubnexus")
try {
    $args = @(
        "-m", "PyInstaller", "--noconfirm",
        "--onefile", "--windowed",
        "--name", "PDF Extractor",
        "--distpath", "$work\dist", "--workpath", "$work\work", "--specpath", $work,
        "--paths", "src",
        # 설정을 exe 안에 동봉한다 — exe 를 아무 폴더에 혼자 놔도 실행되게.
        # (utils.load_config 가 exe 옆 → 프로젝트 루트 → 동봉본 순으로 찾는다)
        "--add-data", "config.yaml;.",
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

$out = "$work\dist\PDF Extractor.exe"
if (-not (Test-Path $out)) { throw "결과물이 없다: $out" }
Copy-Item $out (Join-Path $repo "PDF Extractor.exe") -Force
$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
"완료 — PDF Extractor.exe ($mb MB) → $repo"
