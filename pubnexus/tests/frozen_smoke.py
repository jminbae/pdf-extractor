"""Hidden frozen EXE smoke through its real WebView2 JS bridge.

Run with the development Python, never from unittest discovery. Fixtures and
the app store/profile are TEMP-only; receipts and screenshots use DevCache.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


_CDP_SCRIPT = r'''
import fs from 'node:fs';
import path from 'node:path';
const cfg=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const deadline=Date.now()+cfg.timeout*1000;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label){while(Date.now()<deadline){const value=await fn();if(value)return value;await pause(100);}throw Error('Timed out: '+label);}
let ws;
let previousProbe='';
function probe(value){const serialized=JSON.stringify(value,null,2);if(serialized!==previousProbe){fs.writeFileSync(cfg.probe,serialized,'utf8');previousProbe=serialized;}}
try {
  const target=await until(async()=>{try{const response=await fetch(`http://127.0.0.1:${cfg.port}/json/list`,{signal:AbortSignal.timeout(1500)});const pages=await response.json();probe({status:response.status,targets:pages.map(p=>({id:p.id,type:p.type,url:p.url,title:p.title}))});return pages.find(p=>p.type==='page'&&/^http:\/\/127\.0\.0\.1:\d+\/$/.test(p.url));}catch(error){probe({error:String(error),code:error.cause?.code||''});return null;}},'private WebView2 CDP target');
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('CDP connect timeout')),5000);ws.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});ws.addEventListener('error',()=>{clearTimeout(timer);reject(Error('CDP connection failed'));},{once:true});});
  let next=0;const pending=new Map();
  ws.addEventListener('message',event=>{const message=JSON.parse(String(event.data));const item=pending.get(message.id);if(item){pending.delete(message.id);clearTimeout(item.timer);message.error?item.reject(Error(JSON.stringify(message.error))):item.resolve(message.result);}});
  const command=(method,params={},timeoutMs=15000)=>new Promise((resolve,reject)=>{const id=++next;const timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP command timeout: '+method));},timeoutMs);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});
  async function evaluate(expression){const result=await command('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result?.value;}
  const screenshots=[];
  async function optionalScreenshot(destination){
    try{const shot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},3000);fs.writeFileSync(destination,Buffer.from(shot.data,'base64'));screenshots.push({path:destination,saved:true});}
    catch(error){screenshots.push({path:destination,saved:false,error:String(error)});}
  }
  await command('Runtime.enable');await command('Page.enable');
  await until(()=>evaluate('!!(window.pywebview&&window.pywebview.api&&window.pywebview.api.state)'), 'pywebview bridge');
  const state=await until(async()=>{const s=await evaluate('window.pywebview.api.state()');return s.files?.length===2?s:null;},'two isolated inputs');
  if(path.resolve(state.path).toLowerCase()!==path.resolve(cfg.inputs).toLowerCase())throw Error('Refusing to operate outside private fixture folder');
  if(path.resolve(state.output_dir||'').toLowerCase()!==path.resolve(cfg.output_dir).toLowerCase())throw Error('Refusing to save outside the explicit private output folder');
  if(JSON.stringify(state.files.map(f=>f.name))!==JSON.stringify(cfg.names))throw Error('Unexpected input list');
  if(state.files.some(f=>f.md_ready))throw Error('A private fixture was presented as saved Markdown before export');
  await until(()=>evaluate("document.querySelectorAll('.file-row .row-actions [data-action=read]').length===2"),'always-present Markdown buttons');
  const initialMdButtons=await evaluate("Array.from(document.querySelectorAll('.file-row .row-actions [data-action=read]')).map(b=>({label:b.textContent,disabled:b.disabled}))");
  if(initialMdButtons.some(b=>b.label!=='MD 보기'||!b.disabled))throw Error('Cached extraction enabled an unsaved Markdown button');
  const settings=await evaluate('window.pywebview.api.settings()');
  if(!settings?.ok||settings.current_version!==cfg.expected_version||typeof settings.auto_update!=='boolean'||path.resolve(settings.output_dir||'').toLowerCase()!==path.resolve(cfg.output_dir).toLowerCase())throw Error('Frozen settings API mismatch: '+JSON.stringify(settings));
  await evaluate("document.querySelector('#settings-open').click();true");
  await until(()=>evaluate(`document.querySelector('#settings-dialog')?.open&&document.querySelector('#settings-version').textContent===${JSON.stringify(cfg.expected_version)}&&document.querySelector('#settings-output').value===${JSON.stringify(cfg.output_dir)}`),'real settings dialog');
  const settingsUi=await evaluate("({output:document.querySelector('#settings-output').value,readonly:document.querySelector('#settings-output').readOnly,version:document.querySelector('#settings-version').textContent,auto:document.querySelector('#settings-auto').checked})");
  if(!settingsUi.readonly||settingsUi.auto!==settings.auto_update)throw Error('Frozen settings dialog is not an accurate read-only settings view');
  let manualUpdate=null;
  if(cfg.check_update){
    const check=await evaluate('window.pywebview.api.check_update()');if(!check?.ok)throw Error('Manual update check did not start: '+JSON.stringify(check));
    manualUpdate=await until(async()=>{const current=await evaluate('window.pywebview.api.settings()');const update=current.update||{};if(['idle','checking','downloading'].includes(update.status))return null;if(update.status!=='up_to_date')throw Error('Unexpected manual update state: '+JSON.stringify(update));return update;},'explicit manual update check');
  }
  await evaluate("document.querySelector('#settings-close').click();true");
  await evaluate("document.querySelector('.file-row[data-index=\"0\"] .filename').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));true");
  await until(()=>evaluate("document.querySelector('#reader').classList.contains('source-only')&&document.querySelectorAll('#source-scroll img').length===1"),'original double-click before Markdown exists');
  await until(()=>evaluate("Array.from(document.querySelectorAll('#source-scroll img')).some(i=>{i.loading='eager';return !i.hidden&&i.complete&&i.naturalWidth>0;})"),'original PNG before extraction');
  const original=await evaluate("({source_visible:!document.querySelector('#source-pane').hidden,result_hidden:document.querySelector('#result-pane').hidden,md_disabled:document.querySelector('#content-tab').disabled,editable:document.querySelectorAll('#reader textarea,#reader [contenteditable=true]').length,error_note:document.querySelector('#page-note').textContent})");
  if(!original.source_visible||!original.result_hidden||!original.md_disabled||original.editable||original.error_note)throw Error('Original-only view before export is invalid: '+JSON.stringify(original));
  const originalSearch=await evaluate(`window.pywebview.api.search_source(0,${JSON.stringify(cfg.pdfMarker)})`);
  if(originalSearch?.ok===false||!Array.isArray(originalSearch.results)||(!cfg.scan_mode&&!originalSearch.results.some(hit=>hit.side==='source'&&hit.locations?.length)))throw Error('Original search before export failed: '+JSON.stringify(originalSearch));
  original.search_hits=originalSearch.results.length;original.hidden_test_eager_load=true;
  if((await evaluate('window.pywebview.api.state()')).files.some(f=>f.md_ready))throw Error('Original view/search unexpectedly created saved Markdown');
  await evaluate("document.querySelector('[data-action=list]').click();true");
  const started=await evaluate('window.pywebview.api.extract_selected([0,1])');
  if(!started?.ok||started.n!==2)throw Error('Native extraction did not start: '+JSON.stringify(started));
  const finished=await until(async()=>{const s=await evaluate('window.pywebview.api.state()');const failed=s.files?.find(f=>f.error||f.status==='failed');if(failed)throw Error('Native extraction failed: '+JSON.stringify(failed));return s.files?.length===2&&s.files.every(f=>f.done)?s:null;},'both portable Markdown files saved');
  if(!finished.files.every(f=>f.md_ready===true))throw Error('Saved Markdown readiness was not verified by the backend');
  if(cfg.scan_mode&&(finished.files[0].status!=='partial'||finished.files[1].status!=='complete'||!finished.files[0].warnings.some(w=>/OCR|오인식|인식/.test(String(w)))))throw Error('Scan must preserve its partial status and OCR warning');
  const labels=cfg.scan_mode?['확인 필요','저장 완료']:['저장 완료','저장 완료'];
  await until(()=>evaluate(`(()=>{const nodes=Array.from(document.querySelectorAll('.file-row .status'));const expected=${JSON.stringify(labels)};return nodes.length===2&&nodes.every((node,i)=>node.textContent.includes(expected[i]));})()`),'rendered saved rows');
  await optionalScreenshot(cfg.listShot);
  await evaluate("document.querySelector('[data-action=read][data-index=\"1\"]').click();true");
  await until(()=>evaluate(`document.querySelector('#document')?.innerText.includes(${JSON.stringify(cfg.csvMarker)})&&document.querySelectorAll('#document table tr').length>=2`),'CSV reader table');
  const csvUi=await evaluate("({tables:document.querySelectorAll('#document table').length,editable:document.querySelectorAll('#reader [contenteditable=true],#reader textarea').length,readonly:document.querySelector('#reader .readonly')?.textContent,content:document.querySelector('#document').innerText})");
  if(csvUi.editable||!csvUi.readonly?.includes('읽기 전용'))throw Error('Result reader is editable or lacks its read-only label');
  await evaluate("document.querySelector('#code-tab').click();true");
  const code=await evaluate("({tag:document.querySelector('#md-source').tagName,text:document.querySelector('#md-source').textContent,editable:document.querySelector('#md-source').isContentEditable})");
  if(code.tag!=='PRE'||code.editable||!code.text.includes(cfg.csvMarker))throw Error('Raw Markdown read-only view failed');
  await evaluate("document.querySelector('[data-action=list]').click();document.querySelector('[data-action=read][data-index=\"0\"]').click();true");
  await until(()=>evaluate(`document.querySelector('#document')?.innerText.includes(${JSON.stringify(cfg.pdfMarker)})`),'PDF Markdown reader');
  await evaluate("document.querySelector('#compare-tab').click();true");
  // A deliberately hidden WebView need not activate browser lazy-loading.
  // Eager loading is confined to this test and still decodes the real PNG URL.
  await until(()=>evaluate("Array.from(document.querySelectorAll('#source-scroll img')).some(i=>{i.loading='eager';return !i.hidden&&i.complete&&i.naturalWidth>0;})"),'real PDF source rendering');
  const compare=await evaluate("({visible:!document.querySelector('#source-pane').hidden,images:document.querySelectorAll('#source-scroll img').length,pages:document.querySelector('#page-total').textContent,editable:document.querySelectorAll('#reader [contenteditable=true],#reader textarea').length,error_note:document.querySelector('#page-note').textContent,page_errors:document.querySelectorAll('#source-scroll .page-error').length})");
  if(!compare.visible||compare.editable||compare.error_note||compare.page_errors)throw Error('PDF comparison has an error or is not a read-only view: '+JSON.stringify(compare));
  compare.hidden_test_eager_load=true;
  await optionalScreenshot(cfg.compareShot);
  fs.writeFileSync(cfg.result,JSON.stringify({ok:true,target_url:target.url,bridge:true,settings,settings_ui:settingsUi,manual_update:manualUpdate,initial_md_buttons:initialMdButtons,original_before_export:original,start_response:started,state:finished,csv_ui:csvUi,raw_markdown:code,pdf_comparison:compare,screenshots},null,2),'utf8');
} finally {if(ws)ws.close();}
'''


class _PrivateJob:
    """KILL_ON_JOB_CLOSE applies only to this newly spawned EXE and descendants."""
    def __init__(self, process):
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                        wintypes.DWORD, ctypes.c_void_p]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def pids(self):
        buffer = ctypes.create_string_buffer(65536)
        if not self.kernel.QueryInformationJobObject(self.handle, 3, buffer, len(buffer), None):
            return []
        count = wintypes.DWORD.from_buffer(buffer, 4).value
        if count > (len(buffer) - 8) // ctypes.sizeof(ctypes.c_size_t):
            return []
        return list((ctypes.c_size_t * count).from_buffer(buffer, 8))


def _cache_root() -> Path:
    settings = Path.home() / ".ai-workspace" / "paths.md"
    match = re.search(r"^cache_root:\s*(.+)$", settings.read_text(encoding="utf-8-sig"), re.M)
    if not match:
        raise RuntimeError("cache_root is missing from local paths.md")
    return Path(match.group(1).strip()) / "260725 PDF 추출"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixtures(root: Path, *, scan=False) -> dict:
    import fitz
    from pubnexus import general_pdf, store
    inputs = root / "inputs"
    inputs.mkdir()
    pdf, csv = inputs / "01-native.pdf", inputs / "02-table.csv"
    pdf_marker, csv_marker = "FROZEN_PDF_NATIVE_7391", "FROZEN_CSV_CELL_8426"
    with fitz.open() as document:
        page = document.new_page()
        if scan:
            from PIL import Image, ImageDraw, ImageFont
            image = Image.new("RGB", (1800, 450), "white")
            drawing = ImageDraw.Draw(image)
            font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 76)
            drawing.text((75, 170), "Frozen OCR marker 7391", font=font, fill="black")
            data = io.BytesIO()
            image.save(data, format="PNG")
            page.insert_image(page.rect, stream=data.getvalue())
            pdf_marker = "7391"
        else:
            for index in range(12):
                page.insert_text((55, 60 + index * 25), pdf_marker + f" synthetic original line {index + 1}.")
        document.save(pdf)
    csv.write_text(f"Name,Value\n{csv_marker},42\nSecond,7\n", encoding="utf-8", newline="")
    cache = root / "cache"
    previous = os.environ.get("PUBNEXUS_STORE")
    os.environ["PUBNEXUS_STORE"] = str(cache)
    try:
        sha = store.file_sha1(pdf)
        if scan:
            with fitz.open(pdf) as original:
                if original[0].get_text().strip():
                    raise AssertionError("Scan fixture must not contain a native text layer")
            doc = {"sha1": sha, "source": "pdf_general", "meta": {"title": "Frozen scan PDF"},
                   "body_text": [], "tables": [], "figures": [], "references": []}
        else:
            doc, report = general_pdf.ensure_fulltext({"sha1": sha, "source": "pdf_general",
                                                     "meta": {"title": "Frozen native PDF"}}, pdf)
            if report["status"] == "failed" or pdf_marker not in json.dumps(doc):
                raise AssertionError("Native PDF cache fixture extraction failed")
        store.save(sha, doc, pdf)
        (cache / "ui.json").write_text(json.dumps({"folder": str(inputs)}), encoding="utf-8")
    finally:
        if previous is None:
            os.environ.pop("PUBNEXUS_STORE", None)
        else:
            os.environ["PUBNEXUS_STORE"] = previous
    configuration = {"project": {"input_dir": str(inputs), "work_dir": str(root / "work")},
                     "grobid": {"url": ""}, "identify": {"resolve_missing_doi": False},
                     "metadata": {"request_delay_sec": 0}}
    (root / "config.yaml").write_text(json.dumps(configuration), encoding="utf-8")
    for source in (pdf, csv):
        source.chmod(stat.S_IREAD)
    return {"inputs": str(inputs), "cache": str(cache), "names": [pdf.name, csv.name],
            "output_dir": str(inputs / "Markdown"),
            "pdfMarker": pdf_marker, "csvMarker": csv_marker,
            "scan_mode": scan, "native_text_empty": scan,
            "source_digests_before": {str(p): _digest(p) for p in (pdf, csv)}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--node", default=shutil.which("node"))
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--scan", action="store_true", help="Use a raster-only PDF and an empty canonical cache")
    parser.add_argument("--check-update", action="store_true", help="Explicitly query the real release manifest; never install")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This harness requires Windows WebView2")
    if args.timeout <= 0 or not args.node:
        parser.error("A positive timeout and Node.js 22+ are required")
    if not args.prepare_only and (not args.exe or not args.exe.is_file()):
        parser.error("Pass the freshly built EXE through --exe")
    cache_root = _cache_root().resolve()
    output = (args.out_dir or cache_root / "frozen-smoke" /
              (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])).resolve()
    if not output.is_relative_to(cache_root):
        parser.error("Receipts must stay inside the configured project's DevCache")
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"ok": False, "mode": "prepare-only" if args.prepare_only else "frozen-exe",
               "started_at": datetime.now(timezone.utc).isoformat(), "output_dir": str(output),
               "harness_elevated": bool(ctypes.windll.shell32.IsUserAnAdmin())}
    process = job = root = None
    try:
        probe = subprocess.run([args.node, "--input-type=module", "-e", "console.log(typeof WebSocket)"],
                               capture_output=True, text=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        if probe.returncode or probe.stdout.strip() != "function":
            raise RuntimeError("Node.js has no built-in WebSocket; no packages were installed")
        with tempfile.TemporaryDirectory(prefix="pubnexus-frozen-") as temporary:
            root = Path(temporary)
            fixtures = _fixtures(root, scan=args.scan)
            sources = [Path(path) for path in fixtures["source_digests_before"]]
            try:
                receipt.update(fixtures=fixtures, isolated_root=str(root), source_readonly=True)
                from pubnexus.version import VERSION
                config = {**fixtures, "timeout": args.timeout, "result": str(root / "cdp-result.json"),
                          "expected_version": VERSION, "check_update": args.check_update,
                          "listShot": str(output / "list.png"), "compareShot": str(output / "comparison.png"),
                          "probe": str(output / "cdp-probe.json")}
                if args.prepare_only:
                    receipt.update(ok=True, fixtures_validated=True, exe_launched=False)
                else:
                    with socket.socket() as reserve:
                        reserve.bind(("127.0.0.1", 0))
                        config["port"] = reserve.getsockname()[1]
                    config_path = root / "cdp-config.json"
                    config_path.write_text(json.dumps(config), encoding="utf-8")
                    environment = os.environ.copy()
                    environment.update(PUBNEXUS_STORE=fixtures["cache"], PUBNEXUS_ROOT=str(root),
                                       PUBNEXUS_OUTPUT=fixtures["output_dir"],
                                       WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={config['port']}",
                                       WEBVIEW2_USER_DATA_FOLDER=str(root / "profile"), PYTHONDONTWRITEBYTECODE="1",
                                       PNX_TEST_DEBUG_PORT=str(config["port"]))
                    startup = subprocess.STARTUPINFO()
                    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startup.wShowWindow = subprocess.SW_HIDE
                    with open(output / "exe.log", "wb") as log:
                        process = subprocess.Popen([str(args.exe.resolve()), "--hidden"], cwd=str(root),
                                                   env=environment, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                                   creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup)
                        job = _PrivateJob(process)
                        receipt["job_bound"] = bool(job)
                        receipt.update(exe=str(args.exe.resolve()), exe_sha256=_digest(args.exe), pid=process.pid)
                        cdp = subprocess.run([args.node, "--input-type=module", "-e", _CDP_SCRIPT, str(config_path)],
                                             capture_output=True, text=True, timeout=args.timeout + 20,
                                             creationflags=subprocess.CREATE_NO_WINDOW)
                        (output / "cdp.log").write_text(cdp.stdout + cdp.stderr, encoding="utf-8")
                        if cdp.returncode:
                            raise RuntimeError("Frozen WebView2 bridge smoke failed: " + cdp.stderr[-3000:])
                        result = json.loads(Path(config["result"]).read_text(encoding="utf-8"))
                        if not result.get("ok"):
                            raise AssertionError("CDP returned an unsuccessful receipt")
                        markdown = {}
                        expected = {"01-native.md": fixtures["pdfMarker"], "02-table.md": fixtures["csvMarker"]}
                        for name, marker in expected.items():
                            saved = Path(fixtures["output_dir"]) / name
                            text = saved.read_text(encoding="utf-8")
                            if marker not in text or (name.endswith("table.md") and "| " + marker + " | 42 |" not in text):
                                raise AssertionError("Saved Markdown body/table mismatch: " + name)
                            markdown[name] = {"sha256": _digest(saved), "bytes": saved.stat().st_size,
                                              "body_checked": True, "table_checked": name.endswith("table.md")}
                        if args.scan:
                            pdf = Path(fixtures["inputs"]) / fixtures["names"][0]
                            sha = hashlib.sha1(pdf.read_bytes()).hexdigest()
                            cached = Path(fixtures["cache"]) / "docs" / sha[:2] / (sha + ".json")
                            doc = json.loads(cached.read_text(encoding="utf-8"))
                            report = doc.get("extraction_report") or {}
                            if doc.get("source") == "pdf_sdk":
                                ocr_pages = {page["page"] for page in report.get("pages", [])
                                             if page.get("ocr_performed") and page.get("ocr_method") == "tesseract"}
                                ocr = [block for block in doc.get("sdk_blocks", [])
                                       if block.get("source", {}).get("page") in ocr_pages and block.get("text", "").strip()]
                            else:
                                ocr = [p for section in doc.get("body_text", []) for p in section.get("paragraphs", [])
                                       if p.get("source", {}).get("method") == "tesseract"]
                            if not report.get("ocr_performed") or not ocr:
                                raise AssertionError("Scan result lacks actual OCR provenance")
                            receipt["ocr"] = {"performed": True, "paragraphs": len(ocr),
                                              "warnings": report.get("warnings", []), "status": report.get("status")}
                        receipt.update(ok=True, exe_launched=True, webview=result, markdown=markdown)
                after = {str(path): _digest(path) for path in sources}
                receipt.update(source_digests_after=after, sources_unchanged=after == fixtures["source_digests_before"])
                if not receipt["sources_unchanged"]:
                    raise AssertionError("A synthetic original changed")
            finally:
                if process:
                    receipt["exe_returncode_before_cleanup"] = process.poll()
                if job:
                    receipt["owned_job_pids_before_cleanup"] = job.pids()
                    job.close()
                    job = None
                if process and process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                if process:
                    process.wait(timeout=10)
                for source in sources:
                    source.chmod(stat.S_IREAD | stat.S_IWRITE)
        receipt["temporary_inputs_removed"] = not root.exists()
        if not receipt["temporary_inputs_removed"]:
            raise AssertionError("Temporary fixture/profile cleanup did not complete")
    except Exception as error:
        receipt.update(ok=False, error=f"{type(error).__name__}: {error}")
    finally:
        if root is not None:
            receipt["temporary_inputs_removed"] = not root.exists()
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": receipt["ok"], "receipt": str(output / "receipt.json"),
                      "error": receipt.get("error")}, ensure_ascii=False), flush=True)
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
