"""Caption text probing must not decode embedded image payloads."""
import random
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pubnexus import captions


class CaptionTextOnlyTests(unittest.TestCase):
    def test_native_caption_lines_keep_text_geometry_without_image_bytes(self):
        with tempfile.TemporaryDirectory(prefix='pubnexus-caption-text-') as temporary:
            source = Path(temporary) / 'synthetic-image-caption.pdf'
            with fitz.open() as pdf:
                page = pdf.new_page()
                pixels = random.Random(7391).randbytes(256 * 256 * 3)
                image = fitz.Pixmap(fitz.csRGB, 256, 256, pixels, False)
                page.insert_image(fitz.Rect(40, 40, 240, 240), stream=image.tobytes('png'))
                page.insert_text((40, 270), 'Fig. 1. Synthetic image caption with text retained.', fontsize=9)
                page.insert_text((40, 300), 'Original body text and number 7391 remain unchanged.', fontsize=11)
                pdf.save(source)
            original = source.read_bytes()
            with fitz.open(source) as pdf:
                page = pdf[0]
                baseline = page.get_text('dict')
                self.assertTrue(any(block.get('image') for block in baseline['blocks']))
                expected = [span['text'] for block in baseline['blocks'] if block['type'] == 0
                            for line in block['lines'] for span in line['spans']]
                received = []

                class TextProbe:
                    rect = page.rect

                    def get_text(self, *args, **kwargs):
                        data = page.get_text(*args, **kwargs)
                        received.append(data)
                        return data

                lines = captions._page_lines(TextProbe(), 0)
                self.assertEqual([line.text for line in lines], expected)
                expected_boxes = [tuple(line['bbox']) for block in baseline['blocks'] if block['type'] == 0
                                  for line in block['lines']]
                self.assertEqual([(line.x0, line.y0, line.x1, line.y1) for line in lines], expected_boxes)
                self.assertFalse(any(block.get('image') for data in received for block in data['blocks']),
                                 'Caption probing must request text-only dict flags before image payload allocation')
            self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
