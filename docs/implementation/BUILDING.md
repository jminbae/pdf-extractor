# Windows release build

PDF Extractor is distributed under GNU AGPL version 3. Third-party components
retain their own copyright and license terms. `LICENSE` contains the complete
application license. Every installer includes `licenses/SOURCE.txt`, an exact
version source link, dependency versions, original notices and source metadata.

## Source corresponding to a release

For version 1.2.7, use the matching `v1.2.7` release tag in
[jminbae/pdf-extractor](https://github.com/jminbae/pdf-extractor/tree/v1.2.7).
The release provides the installer, SHA256 checksum and source archive at the
same location. No user PDFs, extracted text, images or model caches are included
in the application source archive.

The application adapter changes the local SDK invocation, page recovery,
validated Korean physical-line joins, optional merged-table HTML, asset handling
and provenance. It does not modify installed upstream library code.

## Toolchain and dependencies

- Windows x64, CPython 3.12.10, PowerShell, Inno Setup 6.7.3.
- Main environment: `tools/requirements-main.lock`.
- Isolated PDF worker: `tools/requirements-pdf-worker.lock`.
- PyInstaller 6.21.0. Runtime versions are verified by the worker build script.
- Main PyMuPDF 1.28.2 and worker PyMuPDF 2.0 are deliberately separate.
- rHWP: upstream [v0.8.7](https://github.com/edwardkim/rhwp/tree/v0.8.7),
  commit `1a76570e833917d15817415a53c09ad61ab3203f`, MIT notice included.
- OCR language data: official Tesseract `eng` and `kor`; the installation's
  `engines/tessdata/models.json` records the exact checksums and source.
- Kiwi 0.24.1, model 0.24.0, local `cong` model. Original Apache and nested
  third-party notices are copied, together with the model data.
- RTF content preview: striprtf 0.0.34, BSD-3-Clause. Its metadata and original
  license must ship with the frozen app; the exact wheel and parser hashes are
  recorded in `tools/third_party_licenses/striprtf/PROVENANCE.json`.

Create two virtual environments outside the source checkout and install the
corresponding lock files with `python -m pip install -r <lock-file>`. Place the
worker environment under `<cache_root>/<project-folder>/engines/pymupdf4llm-runtime`.
For the default main location use `%USERPROFILE%/pnxvenv`, or pass `-Python` to
`build_exe.ps1`. These paths are local build configuration, not runtime demands.

Define the absolute `cache_root:` in `%USERPROFILE%/.ai-workspace/paths.md`.
The build refuses an output directory inside the source checkout. Before the
build, put the upstream rHWP executable plus LICENSE under
`<project-cache>/engines/rhwp-v0.8.7/rhwp`, and verified Tesseract data plus LICENSE
and models.json under `<project-cache>/engines/tessdata`. Missing optional engines
produce explicit warnings; a complete release must include both.

```powershell
python -B -m unittest discover -s pubnexus/tests -p 'test_*.py'
& .\build_exe.ps1 -Python <main-python.exe>
```

The installer and checksum remain in the configured project cache. The main
application is built as `onedir`; the SDK runs as a separately frozen process.
`tools/collect_licenses.py` reads actual PyInstaller Analysis files, includes all
selected distribution notices and rejects missing notices. Pinned upstream
packaging exceptions are documented under
`tools/third_party_licenses/proxy_tools/PROVENANCE.md` and
`tools/third_party_licenses/flatbuffers/PROVENANCE.json`, and checked by source
hash. Two empty PyMuPDF compatibility distributions retain their metadata and
an explicit no-code explanation; the executable code's AGPL notices are separate.

The viewer uses the pinned upstream libraries without modifying their installed
source: PyMuPDF for PDF pages, the bundled PPTX browser renderer for slides,
python-docx for DOCX content, openpyxl's ExcelReader and WorkSheetParser for bounded
XLSX cell windows, striprtf for RTF, and rHWP's official render-tree, export-png
and page-numbered export-text commands for HWP/HWPX. A preview does not require
Markdown extraction or an Office installation. DOCX content preview does not
promise full layout, shapes or formula fidelity; an explicit PDF alternative
requires a compatible local conversion engine.

## AGPL upstream sources

- [Main PyMuPDF 1.28.2 exact build source](https://github.com/pymupdf/PyMuPDF/tree/12786d12b2962a1d77fd13b103162eddf749644c)
- [Main MuPDF 1.28.2 source archive](https://mupdf.com/downloads/archive/mupdf-1.28.2-source.tar.gz)
- [Worker PyMuPDF 2.0 exact build source](https://github.com/pymupdf/PyMuPDF/tree/7db0ab090698d49de637046786c8f85f29d89fd2)
- [Worker MuPDF 1.28.5 source archive](https://mupdf.com/downloads/archive/mupdf-1.28.5-source.tar.gz)

The installed main SDK reports MuPDF 1.28.2; the isolated worker reports
MuPDF 1.28.5. These are the actual upstream build provenance, rather than an
assumption that package version and bundled C-library version always match.
The distributed dependency metadata, upstream source references and
full AGPL texts are retained in both license bundles. No commercial entitlement
is asserted.

## Verification and publication

Run the native PDF and scan modes of `pubnexus/tests/frozen_smoke.py` against the
new executable, using a fresh output directory outside the source checkout.
The script uses synthetic originals and private stores, checks actual WebView2
bridge calls, Markdown writes, decoded preview images and unchanged source hashes.
Also run the whole-book comparison in an isolated output store. The source PDF
is read only; existing user Markdown is not replaced during verification.

Publish a stable GitHub tag only after the matching installer and source pass
verification. The app's update contract is exactly `PDF-Extractor-Setup.exe` and
`PDF-Extractor-Setup.exe.sha256`; the checksum file uses the SHA256 and exact file
name. Read back the release and download the published assets to verify hashes.
