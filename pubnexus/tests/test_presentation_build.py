"""Offline boundaries for the pinned browser-renderer packaging tool."""
from contextlib import ExitStack
import base64
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

TOOL=Path(__file__).resolve().parents[2]/'tools/build_presentation_renderer.py'
spec=importlib.util.spec_from_file_location('presentation_builder',TOOL)
builder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def sha(data):return hashlib.sha256(data).hexdigest()


class PresentationBuild(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pnx-presentation-build-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.repo=self.root/'repo';self.repo.mkdir()
        self.notices=self.repo/'tools/third_party_licenses/pptx_renderer'
        self.notices.mkdir(parents=True)
        (self.notices/'LICENSE').write_bytes(b'fixture Apache license')
        (self.notices/'PPTX_RENDERER_NOTICES.md').write_bytes(b'fixture distribution source offer')
        self.cache=self.root/'archives';self.cache.mkdir()
        self.out=self.root/'engine'
        self.bundle=b'export const pinned = true;\n'
        self.archive=self.cache/'aiden0z-pptx-renderer-1.4.0.tgz'
        self.write_archive(self.bundle)
        source=b'exact corresponding source archive'
        (self.cache/'mtx-decompressor-v1.4.2-source.tar.gz').write_bytes(source)
        self.manifest=self.repo/'tools/presentation-renderer-manifest.json'
        self.data={'schema_version':1,'engine':'@aiden0z/pptx-renderer','version':'1.4.0','vendor_patches':False,
                   'bundle':{'npm_entry':'package/dist/aiden0z-pptx-renderer.browser.es.js','sha256':sha(self.bundle),'bytes':len(self.bundle),'cache_path':'Z:/nonportable-do-not-use'},
                   'packages':[{'name':'@aiden0z/pptx-renderer','version':'1.4.0','tarball_url':builder.NPM_URL,'tarball_sha256':'','tarball_bytes':0,'integrity':'','archive_cache_path':'Z:/nonportable-do-not-use','notices':[self.notice('LICENSE')]}],
                   'distribution_notice':self.notice('PPTX_RENDERER_NOTICES.md'),
                   'required_source_archives':[{'name':'mtx-decompressor','version':'1.4.2','url':builder.MPL_SOURCE_URL,'sha256':sha(source),'bytes':len(source),'required':True,'cache_path':'Z:/nonportable-do-not-use'}]}
        self.update_archive_pins()
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(builder,'REPO_ROOT',self.repo))

    def notice(self,name):
        data=(self.notices/name).read_bytes()
        return {'path':'tools/third_party_licenses/pptx_renderer/'+name,'sha256':sha(data),'bytes':len(data)}

    def write_archive(self,data):
        with tarfile.open(self.archive,'w:gz') as tar:
            entry=tarfile.TarInfo('package/dist/aiden0z-pptx-renderer.browser.es.js')
            entry.size=len(data);tar.addfile(entry,io.BytesIO(data))

    def update_archive_pins(self):
        data=self.archive.read_bytes();entry=self.data['packages'][0]
        entry.update(tarball_sha256=sha(data),tarball_bytes=len(data),integrity='sha512-'+base64.b64encode(hashlib.sha512(data).digest()).decode())
        self.manifest.write_text(json.dumps(self.data),encoding='utf-8')

    def build(self):
        return builder.build(self.out,source_cache=self.cache,manifest_path=self.manifest)

    def tree(self):
        return {p.relative_to(self.out).as_posix():(p.read_bytes(),p.stat().st_mtime_ns) for p in self.out.rglob('*') if p.is_file()}

    def test_offline_exact_build_and_valid_output_reuse(self):
        with patch.object(builder,'_download',side_effect=AssertionError('No network expected')):
            result=self.build()
            self.assertEqual((self.out/'renderer.mjs').read_bytes(),self.bundle)
            self.assertEqual((self.out/'licenses/LICENSE').read_bytes(),b'fixture Apache license')
            self.assertEqual((self.out/'licenses/presentation-renderer-manifest.json').read_bytes(),self.manifest.read_bytes())
            self.assertEqual(result['version'],'1.4.0')
            before=self.tree();again=self.build()
        self.assertTrue(again['reused'])
        self.assertEqual(self.tree(),before)

    def test_corrupt_cache_fails_closed_and_preserves_broken_existing_output(self):
        self.archive.write_bytes(b'tampered archive')
        with patch.object(builder,'_download',side_effect=AssertionError('Do not hide corrupt cache')),self.assertRaisesRegex(builder.BuildError,'Pinned size/SHA256 mismatch'):
            self.build()
        self.assertFalse(self.out.exists())
        self.out.mkdir();(self.out/'keep.txt').write_bytes(b'old broken output, preserve')
        before=self.tree()
        with patch.object(builder,'_download',side_effect=AssertionError('Do not hide corrupt cache')),self.assertRaises(builder.BuildError):self.build()
        self.assertEqual(self.tree(),before)

    def test_exact_archive_does_not_authorize_different_bundle_entry(self):
        self.write_archive(b'wrong or patched browser bundle');self.update_archive_pins()
        with self.assertRaises(builder.BuildError):self.build()
        self.assertFalse(self.out.exists())

    def test_missing_notice_keeps_previously_valid_output_unchanged(self):
        self.build();before=self.tree();(self.notices/'LICENSE').unlink()
        with self.assertRaises(builder.BuildError):self.build()
        self.assertEqual(self.tree(),before)

    def test_rejects_repository_output_and_notice_link_escape(self):
        with self.assertRaises(builder.BuildError):builder.build(self.repo/'generated',source_cache=self.cache,manifest_path=self.manifest)
        outside=self.root/'outside-license';outside.write_bytes(b'fixture Apache license')
        (self.notices/'LICENSE').unlink()
        try:(self.notices/'LICENSE').symlink_to(outside)
        except OSError as exc:self.skipTest(str(exc))
        with self.assertRaises(builder.BuildError):self.build()
        self.assertFalse(self.out.exists())

    def test_publish_failure_removes_only_owned_staging(self):
        with patch.object(builder.os,'rename',side_effect=OSError('simulated publish failure')),self.assertRaises(builder.BuildError):self.build()
        self.assertFalse(self.out.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['archives','repo'])

    def test_download_enforces_stream_budget_redirect_and_deadline(self):
        class Response(io.BytesIO):
            headers={}
            def geturl(self):return builder.NPM_URL
        with patch.object(builder,'_open_request',return_value=Response(b'x'*65)):
            with self.assertRaises(builder.BuildError):builder._download(builder.NPM_URL,max_bytes=64,timeout=1)
        class UnsafeResponse(Response):
            def geturl(self):return 'https://attacker.example/payload'
        with patch.object(builder,'_open_request',return_value=UnsafeResponse(b'payload')):
            with self.assertRaises(builder.BuildError):builder._download(builder.NPM_URL,max_bytes=64,timeout=1)
        with patch.object(builder,'_open_request',return_value=Response(b'x')),patch.object(builder.time,'monotonic',side_effect=[0,10]):
            with self.assertRaises(builder.BuildError):builder._download(builder.NPM_URL,max_bytes=64,timeout=1)

    def test_truncated_http_and_off_host_redirect_are_typed_failures(self):
        class Truncated(io.BytesIO):
            headers={}
            def geturl(self):return builder.NPM_URL
            def read(self,*args):raise http.client.IncompleteRead(b'partial',9)
        with patch.object(builder,'_open_request',return_value=Truncated()):
            with self.assertRaises(builder.BuildError):builder._download(builder.NPM_URL,max_bytes=64,timeout=1)
        request=builder.urllib.request.Request(builder.NPM_URL)
        with self.assertRaises(builder.BuildError):
            builder._PinnedRedirect().redirect_request(request,None,302,'redirect',{},'https://attacker.example/payload')


if __name__=='__main__':unittest.main()
