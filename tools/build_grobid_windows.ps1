# GROBID 를 **윈도우 네이티브로** 빌드한다 (Docker 불필요).
#
#   powershell -ExecutionPolicy Bypass -File tools\build_grobid_windows.ps1
#
# 왜 패치가 필요한가
#   공식 GROBID 는 윈도우에서 그냥은 안 돈다. 세 군데가 막는다.
#   1) build.gradle 이 Mac/Unix 외 플랫폼에서 RuntimeException("Unsupported platform!")
#   2) installDist 가 중복 jar(langdetect)에서 멈춘다 (Gradle 9 부터 전략 명시 필수)
#   3) **진짜 블로커** — grobid-core 의 DocumentSource 가 pdfalto 에 넘기는 인자.
#      윈도우에 번들된 pdfalto 는 0.1 이라(리눅스·맥은 0.5/0.6, kermitt2 가 윈도우
#      바이너리를 갱신하지 않는다) `-noLineNumbers` 와 `-onlyGraphsCoord` 를 모른다.
#      그대로 넘기면 변환이 시작도 못 하고 죽어 늘 아래로 끝난다:
#          [NO_BLOCKS] PDF parsing resulted in empty content
#      `-blocks` 를 줘야 <TextBlock> 이 생긴다. 이것이 파서가 읽는 단위다.
#      또 서버 모드는 `--timeout`·`--ulimit` 을 붙이는데 0.1 은 이것도 모른다
#      → 윈도우에서는 스레드 모드로 내려보낸다.
#
#   주의: "GROBID 파서가 <BLOCK>/<TOKEN> 커스텀 포맷을 기대한다"는 옛 설명은 틀렸다.
#   0.9.0 의 PDFALTOSaxHandler 는 표준 ALTO(Page·PrintSpace·TextBlock·TextLine·String)
#   를 읽는다. pdfalto 0.1 이 내는 바로 그 형식이다. 문제는 포맷이 아니라 인자였다.
#
# 검증(2026-07-26, 이 스크립트로 만든 빌드): 영어 논문 6/6 성공.
#   예) 섹션 29 · 표/그림 6 · 참고문헌 74 · 평균 2.4초/편
$ErrorActionPreference = "Stop"

$root    = if ($env:GROBID_ROOT) { $env:GROBID_ROOT } else { "C:\grobid" }
$version = "0.9.0"
$jdkUrl  = "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jdk/hotspot/normal/eclipse"
$srcUrl  = "https://github.com/kermitt2/grobid/archive/refs/tags/$version.zip"

New-Item -ItemType Directory -Force -Path $root | Out-Null
$ProgressPreference = 'SilentlyContinue'

# ── JDK 21 (GROBID 0.9.0 이 JavaLanguageVersion.of(21) 을 선언한다) ──────
if (-not (Test-Path "$root\jdk21\bin\javaw.exe")) {
    Write-Host "JDK 21 내려받는 중..."
    Invoke-WebRequest -Uri $jdkUrl -OutFile "$root\jdk21.zip" -UseBasicParsing
    Expand-Archive -Path "$root\jdk21.zip" -DestinationPath "$root\_jdk" -Force
    Move-Item (Get-ChildItem "$root\_jdk" -Directory | Select-Object -First 1).FullName "$root\jdk21"
    Remove-Item "$root\_jdk" -Recurse -Force
    Remove-Item "$root\jdk21.zip" -Force
}

# ── 소스 ────────────────────────────────────────────────────────────────
if (-not (Test-Path "$root\grobid\build.gradle")) {
    Write-Host "GROBID $version 소스 내려받는 중 (약 485MB)..."
    Invoke-WebRequest -Uri $srcUrl -OutFile "$root\grobid-src.zip" -UseBasicParsing
    Push-Location $root
    tar -xf "grobid-src.zip"
    Pop-Location
    Rename-Item "$root\grobid-$version" "grobid"
    Remove-Item "$root\grobid-src.zip" -Force
}

# ── 패치 1·2: build.gradle ──────────────────────────────────────────────
$bg = "$root\grobid\build.gradle"
$s  = Get-Content $bg -Raw
if ($s -notmatch 'FAMILY_WINDOWS') {
    $s = $s.Replace(@'
    } else {
        throw new RuntimeException("Unsupported platform!")
    }

    def javaLibraryPath
'@, @'
    } else if (Os.isFamily(Os.FAMILY_WINDOWS)) {
        jepLocalLibraries = "${file("./grobid-home/lib/win-64").absolutePath}"
    } else {
        throw new RuntimeException("Unsupported platform!")
    }

    def javaLibraryPath
'@)
    $s = $s.Replace(@'
    } else {
        throw new RuntimeException("Unsupported platform!")
    }

    trainerTasks
'@, @'
    } else if (Os.isFamily(Os.FAMILY_WINDOWS)) {
        libraries = "${file("../grobid-home/lib/win-64").absolutePath}"
    } else {
        throw new RuntimeException("Unsupported platform!")
    }

    trainerTasks
'@)
    $s = $s.Replace(
        '    distTar { duplicatesStrategy = DuplicatesStrategy.EXCLUDE }',
        "    distTar { duplicatesStrategy = DuplicatesStrategy.EXCLUDE }`r`n    installDist { duplicatesStrategy = DuplicatesStrategy.EXCLUDE }")
    Set-Content -Path $bg -Value $s -Encoding UTF8
    Write-Host "build.gradle 패치 완료"
}

# ── 패치 3: DocumentSource.java (pdfalto 인자·실행 모드) ────────────────
$ds = "$root\grobid\grobid-core\src\main\java\org\grobid\core\document\DocumentSource.java"
$s  = Get-Content $ds -Raw
if ($s -notmatch '\-blocks') {
    # 인자: 윈도우는 -blocks, -noLineNumbers·-onlyGraphsCoord 금지
    $s = $s.Replace(@'
        pdfToXml.append(" -fullFontName -noLineNumbers");

        if (!withImage) {
            pdfToXml.append(" -onlyGraphsCoord ");
		}
'@, @'
        // 윈도우 번들 pdfalto 는 0.1 이라 -noLineNumbers·-onlyGraphsCoord 를 모른다.
        // -blocks 를 줘야 <TextBlock> 이 생긴다(파서가 읽는 단위).
        if (SystemUtils.IS_OS_WINDOWS) {
            pdfToXml.append(" -fullFontName -blocks");
        } else {
            pdfToXml.append(" -fullFontName -noLineNumbers");

            if (!withImage) {
                pdfToXml.append(" -onlyGraphsCoord ");
            }
        }
'@)
    # 실행 모드: 서버 모드의 --timeout·--ulimit 을 0.1 이 모른다 → 스레드 모드로
    $s = $s.Replace(
        '            if (GrobidProperties.isContextExecutionServer()) {',
        '            if (GrobidProperties.isContextExecutionServer() && !SystemUtils.IS_OS_WINDOWS) {')
    Set-Content -Path $ds -Value $s -Encoding UTF8
    Write-Host "DocumentSource.java 패치 완료"
}

# ── 빌드 ────────────────────────────────────────────────────────────────
$env:JAVA_HOME = "$root\jdk21"
$inst = "$root\grobid\grobid-service\build\install"
if (Test-Path $inst) { Remove-Item -LiteralPath $inst -Recurse -Force }   # 남아 있으면 installDist 가 거부한다

Push-Location "$root\grobid"
try {
    & .\gradlew.bat --no-daemon -q :grobid-service:installDist
    if ($LASTEXITCODE -ne 0) { throw "빌드 실패 (exit $LASTEXITCODE)" }
} finally { Pop-Location }

$lib = "$root\grobid\grobid-service\build\install\grobid-service\lib"
$n   = (Get-ChildItem $lib).Count
"완료 - jar $n 개, {0} MB. 기동은 pubnexus/src/pubnexus/grobid_service.py 가 한다." -f `
    [math]::Round(((Get-ChildItem $lib | Measure-Object Length -Sum).Sum/1MB), 1)
