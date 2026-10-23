"""Actual hidden WebView2 smoke; synthetic source and isolated app store only."""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import fitz
import webview

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('native_app', Path(__file__).resolve().parents[1] / 'app.py')
appmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appmod)
from pubnexus import general_pdf, store


def main():
    receipt = {}
    with tempfile.TemporaryDirectory(prefix='native-document-') as temp:
        root = Path(temp)
        inputs = root / 'inputs'
        inputs.mkdir()
        source = inputs / 'Native source.pdf'
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'Native WebView2 source verification 7391.')
            pdf.save(source)
        os.environ['PUBNEXUS_STORE'] = str(root / 'cache')
        os.environ['PUBNEXUS_OUTPUT'] = str(inputs / 'Markdown')
        appmod.utils.load_config = lambda: {}
        sha = store.file_sha1(source)
        doc, _ = general_pdf.ensure_fulltext({'sha1': sha, 'meta': {'title': 'Native smoke'}}, source)
        store.save(sha, doc, source)
        app, window, boot = appmod.build(inputs, hidden=True)
        app.grobid.state = 'off'
        loaded = threading.Event()
        window.events.loaded += lambda: loaded.set()

        def smoke():
            try:
                if not loaded.wait(30):
                    raise TimeoutError('Native WebView did not load')
                # Skip cosmetic caption painting in an intentionally hidden window.
                app.set_folder(inputs)
                deadline = time.monotonic() + 10
                while not window.evaluate_js('!!(window.pywebview && window.pywebview.api && window.pywebview.api.state)'):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Native bridge did not become ready')
                    time.sleep(.05)
                window.evaluate_js("window.__nativeSmoke=null; window.pywebview.api.extract_selected([0]).then(r=>window.__nativeSmoke=r).catch(e=>window.__nativeSmoke={ok:false,why:String(e)}); true")
                deadline = time.monotonic() + 10
                response = None
                while response is None:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Native API bridge did not return')
                    response = window.evaluate_js('window.__nativeSmoke')
                    time.sleep(.05)
                if not response['ok']:
                    raise AssertionError('Native job did not start')
                app.worker.join(30)
                if app.worker.is_alive():
                    raise TimeoutError('Native extraction did not finish')
                row = app.file_rows()[0]
                if not row['done']:
                    raise AssertionError(str(row))
                ui = window.evaluate_js("({title:document.title,rows:document.querySelectorAll('.file-row').length,status:document.querySelector('.file-row .status')?.textContent,bridge:!!window.pywebview.api.state})")
                receipt.update(ok=True, ui=ui, js_to_native_call=response, markdown_saved=Path(row['markdown']).is_file(),
                               source_unchanged=store.file_sha1(source) == sha)
                assert ui['rows'] == 1 and ui['status'] == '저장 완료' and ui['bridge']
                assert receipt['markdown_saved'] and receipt['source_unchanged']
            except Exception as error:
                receipt.update(ok=False, error=f'{type(error).__name__}: {error}')
            finally:
                app.renderer.close_all()
                window.destroy()

        webview.start(smoke, gui='edgechromium', debug=False)
    print(json.dumps(receipt, ensure_ascii=True), flush=True)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    if not receipt.get('ok'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
