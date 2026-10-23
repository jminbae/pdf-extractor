import os
from pathlib import Path
import unittest
from unittest.mock import patch


class OutputLocationTests(unittest.TestCase):
    def test_default_uses_documents_known_folder(self):
        from pubnexus.output_location import default_output_dir
        with patch.dict(os.environ, {'PUBNEXUS_OUTPUT': ''}), \
             patch('pubnexus.output_location.documents_directory', return_value=Path('redirected-documents')):
            self.assertEqual(default_output_dir(), Path('redirected-documents') / '문서 추출기')

    def test_explicit_output_override_is_resolved(self):
        from pubnexus.output_location import default_output_dir
        with patch.dict(os.environ, {'PUBNEXUS_OUTPUT': 'isolated-output'}):
            self.assertEqual(default_output_dir(), Path('isolated-output').resolve())
