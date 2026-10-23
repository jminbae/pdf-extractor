import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from pubnexus import preview, store


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'PUBNEXUS_STORE': str(self.root / 'cache')})
        env.start()
        self.addCleanup(env.stop)
        self.source = self.root / 'source.docx'
        self.source.write_bytes(b'synthetic document input')
        self.engine = self.root / 'soffice.exe'
        self.engine.write_bytes(b'synthetic engine')

    def test_missing_engine_reports_dependency_without_touching_original(self):
        with patch.object(preview, 'find_engine', return_value=None):
            with self.assertRaisesRegex(preview.PreviewUnavailable, 'LibreOffice'):
                preview.preview_path(self.source)
        self.assertEqual(self.source.read_bytes(), b'synthetic document input')

    def test_failed_conversion_is_not_reused_as_a_valid_preview(self):
        with patch.object(preview, 'find_engine', return_value=self.engine), \
             patch.object(preview, '_run', side_effect=preview.PreviewUnavailable('변환 실패')) as run:
            for _ in range(2):
                with self.assertRaises(preview.PreviewUnavailable):
                    preview.preview_path(self.source)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(list((store.root() / 'previews').rglob('preview.pdf')), [])

    def test_completed_preview_cache_does_not_reconvert(self):
        def convert(args, **kwargs):
            from urllib.parse import urlparse, unquote
            profile_arg = next(str(x).split('=', 1)[1] for x in args if str(x).startswith('-env:UserInstallation='))
            profile_path = Path(unquote(urlparse(profile_arg).path).lstrip('/') if os.name == 'nt' else unquote(urlparse(profile_arg).path))
            self.assertFalse(profile_path.is_relative_to(kwargs['work']), 'Windows LO profile must use a short separate TEMP path')
            output = Path(args[args.index('--outdir') + 1]) / (Path(args[-1]).stem + '.pdf')
            with fitz.open() as pdf:
                pdf.new_page().insert_text((72, 72), 'Source preview')
                pdf.save(output)
        with patch.object(preview, 'find_engine', return_value=self.engine), \
             patch.object(preview, '_run', side_effect=convert) as run:
            one = preview.preview_path(self.source)
            two = preview.preview_path(self.source)
        self.assertEqual(one.path, two.path)
        self.assertTrue(one.converted)
        self.assertEqual(run.call_count, 1)

    def test_cancelled_conversion_publishes_no_preview(self):
        with patch.object(preview, 'find_engine', return_value=self.engine):
            with self.assertRaises(preview.PreviewUnavailable):
                preview.preview_path(self.source, cancel=lambda: True)
        self.assertEqual(list((store.root() / 'previews').rglob('preview.pdf')), [])

    def test_private_profile_disables_scripts_and_automatic_external_link_updates(self):
        from lxml import etree
        profile = self.root / 'profile'
        preview._profile(profile)
        root = etree.parse(str(profile / 'user' / 'registrymodifications.xcu'))
        ns = {'oor': 'http://openoffice.org/2001/registry'}
        values = {item.get('{'+ns['oor']+'}path') + '/' + prop.get('{'+ns['oor']+'}name'): prop.findtext('value')
                  for item in root.getroot() for prop in item}
        self.assertEqual(values['/org.openoffice.Office.Common/Security/Scripting/DisableMacrosExecution'], 'true')
        self.assertEqual(values['/org.openoffice.Office.Calc/Content/Update/Link'], '1')  # Calc schema: never
        self.assertEqual(values['/org.openoffice.Office.Writer/Content/Update/Link'], '2')  # Writer schema: never
