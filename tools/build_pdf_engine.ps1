# Build only the isolated PDF Markdown worker. All generated files stay in cache_root.
[CmdletBinding()]
param([string]$SdkPython, [string]$OutputRoot)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pathsFile = Join-Path $env:USERPROFILE '.ai-workspace\paths.md'
if (-not (Test-Path -LiteralPath $pathsFile)) { throw 'Missing local workspace paths.md' }
$cacheLine = Get-Content -LiteralPath $pathsFile | Select-String '^cache_root:\s*(.+)$' | Select-Object -First 1
if (-not $cacheLine) { throw 'cache_root is not configured' }
$cacheRoot = $cacheLine.Matches[0].Groups[1].Value.Trim()
if (-not [IO.Path]::IsPathRooted($cacheRoot)) { throw 'cache_root must be absolute' }
$projectCache = [IO.Path]::GetFullPath((Join-Path $cacheRoot (Split-Path -Leaf $repo)))
if ($projectCache.StartsWith($repo.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The engine build must stay outside the source checkout'
}
if (-not $OutputRoot) { $OutputRoot = Join-Path $projectCache 'pdf-engine-build' }
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
if (-not $OutputRoot.StartsWith($projectCache.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputRoot must be inside the configured project cache'
}
if (-not $SdkPython) { $SdkPython = Join-Path $projectCache 'engines\pymupdf4llm-runtime\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $SdkPython)) { throw 'The private PyMuPDF 2.0 SDK runtime is missing' }
$probeCode = "import json,sysconfig; from importlib.metadata import version; print(json.dumps({'sdk':version('pymupdf'),'pyinstaller':version('pyinstaller'),'site':sysconfig.get_path('purelib')}))"
$probe = & $SdkPython -I -B -c $probeCode
if ($LASTEXITCODE -ne 0) { throw 'Install the pinned PyInstaller build dependency in the private SDK runtime first' }
$sdk = ($probe | Select-Object -Last 1) | ConvertFrom-Json
if ($sdk.sdk -ne '2.0') { throw 'This worker build requires the verified PyMuPDF 2.0 SDK' }
if ($sdk.pyinstaller -ne '6.21.0') { throw 'This worker build requires PyInstaller 6.21.0' }
$entry = Join-Path $PSScriptRoot 'pdf_markdown_worker.py'
$child = Join-Path $repo 'pubnexus\src\pubnexus\pdf_markdown_child.py'
$agpl = Join-Path $PSScriptRoot 'pdf_engine_licenses\AGPL-3.0.txt'
foreach ($required in @($entry, $child, $agpl)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required worker source/license missing: $required" }
}
$childSourceHash = (Get-FileHash -LiteralPath $child -Algorithm SHA256).Hash.ToLowerInvariant()
$appVer = (& $SdkPython -I -B -c "import runpy,sys; print(runpy.run_path(sys.argv[1])['VERSION'])" (Join-Path $repo 'pubnexus\src\pubnexus\version.py')).Trim()
if ($LASTEXITCODE -ne 0 -or $appVer -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid application version' }
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$buildArgs = @('-I', '-B', '-m', 'PyInstaller', '--noconfirm', '--onedir', '--console',
    '--name', 'pdf-markdown-worker', '--distpath', (Join-Path $OutputRoot 'dist'),
    '--workpath', (Join-Path $OutputRoot 'work'), '--specpath', $OutputRoot,
    '--paths', (Join-Path $repo 'pubnexus\src'),
    '--collect-all', 'pymupdf', '--collect-all', 'pymupdf4llm',
    '--collect-all', 'onnxruntime', '--collect-all', 'numpy',
    '--collect-all', 'kiwipiepy', '--collect-all', 'kiwipiepy_model',
    '--collect-all', 'markdown_it', '--hidden-import', '_kiwipiepy',
    '--copy-metadata', 'pymupdf', '--copy-metadata', 'pymupdf4llm',
    '--copy-metadata', 'pymupdf-layout', '--copy-metadata', 'onnxruntime',
    '--copy-metadata', 'numpy', '--copy-metadata', 'psutil',
    '--copy-metadata', 'pyyaml', '--copy-metadata', 'networkx', '--copy-metadata', 'tabulate',
    '--exclude-module', 'torch', '--exclude-module', 'transformers',
    '--exclude-module', 'tensorflow', '--exclude-module', 'matplotlib',
    '--exclude-module', 'IPython', '--exclude-module', 'tkinter', $entry)
$previousInstallerCache = $env:PYINSTALLER_CONFIG_DIR
$previousBytecodePolicy = $env:PYTHONDONTWRITEBYTECODE
try {
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $OutputRoot 'pyinstaller-cache'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $SdkPython @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "PDF worker build failed: $LASTEXITCODE" }
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $previousInstallerCache
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecodePolicy
}
$bundle = Join-Path $OutputRoot 'dist\pdf-markdown-worker'
$executable = Join-Path $bundle 'pdf-markdown-worker.exe'
if (-not (Test-Path -LiteralPath $executable)) { throw 'The PDF worker executable was not produced' }

# The upstream package's COPYING is a dual-license declaration; include full AGPL too.
$licenses = Join-Path $bundle 'licenses'
New-Item -ItemType Directory -Force -Path $licenses | Out-Null
Copy-Item -LiteralPath $agpl -Destination (Join-Path $licenses 'AGPL-3.0.txt') -Force
$site = [string]$sdk.site
Copy-Item -LiteralPath (Join-Path $site 'pymupdf-2.0.dist-info\COPYING') -Destination (Join-Path $licenses 'PyMuPDF-COPYING.txt') -Force
Copy-Item -LiteralPath (Join-Path $site 'pymupdf-2.0.dist-info\METADATA') -Destination (Join-Path $licenses 'PyMuPDF-METADATA.txt') -Force
Copy-Item -LiteralPath (Join-Path $site 'onnxruntime\LICENSE') -Destination (Join-Path $licenses 'ONNXRuntime-LICENSE.txt') -Force
Copy-Item -LiteralPath (Join-Path $site 'onnxruntime\ThirdPartyNotices.txt') -Destination (Join-Path $licenses 'ONNXRuntime-ThirdPartyNotices.txt') -Force
foreach ($distribution in @('numpy', 'networkx', 'pyyaml', 'packaging', 'protobuf', 'tabulate', 'psutil')) {
    $metadata = Get-ChildItem -LiteralPath $site -Directory -Filter ($distribution + '-*.dist-info') | Select-Object -First 1
    if (-not $metadata) { continue }
    $folder = Join-Path $licenses $distribution
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    foreach ($name in @('licenses', 'LICENSE', 'LICENSE.txt', 'COPYING')) {
        $licensePath = Join-Path $metadata.FullName $name
        if (Test-Path -LiteralPath $licensePath) { Copy-Item -LiteralPath $licensePath -Destination $folder -Recurse -Force }
    }
    Copy-Item -LiteralPath (Join-Path $metadata.FullName 'METADATA') -Destination $folder -Force
}
$notice = @'
PDF Markdown worker: official PyMuPDF 2.0 SDK, unmodified upstream layout/export.
PyMuPDF and MuPDF: GNU Affero GPL 3.0 or Artifex Commercial License, as declared by upstream.
No commercial-license entitlement is asserted by this package.
Upstream source: https://github.com/pymupdf/PyMuPDF
Upstream licensing: https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
Application and adapter source: https://github.com/jminbae/pdf-extractor
See AGPL-3.0.txt, PyMuPDF-COPYING.txt, package METADATA and dependency license notices.
ONNX models and native dependencies are copied from the installed verified SDK; no models are downloaded at runtime.
'@
[IO.File]::WriteAllText((Join-Path $licenses 'NOTICE.txt'), $notice, [Text.Encoding]::UTF8)
& $SdkPython -I -B (Join-Path $PSScriptRoot 'collect_licenses.py') `
    --toc (Join-Path $OutputRoot 'work\pdf-markdown-worker\Analysis-00.toc') `
    --destination (Join-Path $licenses 'dependencies') --repo $repo --version $appVer
if ($LASTEXITCODE -ne 0) { throw 'PDF worker dependency license collection failed' }

# Verify each shipped upstream layout model, rather than assuming collect-all found it.
$models = @()
foreach ($package in @('pymupdf', 'pymupdf4llm')) {
    $packageRoot = Join-Path $site $package
    foreach ($model in Get-ChildItem -LiteralPath $packageRoot -File -Recurse -Filter '*.onnx') {
        $relative = $model.FullName.Substring($packageRoot.Length).TrimStart('\')
        $target = Join-Path $bundle ("_internal\$package\$relative")
        if (-not (Test-Path -LiteralPath $target)) { throw "Missing bundled ONNX model: $package/$relative" }
        $sha = (Get-FileHash -LiteralPath $model.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha) {
            throw "Bundled ONNX model hash mismatch: $package/$relative"
        }
        $models += @{ name=("$package/$relative".Replace('\', '/')); bytes=$model.Length; sha256=$sha }
    }
}
if ($models.Count -eq 0) { throw 'No SDK layout ONNX resources were found' }
if (-not (Test-Path -LiteralPath (Join-Path $bundle '_internal\onnxruntime\capi\onnxruntime_pybind11_state.pyd'))) {
    throw 'ONNX Runtime native binding is missing'
}
if ((Get-FileHash -LiteralPath $child -Algorithm SHA256).Hash.ToLowerInvariant() -ne $childSourceHash) {
    throw 'The worker child source changed during the build; rebuild after it is frozen'
}
$manifest = @{ sdk_version=$sdk.sdk; pyinstaller_version=$sdk.pyinstaller; onedir=$true;
    executable='pdf-markdown-worker.exe'; executable_sha256=(Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant();
    child_source_sha256=$childSourceHash; models=$models }
[IO.File]::WriteAllText((Join-Path $bundle 'build-manifest.json'), ($manifest | ConvertTo-Json -Depth 8), [Text.Encoding]::UTF8)
Write-Host "PDF SDK worker built: $executable"
