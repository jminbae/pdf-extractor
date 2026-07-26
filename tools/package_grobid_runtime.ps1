# 빌드해 둔 GROBID 를 **배포용 런타임 묶음**으로 만든다.
#
#   powershell -ExecutionPolicy Bypass -File tools\package_grobid_runtime.ps1
#
# 결과물 `grobid-win64.zip` 을 GitHub Releases 에 올리면, 앱이 처음 켜질 때
# grobid_service.install() 이 내려받아 %LOCALAPPDATA%\PDF Extractor\grobid 에 푼다.
# 사용자 PC 에는 아무것도 미리 깔 필요가 없다 — Docker 도, 자바도.
#
# 왜 통째로 배포하나
#   공식 GROBID 는 윈도우에서 돌지 않는다(tools\build_grobid_windows.ps1 주석 참고).
#   패치해 빌드한 결과물을 직접 배포하는 수밖에 없다. Apache-2.0 이라 재배포는 된다.
#
# 무엇을 덜어냈나 (원본 1,561MB → 아래 구성)
#   · JDK 21 전체(328MB) → jlink 최소 런타임(49MB). javaw.exe 가 있어야 창이 안 뜬다.
#   · pdfalto 의 리눅스·맥·win-32 판(248MB) → 뺀다. win-64 와 languages 만 쓴다.
#   · patent 모델(79MB) → 뺀다. 논문만 다룬다.
#   · DeLFT 용 *.hdf5(32MB) → 뺀다. CRF(.wapiti)로만 돈다.
$ErrorActionPreference = "Stop"

$root  = if ($env:GROBID_ROOT) { $env:GROBID_ROOT } else { "C:\grobid" }
$repo  = Split-Path -Parent $PSScriptRoot
$stage = Join-Path ([System.IO.Path]::GetTempPath()) "grobid_pkg"
$out   = Join-Path $repo "grobid-win64.zip"

$srcLib = "$root\grobid\grobid-service\build\install\grobid-service\lib"
if (-not (Test-Path $srcLib)) { throw "빌드된 결과물이 없다: $srcLib  (먼저 build_grobid_windows.ps1)" }

if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# ── 1. 최소 자바 런타임 ─────────────────────────────────────────────────
Write-Host "최소 JRE 만드는 중..."
& "$root\jdk21\bin\jlink.exe" `
    --add-modules java.base,java.logging,java.xml,java.naming,java.management,java.desktop,java.sql,java.scripting,java.instrument,java.security.jgss,java.net.http,jdk.unsupported,jdk.crypto.ec,jdk.zipfs `
    --strip-debug --no-header-files --no-man-pages --compress=zip-6 `
    --output "$stage\jdk21"
if (-not (Test-Path "$stage\jdk21\bin\javaw.exe")) { throw "javaw.exe 가 없다 — 창 없는 기동이 불가능하다" }

# ── 2. grobid-home (덜어낼 것 빼고) ────────────────────────────────────
Write-Host "grobid-home 복사 중..."
$homeSrc = "$root\grobid\grobid-home"
$homeDst = "$stage\grobid\grobid-home"
$skipDirs = @("pdfalto\lin-64", "pdfalto\lin_arm-64", "pdfalto\mac-64",
              "pdfalto\mac_arm-64", "pdfalto\win-32", "models\patent", "tmp")
$xd = $skipDirs | ForEach-Object { Join-Path $homeSrc $_ }
robocopy $homeSrc $homeDst /E /NFL /NDL /NJH /NJS /NP /XD @xd /XF *.hdf5 | Out-Null
if ($LASTEXITCODE -ge 8) { throw "grobid-home 복사 실패 (robocopy $LASTEXITCODE)" }
New-Item -ItemType Directory -Force -Path "$homeDst\tmp" | Out-Null   # 서비스가 쓰는 임시 자리

# ── 3. 서비스 jar ───────────────────────────────────────────────────────
Write-Host "서비스 jar 복사 중..."
$libDst = "$stage\grobid\grobid-service\build\install\grobid-service\lib"
robocopy $srcLib $libDst /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "jar 복사 실패 (robocopy $LASTEXITCODE)" }

$mb = [math]::Round(((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum/1MB), 1)
Write-Host "묶기 전 크기: $mb MB"

# ── 4. 압축 ─────────────────────────────────────────────────────────────
Write-Host "압축 중(몇 분 걸린다)..."
if (Test-Path $out) { Remove-Item -LiteralPath $out -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $stage, $out, [System.IO.Compression.CompressionLevel]::Optimal, $false)

Remove-Item -LiteralPath $stage -Recurse -Force
"완료 - {0} ({1} MB)" -f $out, [math]::Round((Get-Item $out).Length/1MB, 1)
"GitHub Releases 에 올린 뒤 grobid_service.RUNTIME_URL 과 맞는지 확인할 것."
