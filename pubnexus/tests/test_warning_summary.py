import unittest


class WarningSummaryTests(unittest.TestCase):
    def test_table_or_image_failure_mentions_ocr_but_is_not_an_ocr_status(self):
        from pubnexus.warning_summary import summarize_warnings
        failure = '3쪽 표 검출 실패 — OCR table coordinates unavailable'
        result = summarize_warnings([failure], [{'page': 3, 'status': 'ocr_extracted', 'ocr_performed': True}])
        self.assertIn(failure, result)

    def test_old_ocr_attempt_with_no_words_is_not_reported_as_never_run(self):
        from pubnexus.warning_summary import summarize_warnings
        generic = 'OCR로 읽은 텍스트입니다. 오인식/누락을 원본과 확인하세요.'
        raw = [generic, '3쪽 텍스트층 없음(빈 쪽 또는 스캔) — OCR 미실행',
               generic, '4쪽 텍스트층 없음(빈 쪽 또는 스캔) — OCR 미실행']
        pages = [{'page': p, 'status': 'empty', 'ocr_performed': True} for p in (3, 4)]
        result = summarize_warnings(raw, pages)
        text = '\n'.join(result)
        self.assertNotIn('미실행', text)
        self.assertNotIn('OCR로 읽은 텍스트입니다', text)
        self.assertIn('3–4', text)
        self.assertIn('문자를 찾지 못', text)
        self.assertEqual(len(result), 1)

    def test_real_ocr_warning_is_once_and_unrun_failure_remains_distinct(self):
        from pubnexus.warning_summary import summarize_warnings
        generic = 'OCR로 읽은 텍스트입니다. 오인식/누락을 원본과 확인하세요.'
        result = summarize_warnings([generic, generic, '2쪽 로컬 OCR — 인식 결과 원문 대조 필요',
                                    '9쪽 Tesseract 미설치/검색불가 — OCR 미실행', '디스크 오류', '디스크 오류'],
                                   [{'page': 2, 'status': 'ocr_extracted', 'ocr_performed': True},
                                    {'page': 9, 'status': 'empty', 'ocr_performed': False}])
        self.assertEqual(sum('오인식' in s for s in result), 1)
        self.assertTrue(any('미실행' in s and '9' in s for s in result))
        self.assertEqual(result.count('디스크 오류'), 1)


if __name__ == '__main__':
    unittest.main()
