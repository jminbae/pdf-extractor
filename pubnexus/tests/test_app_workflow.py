import hashlib
import os
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import test_app_export as support
from pubnexus import store


class WorkflowTests(unittest.TestCase):
    setUp = support.AppExportTests.setUp
    source = support.AppExportTests.source
    extracted = support.AppExportTests.extracted

    def test_start_extracts_in_a_child_without_running_gui_process_pipeline(self):
        source = self.root / 'background.csv'
        source.write_text('Name,Value\nBackground,713\n', encoding='utf-8')
        self.app.pdfs = [source]
        with patch.object(self.app, '_work', side_effect=AssertionError('GUI must not execute the extraction pipeline')):
            self.app.start([source])
            self.app.worker.join(timeout=20)
        self.assertFalse(self.app.worker.is_alive())
        self.assertTrue((self.root / 'Markdown/background.md').is_file())
        started = [payload for kind, payload in self.events if kind == 'worker_started']
        self.assertTrue(started)
        self.assertNotEqual(started[0]['pid'], os.getpid())

    def test_each_document_uses_a_fresh_child_process(self):
        sources = [self.root / name for name in ('one.csv', 'two.csv')]
        for source in sources:
            source.write_text('Name,Value\n' + source.stem + ',713\n', encoding='utf-8')
        self.app.pdfs = sources
        self.app.start(sources)
        self.app.worker.join(timeout=20)
        self.assertFalse(self.app.worker.is_alive())
        started = [payload for kind, payload in self.events if kind == 'worker_started']
        self.assertEqual(len(started), 2, 'Each document needs its own process boundary')
        self.assertEqual(len({item['pid'] for item in started}), 2)
        self.assertTrue(all(row['done'] for row in self.app.file_rows()))

    def test_enqueue_during_run_deduplicates_and_a_crashed_document_cannot_stop_next(self):
        from pubnexus import extraction_process
        first = self.root / 'crashing.csv'
        second = self.root / 'next.csv'
        for source in (first, second):
            source.write_text('Name,Value\n' + source.stem + ',713\n', encoding='utf-8')
        self.app.pdfs = [first, second]
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real_run = extraction_process.run
        calls = []

        def run(paths, output_dir, **kwargs):
            calls.append(list(paths))
            if paths == [first]:
                entered.set()
                release.wait(5)
                raise RuntimeError('Synthetic worker crash')
            return real_run(paths, output_dir, **kwargs)

        with patch.object(extraction_process, 'run', side_effect=run):
            self.app.start([first])
            self.assertTrue(entered.wait(3))
            api = support.app_module.Api(self.app)
            added = api.extract_selected([1], True)
            self.assertTrue(added['ok'], added)
            self.assertTrue(added['queued'])
            self.assertEqual(added['n'], 1)
            duplicate = api.extract_selected([0, 1], True)
            self.assertTrue(duplicate['ok'], duplicate)
            self.assertEqual(duplicate['n'], 0)
            release.set()
            self.app.worker.join(timeout=20)
        self.assertFalse(self.app.worker.is_alive())
        self.assertEqual(calls, [[first], [second]])
        rows = self.app.file_rows()
        self.assertEqual(rows[0]['status'], 'failed')
        self.assertTrue(rows[1]['done'])
        final = [payload for kind, payload in self.events if kind == 'run'][-1]
        self.assertEqual((final['done'], final['total'], final['nfail']), (1, 2, 1))

    def test_cancel_stops_queued_documents_before_any_new_process_starts(self):
        from pubnexus import extraction_process
        from pubnexus.general_pdf import ExtractionCancelled
        sources = [self.root / name for name in ('working.csv', 'queued.csv')]
        for source in sources:
            source.write_text('Name,Value\n' + source.stem + ',713\n', encoding='utf-8')
        self.app.pdfs = sources
        entered = threading.Event()
        calls = []

        def run(paths, output_dir, **kwargs):
            calls.append(list(paths))
            entered.set()
            deadline = time.monotonic() + 5
            while not kwargs['cancel']() and time.monotonic() < deadline:
                time.sleep(.01)
            raise ExtractionCancelled('Stopped')

        with patch.object(extraction_process, 'run', side_effect=run):
            self.app.start(sources)
            self.assertTrue(entered.wait(3))
            support.app_module.Api(self.app).cancel()
            self.app.worker.join(timeout=3)
        self.assertFalse(self.app.worker.is_alive())
        self.assertEqual(calls, [[sources[0]]])
        self.assertTrue(all(row['status'] == 'paused' for row in self.app.file_rows()))

    def test_committed_document_is_reserved_until_child_exit_and_survives_stop(self):
        from pubnexus import extraction_process
        source = self.root / 'saved.csv'
        source.write_text('Name,Value\nSaved,713\n', encoding='utf-8')
        self.app.pdfs = [source]
        self.app._work([source])
        saved = dict(self.app.exported[support.app_module._norm(source)])
        accepted_after_commit = []

        def run(paths, output_dir, **kwargs):
            kwargs['on_event']('mark', dict(saved, path=str(source), name=source.name, md_ready=True))
            accepted_after_commit.extend(self.app.start([source], True))
            self.app.cancel.set()  # Stop requested while this committed child is exiting.
            kwargs['on_event']('run', {'on': False, 'stopped': True, 'nfail': 0})

        with patch.object(extraction_process, 'run', side_effect=run):
            self.app.start([source], True)
            self.app.worker.join(timeout=3)
        self.assertFalse(self.app.worker.is_alive())
        self.assertEqual(accepted_after_commit, [], 'Do not accept a duplicate until the old child exits')
        self.assertEqual(self.app.file_rows()[0]['status'], 'complete')
        self.assertTrue(self.app.file_rows()[0]['done'])

    def test_saved_markdown_remains_readable_while_same_file_is_reextracted(self):
        source = self.source()
        doc = self.extracted(source)
        store.save(doc['sha1'], doc, source)
        self.app.pdfs = [source]
        self.app._work([source])
        self.app._active_path = source
        self.app._pending_paths = {support.app_module._norm(source)}
        self.app._file_progress.clear()
        self.app.prog(1, 1, source.name, '문서 구조 추출', 0, path=source, file_pct=25)
        row = self.app.file_rows()[0]
        self.assertEqual(row['status'], 'running')
        self.assertTrue(row['md_ready'], 'The already saved Markdown must stay readable')
        self.assertFalse(row['done'], 'An active extraction is not finished')
        self.assertEqual(row['progress_pct'], 25)
        self.assertTrue(self.app.doc_view(source)['extracted'])

    def test_file_addition_is_available_during_extraction_without_changing_targets(self):
        first = self.source()
        second = self.root / 'notes.md'
        second.write_text('A separate readable document.', encoding='utf-8')
        self.app.pdfs = [first]
        release = threading.Event()
        self.addCleanup(release.set)
        self.app.worker = threading.Thread(target=release.wait, daemon=True)
        self.app.worker.start()
        self.app._pending_paths = {support.app_module._norm(first)}
        self.app.add_files([second])
        self.assertEqual(self.app.pdfs, [first, second])
        self.assertEqual(self.app._pending_paths, {support.app_module._norm(first)})
        self.assertIn('key', self.app.source_view(0))
        self.addCleanup(self.app.renderer.close_all)

    def test_open_original_before_extraction_never_reads_or_queues_markdown(self):
        source = self.source()
        self.app.pdfs = [source]
        self.addCleanup(self.app.renderer.close_all)
        with patch.object(self.app, 'doc_view', side_effect=AssertionError('No Markdown on original open')), \
             patch.object(self.app, 'sha_for', side_effect=AssertionError('No extraction cache on original open')), \
             patch.object(self.app, 'bump', side_effect=AssertionError('Reading never changes queue')):
            result = self.app.source_view(0)
        self.assertEqual(len(result['pages']), 1)
        self.assertIn('key', result)
        self.assertNotIn('doc', result)
        self.assertFalse(self.app.done)
        self.assertFalse(self.app.exported)
        self.assertFalse(self.app.file_rows()[0]['md_ready'])

    def test_original_search_works_before_markdown_exists(self):
        source = self.source()
        self.app.pdfs = [source]
        with patch.object(self.app, 'comparison_doc', side_effect=AssertionError('No extracted result required')):
            result = support.app_module.Api(self.app).search_source(0, 'offline')
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['results'])
        self.assertTrue(all(hit['side'] == 'source' for hit in result['results']))
        self.assertEqual(result['results'][0]['page'], 0)
        self.assertFalse(self.app.exported)

    def test_reopened_original_uses_current_bytes_and_retires_previous_images(self):
        import fitz
        source = self.source()
        self.app.pdfs = [source]
        self.addCleanup(self.app.renderer.close_all)
        first = self.app.source_view(0)
        self.assertTrue(self.app.renderer.png(first['key'], 0, 300))
        with fitz.open() as replacement:
            replacement.new_page(width=500, height=600)
            replacement.new_page(width=500, height=600)
            source.write_bytes(replacement.tobytes())
        second = self.app.source_view(0)
        self.assertEqual(second['pages'], [[500.0, 600.0], [500.0, 600.0]])
        self.assertNotEqual(first['key'], second['key'])
        self.assertIsNone(self.app.renderer.png(first['key'], 0, 300))

    def test_row_progress_identifies_path_and_md_is_ready_only_after_save(self):
        source = self.source()
        other = self.root / 'other' / source.name
        other.parent.mkdir()
        other.write_bytes(source.read_bytes())
        self.app.pdfs = [source, other]
        self.app._active_path = source
        self.app.prog(1, 2, source.name, '원문 확인', 0, path=source,
                      file_pct=37.5, page=3, pages=8)
        rows = self.app.file_rows()
        self.assertEqual(rows[0]['status'], 'running')
        self.assertEqual(rows[0]['progress_pct'], 37.5)
        self.assertEqual(rows[0]['progress_page'], 3)
        self.assertEqual(rows[1]['status'], 'ready')
        self.assertFalse(rows[0]['md_ready'])
        event = [p for kind, p in self.events if kind == 'prog'][-1]
        self.assertEqual(event['path'], str(source))
        self.assertEqual(event['file_pct'], 37.5)
        self.app._active_path = None
        doc = self.extracted(source)
        store.save(doc['sha1'], doc, source)
        self.app._work([source])
        self.assertTrue(self.app.file_rows()[0]['md_ready'])
        self.assertEqual(self.app.file_rows()[0]['progress_pct'], 100)

    def test_previous_source_markdown_is_not_mixed_with_current_cached_html(self):
        source = self.source()
        old_doc = self.extracted(source)
        store.save(old_doc['sha1'], old_doc, source)
        self.app.pdfs = [source]
        self.app._work([source])
        markdown = Path(self.app.file_rows()[0]['markdown'])
        old_bytes = markdown.read_bytes()
        source.write_bytes(source.read_bytes() + b'\nChanged revision\n')
        new_doc = self.extracted(source)
        new_doc['body_text'][0]['paragraphs'][0]['text'] = 'Current canonical revision content.'
        store.save(new_doc['sha1'], new_doc, source)
        view = self.app.doc_view(source)
        self.assertIn('Current canonical revision content.', view['html'])
        self.assertIn('Current canonical revision content.', view['md_text'])
        self.assertEqual(markdown.read_bytes(), old_bytes)
        self.assertFalse(self.app.file_rows()[0]['md_ready'])

    def test_legacy_general_pdf_needs_refresh_without_deleting_previous_output(self):
        source = self.source()
        doc = self.extracted(source)
        store.save(doc['sha1'], doc, source)
        self.app.pdfs = [source]
        self.app._work([source])
        record = self.app.exported[support.app_module._norm(source)]
        markdown = Path(record['markdown'])
        before = markdown.read_bytes()
        from pubnexus import pdf_markdown
        record.pop('pdf_conversion_engine', None)
        record.pop('pdf_conversion_attempt', None)
        with patch.object(pdf_markdown, 'available', return_value=True):
            row = self.app.file_rows()[0]
        self.assertTrue(row['needs_refresh'])
        self.assertFalse(row['md_ready'])
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(markdown.read_bytes(), before)
        self.app._work([source])
        self.assertTrue(self.app.file_rows()[0]['md_ready'])


if __name__ == '__main__':
    unittest.main()
