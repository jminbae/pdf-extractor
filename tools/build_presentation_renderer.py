"""Package the unmodified, pinned browser renderer; no Node/global install required."""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

REPO_ROOT=Path(__file__).resolve().parents[1]
ENGINE='@aiden0z/pptx-renderer'
VERSION='1.4.0'
NPM_URL='https://registry.npmjs.org/@aiden0z/pptx-renderer/-/pptx-renderer-1.4.0.tgz'
MPL_SOURCE_URL='https://codeload.github.com/ChristopherVR/mtx-decompressor/tar.gz/refs/tags/v1.4.2'
NPM_ENTRY='package/dist/aiden0z-pptx-renderer.browser.es.js'
NPM_NAME='aiden0z-pptx-renderer-1.4.0.tgz'
SOURCE_NAME='mtx-decompressor-v1.4.2-source.tar.gz'
MAX_ARCHIVE=8*1024*1024
MAX_SOURCE=1024*1024


class BuildError(RuntimeError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _relative(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value:
        raise BuildError('Unsafe relative path')
    path=PurePosixPath(value)
    if path.is_absolute() or any(p in ('','.','..') for p in value.split('/')):
        raise BuildError('Unsafe relative path: '+value)
    return path


def _linked(path):
    return path.is_symlink() or bool(getattr(path,'is_junction',lambda:False)())


def _pinned(data,pin,label):
    if type(pin.get('bytes')) is not int or pin['bytes']<0 or len(data)!=pin['bytes'] or _sha(data)!=pin.get('sha256'):
        raise BuildError('Pinned size/SHA256 mismatch: '+label)


class _PinnedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if newurl!=req.full_url:
            raise BuildError('Unexpected download redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def _open_request(request,timeout):
    return urllib.request.build_opener(_PinnedRedirect()).open(request,timeout=timeout)


def _download(url,*,max_bytes,timeout=60):
    if url not in (NPM_URL,MPL_SOURCE_URL):
        raise BuildError('Download URL is not a pinned HTTPS source')
    deadline=time.monotonic()+timeout
    request=urllib.request.Request(url,headers={'User-Agent':'PDF-Extractor-vendor-build'})
    try:
        with _open_request(request,min(timeout,10)) as response:
            if response.geturl()!=url:
                raise BuildError('Download response escaped the pinned URL')
            size=response.headers.get('Content-Length')
            if size is not None and (not str(size).isdigit() or int(size)>max_bytes):
                raise BuildError('Download Content-Length exceeds budget')
            result=bytearray()
            while True:
                if time.monotonic()>deadline:
                    raise BuildError('Download deadline exceeded')
                block=response.read(min(65536,max_bytes-len(result)+1))
                if not block:
                    break
                result.extend(block)
                if len(result)>max_bytes:
                    raise BuildError('Download stream exceeds budget')
            return bytes(result)
    except BuildError:
        raise
    except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException) as exc:
        raise BuildError('Pinned download failed: '+str(exc)) from exc


def _obtain(source_cache,name,url,pin,limit):
    if type(pin.get('bytes')) is not int or not 0<pin['bytes']<=limit:
        raise BuildError('Invalid archive size budget')
    cached=source_cache/name if source_cache is not None else None
    if cached is not None and cached.exists():
        if not cached.is_file() or cached.stat().st_size>limit:
            raise BuildError('Cached archive exceeds budget')
        data=cached.read_bytes()
    else:
        data=_download(url,max_bytes=limit)
    _pinned(data,pin,name)
    return data


def _bundle_from_archive(data,pin):
    found=None
    try:
        with tarfile.open(fileobj=io.BytesIO(data),mode='r|gz') as archive:
            scanned=0
            for member in archive:
                scanned+=member.size+512
                if scanned>64*1024*1024:
                    raise BuildError('Archive expansion exceeds budget')
                if member.name!=NPM_ENTRY:
                    continue
                if not member.isfile() or member.size!=pin['bytes'] or member.size>MAX_ARCHIVE:
                    raise BuildError('Invalid exact browser bundle entry')
                found=archive.extractfile(member).read(member.size+1)
                break
    except (OSError,tarfile.TarError,EOFError) as exc:
        raise BuildError('Cannot read pinned npm entry') from exc
    if found is None:
        raise BuildError('Pinned npm entry is missing')
    _pinned(found,pin,'renderer.mjs')
    return found


def _load_manifest(path):
    try:
        raw=path.read_bytes()
        if len(raw)>1024*1024:
            raise BuildError('Manifest exceeds budget')
        spec=json.loads(raw)
        if spec.get('schema_version')!=1 or spec.get('engine')!=ENGINE or spec.get('version')!=VERSION or spec.get('vendor_patches') is not False:
            raise BuildError('Unsupported renderer identity')
        if spec['bundle']['npm_entry']!=NPM_ENTRY:
            raise BuildError('Unexpected npm browser entry')
        packages=[p for p in spec['packages'] if p['name']==ENGINE and p['version']==VERSION]
        if len(packages)!=1 or packages[0]['tarball_url']!=NPM_URL:
            raise BuildError('Unexpected npm archive identity')
        sources=spec['required_source_archives']
        if len(sources)!=1 or sources[0]['url']!=MPL_SOURCE_URL or sources[0]['name']!='mtx-decompressor' or sources[0]['version']!='1.4.2' or sources[0].get('required') is not True:
            raise BuildError('Missing pinned MPL corresponding source')
        return spec,raw,packages[0],sources[0]
    except BuildError:
        raise
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise BuildError('Cannot read renderer manifest: '+str(exc)) from exc


def _notices(spec):
    root=REPO_ROOT.resolve()
    base=root/'tools/third_party_licenses/pptx_renderer'
    prefix=('tools','third_party_licenses','pptx_renderer')
    files={}
    entries=[n for p in spec['packages'] for n in p['notices']]+[spec['distribution_notice']]
    for notice in entries:
        rel=_relative(notice['path'])
        if tuple(rel.parts[:3])!=prefix or len(rel.parts)<4:
            raise BuildError('Notice is outside the pinned notice directory')
        source=root.joinpath(*rel.parts)
        resolved=source.resolve()
        if not resolved.is_relative_to(base) or not resolved.is_relative_to(root) or _linked(source):
            raise BuildError('Notice path escapes the repository')
        try:
            if source.stat().st_size>1024*1024:
                raise BuildError('Notice exceeds budget')
            data=source.read_bytes()
        except OSError as exc:
            raise BuildError('Required notice is missing: '+str(rel)) from exc
        _pinned(data,notice,str(rel))
        target=PurePosixPath('licenses',*rel.parts[3:]).as_posix()
        if target in files:
            raise BuildError('Duplicate notice destination')
        files[target]=data
    return files


def _output_path(out):
    absolute=Path(os.path.abspath(out))
    resolved=absolute.resolve()
    repo=REPO_ROOT.resolve()
    if resolved==resolved.parent or resolved.is_relative_to(repo) or repo.is_relative_to(resolved):
        raise BuildError('Renderer output must be outside the repository and its ancestors')
    if _linked(absolute) or absolute!=resolved:
        raise BuildError('Renderer output cannot pass through a link/junction')
    return resolved


def _runtime_manifest(spec,raw,pins):
    return {'schema_version':1,'engine':ENGINE,'version':VERSION,'sha256':spec['bundle']['sha256'],
            'bytes':spec['bundle']['bytes'],'vendor_patches':False,'source_manifest_sha256':_sha(raw),
            'notice_count':sum(len(p['notices']) for p in spec['packages']),
            'distribution_notice':True,'mpl_source_included':True,'files':pins}


def _verify_output(out,pins,runtime):
    expected={p['path'] for p in pins}|{'manifest.json'}
    actual=set()
    for path in out.rglob('*'):
        if _linked(path) or not path.resolve().is_relative_to(out):
            raise BuildError('Existing renderer contains a link/escape')
        if path.is_file():
            actual.add(path.relative_to(out).as_posix())
    if actual!=expected:
        raise BuildError('Existing renderer has missing or unexpected files; preserved unchanged')
    for pin in pins:
        path=out.joinpath(*_relative(pin['path']).parts)
        if path.stat().st_size!=pin['bytes']:
            raise BuildError('Existing renderer file size mismatch; preserved unchanged')
        _pinned(path.read_bytes(),pin,pin['path'])
    identity=out/'manifest.json'
    if identity.stat().st_size>1024*1024 or json.loads(identity.read_bytes())!=runtime:
        raise BuildError('Existing renderer identity mismatch; preserved unchanged')


def build(out,*,source_cache=None,manifest_path=None):
    """Return a verified package identity; never replace a different existing tree."""
    out=_output_path(Path(out))
    manifest_path=Path(manifest_path) if manifest_path is not None else REPO_ROOT/'tools/presentation-renderer-manifest.json'
    try:
        spec,raw,npm,source=_load_manifest(manifest_path)
        files=_notices(spec)
        files['licenses/presentation-renderer-manifest.json']=raw
        pins=[{'path':path,'bytes':len(data),'sha256':_sha(data)} for path,data in files.items()]
        pins.extend([{'path':'renderer.mjs','bytes':spec['bundle']['bytes'],'sha256':spec['bundle']['sha256']},
                     {'path':'licenses/sources/'+SOURCE_NAME,'bytes':source['bytes'],'sha256':source['sha256']}])
        pins.sort(key=lambda p:p['path'])
        runtime=_runtime_manifest(spec,raw,pins)
        if out.exists():
            if not out.is_dir():
                raise BuildError('Existing output is not a directory; preserved unchanged')
            _verify_output(out,pins,runtime)
            return {**runtime,'out':str(out),'reused':True}
        cache=Path(source_cache) if source_cache is not None else None
        archive=_obtain(cache,NPM_NAME,NPM_URL,{'bytes':npm['tarball_bytes'],'sha256':npm['tarball_sha256']},MAX_ARCHIVE)
        sri='sha512-'+base64.b64encode(hashlib.sha512(archive).digest()).decode()
        if sri!=npm['integrity']:
            raise BuildError('Npm archive SHA512 integrity mismatch')
        files['renderer.mjs']=_bundle_from_archive(archive,spec['bundle'])
        files['licenses/sources/'+SOURCE_NAME]=_obtain(cache,SOURCE_NAME,MPL_SOURCE_URL,source,MAX_SOURCE)
        out.parent.mkdir(parents=True,exist_ok=True)
        # A sibling stage keeps the final rename on the same filesystem.
        with tempfile.TemporaryDirectory(prefix='.pptx-renderer-stage-',dir=out.parent) as work:
            stage=Path(work)/'engine';stage.mkdir()
            for name,data in files.items():
                path=stage.joinpath(*_relative(name).parts)
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            (stage/'manifest.json').write_text(json.dumps(runtime,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            _verify_output(stage,pins,runtime)
            if out.exists():
                raise BuildError('Output appeared during build; preserved unchanged')
            os.rename(stage,out)
        return {**runtime,'out':str(out),'reused':False}
    except BuildError:
        raise
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise BuildError('Renderer package build failed: '+str(exc)) from exc


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--source-cache',type=Path,help='Optional existing archive directory; absolute manifest cache paths are ignored')
    args=parser.parse_args(argv)
    try:
        result=build(args.out,source_cache=args.source_cache)
        summary={key:value for key,value in result.items() if key!='files'}
        print(json.dumps({'ok':True,**summary,'file_count':len(result['files'])+1},ensure_ascii=True))
        return 0
    except BuildError as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=True))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
