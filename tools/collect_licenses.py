"""Copy notices for distributions actually selected by PyInstaller Analysis.

Build-time only: no network, no package import, no user document access.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import sys


def selected_files(toc):
    found = set()

    def visit(item):
        if isinstance(item, (list, tuple)):
            if len(item) == 3 and isinstance(item[1], str) and Path(item[1]).is_file():
                found.add(str(Path(item[1]).resolve()).casefold())
            for child in item:
                visit(child)

    visit(ast.literal_eval(Path(toc).read_text(encoding="utf-8")))
    if not found:
        raise ValueError("PyInstaller analysis contains no source files")
    return found


def license_file(path):
    return any(part.casefold() in ("licenses", "licences") for part in path.parts) or bool(
        re.match(r"^(?:licen[sc]e|copying|notice|authors)(?:[._-].*)?$", path.name, re.I))


def collect(toc, destination, repo, version, distributions=None):
    selected = selected_files(toc)
    destination, repo = Path(destination), Path(repo)
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for dist in distributions if distributions is not None else metadata.distributions():
        files = list(dist.files or [])
        if not any(str(Path(dist.locate_file(file)).resolve()).casefold() in selected for file in files):
            continue
        name = dist.metadata["Name"]
        folder = destination / re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
        folder.mkdir(exist_ok=True)
        notices = []
        for file in files:
            if not license_file(Path(file)):
                continue
            # Do not copy wheel paths traversing outside the installed package.
            if Path(file).is_absolute() or ".." in Path(file).parts:
                continue
            source = Path(dist.locate_file(file))
            if not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
                continue
            target = folder / str(file)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            notices.append({"path": target.relative_to(destination).as_posix(),
                            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        # This upstream wheel omits its BSD notice and incorrectly says MIT in
        # METADATA. Pin both upstream source and notice; never infer a license.
        if not notices and name == 'proxy_tools' and dist.version == '0.1.0':
            code = Path(dist.locate_file('proxy_tools/__init__.py'))
            notice = repo / 'tools/third_party_licenses/proxy_tools/LICENSE.txt'
            if (hashlib.sha256(code.read_bytes()).hexdigest() !=
                    'd1539d95e1a713c068ca81d42e047b2c76568964cf277596d4e19efb22f476be' or
                    hashlib.sha256(notice.read_bytes()).hexdigest() !=
                    'a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4'):
                raise ValueError('proxy_tools upstream source/notice hash mismatch')
            target = folder / 'upstream-LICENSE.txt'
            shutil.copyfile(notice, target)
            shutil.copyfile(notice.with_name('PROVENANCE.md'), folder / 'PROVENANCE.md')
            notices.append({'path': target.relative_to(destination).as_posix(),
                            'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
        metadata_only = False
        if not notices and name == 'flatbuffers' and dist.version == '25.12.19':
            origin = repo / 'tools/third_party_licenses/flatbuffers'
            provenance = json.loads((origin / 'PROVENANCE.json').read_text(encoding='utf-8'))
            expected = {row['path']: row['sha256'] for row in provenance['installed_source_files']}
            installed = {Path(file).as_posix() for file in files
                         if Path(file).as_posix().startswith('flatbuffers/') and str(file).endswith('.py')}
            if (set(expected) != installed or len(expected) != 10 or
                    hashlib.sha256((origin / 'LICENSE').read_bytes()).hexdigest() !=
                    'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'):
                raise ValueError('flatbuffers upstream source/notice set mismatch')
            for file, digest in expected.items():
                if hashlib.sha256(Path(dist.locate_file(file)).read_bytes()).hexdigest() != digest:
                    raise ValueError('flatbuffers upstream source hash mismatch: ' + file)
            target = folder / 'upstream-LICENSE.txt'
            shutil.copyfile(origin / 'LICENSE', target)
            shutil.copyfile(origin / 'PROVENANCE.json', folder / 'PROVENANCE.json')
            notices.append({'path': target.relative_to(destination).as_posix(),
                            'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
        if (not notices and name.replace('-', '_').lower() in ('pymupdf4llm', 'pymupdf_layout')
                and dist.version == '2.0' and files
                and all('.dist-info/' in Path(file).as_posix() for file in files)
                and 'This is an empty package' in (dist.read_text('METADATA') or '')):
            # These exact upstream compatibility distributions contain metadata
            # only. The executable 4llm/layout code belongs to pymupdf 2.0 and
            # its full AGPL notices are collected separately, not invented here.
            target = folder / 'METADATA-ONLY.txt'
            target.write_text(
                f'{name} 2.0 is an empty upstream compatibility distribution.\n'
                'Its complete installed RECORD contains no executable code or model data.\n'
                'The actual layout/4llm implementation is distributed by pymupdf 2.0.\n'
                'See its COPYING, full AGPL-3.0 notice and exact source links in BUILDING.md.\n'
                'Upstream package metadata is preserved as METADATA.txt in this folder.\n', encoding='utf-8')
            notices.append({'path': target.relative_to(destination).as_posix(),
                            'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
            metadata_only = True
        for file in files:
            if str(file).endswith(".dist-info/METADATA"):
                shutil.copyfile(dist.locate_file(file), folder / "METADATA.txt")
                break
        records.append({"name": name, "version": dist.version,
                        "project_urls": dist.metadata.get_all("Project-URL") or [],
                        "home_page": dist.metadata.get("Home-page", ""), "notices": notices,
                        "metadata_only_compatibility": metadata_only})
    records.sort(key=lambda row: row["name"].casefold())
    missing = [row["name"] for row in records if not row["notices"]]
    if missing:
        raise ValueError("Missing installed license notices: " + ", ".join(missing))
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("CPython LICENSE.txt is missing")
    shutil.copyfile(python_license, destination / "CPython-LICENSE.txt")
    shutil.copyfile(repo / "LICENSE", destination / "Application-AGPL-3.0.txt")
    source_url = f"https://github.com/jminbae/pdf-extractor/tree/v{version}"
    (destination / "SOURCE.txt").write_text(
        f"PDF Extractor {version}\nApplication and adapter source: {source_url}\n"
        f"Source archive: https://github.com/jminbae/pdf-extractor/archive/refs/tags/v{version}.zip\n"
        "Build instructions: docs/implementation/BUILDING.md at the same tag.\n"
        "Application: GNU Affero General Public License version 3.\n"
        "Third-party software retains the notices in the adjacent folders.\n"
        "PDF SDK worker and rHWP notices are also included in their engines folders.\n",
        encoding="utf-8")
    manifest = {"application_version": version, "source": source_url, "packages": records}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "requirements-main.lock").write_text(
        "\n".join(f"{row['name']}=={row['version']}" for row in records) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--toc", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    result = collect(args.toc, args.destination, args.repo, args.version)
    print(json.dumps({"packages": len(result["packages"]), "version": args.version}))
