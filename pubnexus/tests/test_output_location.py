import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


class OutputLocationTests(unittest.TestCase):
    def test_redirected_windows_shortcuts_do_not_probe_network_folders(self):
        import ctypes
        from pubnexus import output_location
        redirected = r'\\offline-server\user\documents'
        def found(guid, flags, token, result):
            result._obj.value = redirected
            return 0
        lookup, free = Mock(side_effect=found), Mock()
        with patch.object(output_location.sys, 'platform', 'win32'), \
             patch.object(ctypes.windll.shell32, 'SHGetKnownFolderPath', lookup), \
             patch.object(ctypes.windll.ole32, 'CoTaskMemFree', free):
            for kind in ('documents', 'desktop', 'downloads'):
                self.assertEqual(output_location.known_directory(kind), Path(redirected))
        self.assertEqual([call.args[1] for call in lookup.call_args_list], [0x4000] * 3)
        self.assertEqual(free.call_count, 3)

    def test_known_folder_fallbacks_keep_documents_desktop_and_downloads_distinct(self):
        from pubnexus import output_location
        self.assertTrue(callable(getattr(output_location, 'known_directory', None)))
        with patch.object(output_location.sys, 'platform', 'other'), \
             patch.object(output_location.Path, 'home', return_value=Path('private-home')):
            for kind, folder in [('documents', 'Documents'), ('desktop', 'Desktop'), ('downloads', 'Downloads')]:
                self.assertEqual(output_location.known_directory(kind), Path('private-home') / folder)

    def test_default_uses_documents_known_folder(self):
        from pubnexus.output_location import default_output_dir
        with patch.dict(os.environ, {'PUBNEXUS_OUTPUT': ''}), \
             patch('pubnexus.output_location.documents_directory', return_value=Path('redirected-documents')):
            self.assertEqual(default_output_dir(), Path('redirected-documents') / '문서 추출기')

    def test_explicit_output_override_is_resolved(self):
        from pubnexus.output_location import default_output_dir
        with patch.dict(os.environ, {'PUBNEXUS_OUTPUT': 'isolated-output'}):
            self.assertEqual(default_output_dir(), Path('isolated-output').resolve())
