"""Offline frontend-only seam for background extraction interaction tests.

The real UI JavaScript is served unchanged. Native responses are deterministic
fixtures; no App instance, document extractor, user file or settings is opened.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pubnexus.ui import INDEX_HTML


def main():
    with tempfile.TemporaryDirectory(prefix="pnx-ui-background-") as temporary:
        root = Path(temporary)
        files = [{"name": "A.pdf", "path": str(root / "A.pdf"), "done": False, "md_ready": False, "status": "ready", "pages": 30},
                 {"name": "B.pdf", "path": str(root / "B.pdf"), "done": True, "md_ready": True, "status": "complete", "pages": 30,
                  "markdown": str(root / "Markdown" / "B.md")},
                 {"name": "C.pdf", "path": str(root / "C.pdf"), "done": False, "md_ready": False, "status": "ready", "pages": 30}]
        state = {"files": files, "path": str(root), "output_dir": str(root / "Markdown"),
                 "grobid": "off", "pdf_engine_available": True,
                 "settings": {"ok": True, "theme": "light", "output_dir": str(root / "Markdown"),
                              "auto_update": False, "current_version": "1.2.4", "update": {"status": "idle"}}}
        bootstrap = """<script>window.__calls=[];window.__fixture=STATE;window.__revision='saved-B-v1';
window.__fixture.settings.theme=localStorage.getItem('__fixture-theme')||'light';
window.pywebview={api:new Proxy({},{get:(_,name)=>(...args)=>{window.__calls.push({name,args});
if(name==='state')return Promise.resolve(window.__fixture);
if(name==='settings')return Promise.resolve(window.__fixture.settings);
if(name==='set_theme'){
if(window.__themeRejectNext){window.__themeRejectNext=false;return Promise.resolve({ok:false,why:'테마 저장 실패 fixture'});}
window.__fixture.settings={...window.__fixture.settings,theme:args[0]};localStorage.setItem('__fixture-theme',args[0]);return Promise.resolve(window.__fixture.settings);}
if(name==='extract_selected')return Promise.resolve(window.__rejectNext?{ok:true,n:0,queued:true,accepted_paths:[]}:{ok:true,n:1,queued:!!window.__queueMode});
if(name==='search'&&args[2]!==window.__revision)return Promise.resolve({ok:false,code:'stale_revision',why:'새 Markdown이 저장되었습니다. MD를 다시 열어 검색하세요.'});
if(name==='search'||name==='search_source')return Promise.resolve({results:[{text:'needle fixture',side:name==='search_source'?'source':'result',page:2,locations:[{page:2,bbox:[20,30,100,60],height:500}]}]});
return Promise.resolve({ok:true,n:1});}})};</script>""".replace("STATE", json.dumps(state))

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                from urllib.parse import urlparse, parse_qs
                url = urlparse(self.path)
                if url.path == "/":
                    data, mime = INDEX_HTML.replace("</head>", bootstrap + "</head>"), "text/html; charset=utf-8"
                elif url.path == "/files":
                    data, mime = json.dumps(state), "application/json"
                elif url.path == "/open":
                    index = int(parse_qs(url.query).get("p", [0])[0])
                    data, mime = json.dumps({"key": 1, "revision": "saved-B-v1", "name": files[index]["name"], "pages": [[400,500]] * 30,
                        "doc": {"extracted": True, "html": "<h1>Stored B</h1><p data-block-id='b-old'>Old snapshot mapped paragraph</p>" + "".join(f"<p>Stored paragraph {i} needle</p>" for i in range(80)),
                                "md_text": "# Stored B\n\nneedle"}}), "application/json"
                elif url.path == "/document-map":
                    data, mime = json.dumps({"revision": "saved-B-v1", "pages": [{"width":400,"height":500}] * 30,
                        "blocks": [{"id":"b-old", "mapping":"exact", "locations":[{"page":0,"bbox":[20,30,100,60],"width":400,"height":500}]}]}), "application/json"
                elif url.path == "/page":
                    data, mime = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="500"><rect width="400" height="500" fill="white"/></svg>', "image/svg+xml"
                elif url.path == "/regression":
                    data, mime = Path(__file__).with_suffix(".js").read_text(encoding="utf-8"), "text/javascript; charset=utf-8"
                elif url.path == "/queue-regression":
                    data, mime = Path(__file__).with_name("ui_queue.js").read_text(encoding="utf-8"), "text/javascript; charset=utf-8"
                elif url.path == "/resources-regression":
                    data, mime = Path(__file__).with_name("ui_resources.js").read_text(encoding="utf-8"), "text/javascript; charset=utf-8"
                elif url.path == "/minimal-regression":
                    data, mime = Path(__file__).with_name("ui_minimal.js").read_text(encoding="utf-8"), "text/javascript; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                payload = data.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                if self.path != "/shutdown":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.end_headers()
                threading.Thread(target=server.shutdown, daemon=True).start()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/", "frontend_fixture": True}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
