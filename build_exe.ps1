# "PDF Extractor.exe" 빌드 — pywebview 화면(app.py)을 단일 실행파일로 묶는다.
#
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1
#
# 왜 로컬 cache_root에서 빌드하나
#   PyInstaller 는 작업폴더에 수만 개 임시파일을 쓴다. Dropbox 안에서 하면
#   동기화가 파일을 잠가 빌드가 무작위로 실패한다. 그래서 Dropbox 밖에서
#   빌드하고 결과물도 로컬 캐시에 유지한다.
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
# 빌드·배포 산출물은 이 기기의 cache_root 안에 둔다. 기존 결과는 이동하지 않는다.
$pathsFile = Join-Path $env:USERPROFILE ".ai-workspace\paths.md"
if (-not (Test-Path -LiteralPath $pathsFile)) { throw "기기 경로 설정이 없다: $pathsFile" }
$cacheSetting = Get-Content -LiteralPath $pathsFile | Select-String -Pattern '^cache_root:\s*(.+)$' | Select-Object -First 1
if (-not $cacheSetting) { throw "cache_root 설정이 없다: $pathsFile" }
$cacheRoot = $cacheSetting.Matches[0].Groups[1].Value.Trim()
if (-not [IO.Path]::IsPathRooted($cacheRoot)) { throw "cache_root는 절대 경로여야 한다" }
$work = [IO.Path]::GetFullPath((Join-Path $cacheRoot (Split-Path -Leaf $repo)))
if ($work.StartsWith($repo.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "빌드 경로는 프로젝트 밖이어야 한다: $work"
}

if (-not (Test-Path $py)) { throw "파이썬을 찾지 못했다: $py" }
$versionFile = Join-Path $repo 'pubnexus\src\pubnexus\version.py'
$appVer = (& $py -B -c "import runpy, sys; print(runpy.run_path(sys.argv[1]).get('VERSION'))" $versionFile).Trim()
if ($LASTEXITCODE -ne 0 -or $appVer -notmatch '^\d+\.\d+\.\d+$') { throw '앱 버전을 읽지 못했습니다.' }
New-Item -ItemType Directory -Force -Path $work | Out-Null

$entry = Join-Path $repo "pubnexus\app.py"
if (-not (Test-Path $entry)) { throw "화면 파일이 없다: $entry" }

# The general-PDF SDK stays in its own frozen Python/ONNX process. Never replace
# the main application's PyMuPDF version used by the scholarly-paper/OCR path.
$pdfEngineBuild = Join-Path $work 'pdf-engine-build'
& (Join-Path $repo 'tools\build_pdf_engine.ps1') -OutputRoot $pdfEngineBuild
$pdfEngineDir = Join-Path $pdfEngineBuild 'dist\pdf-markdown-worker'
if (-not (Test-Path -LiteralPath (Join-Path $pdfEngineDir 'pdf-markdown-worker.exe'))) {
    throw 'The bundled PDF Markdown SDK worker is missing'
}

# exe 안에 동봉할 설정. PyInstaller 는 --add-data 를 spec 폴더 기준 상대경로로 바꾸는데,
# spec 은 C:\pnxb 이고 리포는 D: 라 드라이브가 달라 상대경로 계산이 실패한다
# (ValueError: path is on mount 'D:', start on mount 'C:'). 빌드 폴더로 복사해서 넘긴다.
$cfgSrc = Join-Path $repo "pubnexus\config.yaml"
$cfgTmp = Join-Path $work "config.yaml"
Copy-Item $cfgSrc $cfgTmp -Force

# 의학용어 사전도 같은 이유로 빌드 폴더를 거쳐 넣는다. 모듈은 아카이브 안으로
# 들어가므로 __file__ 옆에서 못 찾는다 — translate.glossary() 가 동봉본을 먼저 본다.
$glsTmp = Join-Path $work "glossary.json"
Copy-Item (Join-Path $repo "pubnexus\src\pubnexus\glossary.json") $glsTmp -Force

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
        "--add-data", "$glsTmp;.",
        '--add-data', ($pdfEngineDir + ';engines/pdf-markdown'),
        "--collect-submodules", "pubnexus",
        "--collect-all", "webview",
        "--hidden-import", "clr_loader",
        "--hidden-import", "pythonnet",
        "--hidden-import", "bottle",
        "--hidden-import", "markdown_it",
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
    # 검증된 rHWP와 MIT 고지를 함께 동봉한다. HOP UI는 의존하지 않는다.
    $rhwpDir = Join-Path $work 'engines\rhwp-v0.8.7\rhwp'
    $rhwpExe = Join-Path $rhwpDir 'rhwp.exe'
    if (Test-Path -LiteralPath $rhwpExe) {
        $args = $args[0..($args.Length - 2)] + @(
            '--add-binary', ($rhwpExe + ';engines'),
            '--add-data', ((Join-Path $rhwpDir 'LICENSE') + ';engines/rhwp-license'),
            'app.py'
        )
    } else {
        Write-Warning 'rHWP가 없어 HWP 엔진을 동봉하지 않습니다. PUBNEXUS_RHWP로 별도 지정할 수 있습니다.'
    }
    # MuPDF 내장 OCR은 실행 파일 대신 로컬 언어 데이터만 필요하다.
    $tessdata = Join-Path $work 'engines\tessdata'
    $ocrFiles = @('eng.traineddata', 'kor.traineddata', 'LICENSE', 'models.json')
    if (@($ocrFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $tessdata $_)) }).Count -eq 0) {
        $args = $args[0..($args.Length - 2)] + @('--add-data', ($tessdata + ';engines/tessdata'), 'app.py')
    } else {
        Write-Warning '로컬 OCR 언어 데이터가 없어 동봉하지 않습니다.'
    }
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

& $iscc "/DAppDir=$(Split-Path $app)" "/DAppVer=$appVer" "/O$work" (Join-Path $repo "tools\installer.iss")
if ($LASTEXITCODE -ne 0) { throw "설치프로그램 만들기 실패 (exit $LASTEXITCODE)" }

$setup = "$work\PDF-Extractor-Setup.exe"
if (-not (Test-Path $setup)) { throw "설치프로그램이 없다: $setup" }

# 설치 프로그램도 cache_root에 유지한다. 기존 리포/설치본은 덮어쓰지 않는다.
$final = $setup
$checksum = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(($final + '.sha256'), ($checksum + '  PDF-Extractor-Setup.exe' + [Environment]::NewLine), [Text.Encoding]::ASCII)

$mb = [math]::Round((Get-Item $final).Length / 1MB, 1)
"완료 — PDF-Extractor-Setup.exe ($mb MB)"
"  $final"
"  버전: $appVer"
"  자동 업데이트용 검사값: $final.sha256"
