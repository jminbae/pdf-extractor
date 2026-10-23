"""Isolated optional SDK program; the main application never imports Torch."""

CHILD_CODE = r'''
import os, json, socket, sys, importlib.metadata
from pathlib import Path
# Monitor this interpreter, not Windows' parent venv launcher. This is a
# sampled private-memory guard; process isolation/time/output bounds also apply.
if os.name == "nt":
    import ctypes, threading, time
    from ctypes import wintypes
    class MemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "commit", "peak_commit", "private")]
    memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    current_process.restype = wintypes.HANDLE
    def watch_memory():
        while True:
            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if memory_info(current_process(), ctypes.byref(counters), counters.cb) and counters.private > 3*1024**3:
                print("Local Docling private-memory budget exceeded", file=sys.stderr, flush=True)
                os._exit(70)
            time.sleep(.05)
    threading.Thread(target=watch_memory, daemon=True).start()
def deny(*args, **kwargs):
    raise RuntimeError("Network disabled for offline PDF tables")
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableStructureOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption
options = PdfPipelineOptions(
    artifacts_path=Path(request["models"]),
    accelerator_options=AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU),
    enable_remote_services=False, allow_external_plugins=False, do_ocr=False,
    do_table_structure=True,
    table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE, do_cell_matching=True),
    do_code_enrichment=False, do_formula_enrichment=False, do_picture_classification=False,
    do_picture_description=False, do_chart_extraction=False,
    generate_page_images=False, generate_picture_images=False, document_timeout=45,
)
converter = DocumentConverter(allowed_formats=[InputFormat.PDF], format_options={
    InputFormat.PDF: PdfFormatOption(pipeline_options=options, backend=PyPdfiumDocumentBackend)})
for page in request["pages"]:
    result = converter.convert(Path(request["source"]), page_range=(page, page), raises_on_error=False)
    document = result.document
    tables = [{"prov": [p.model_dump(mode="json") for p in table.prov],
               "cells": [cell.model_dump(mode="json") for cell in table.data.table_cells],
               "rows": table.data.num_rows, "cols": table.data.num_cols,
               "orientation": str(table.data.orientation)} for table in document.tables]
    record = {"page": page, "status": result.status.value,
              "version": importlib.metadata.version("docling"),
              "pages": {str(k): v.size.model_dump(mode="json") for k, v in document.pages.items()},
              "tables": tables, "errors": [e.model_dump(mode="json") for e in result.errors]}
    target = Path(request["output"]) / ("page-%06d.json" % page)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
'''
