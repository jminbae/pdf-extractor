"""Native physical-line corrections through the public SDK record boundary."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class KoreanBoundaryTests(unittest.TestCase):
    def test_attested_word_can_contain_a_verb_ending_token_boundary(self):
        from pubnexus.korean_boundaries import refine_record
        def analyze(text, **kwargs):
            start = text.index("보이지")
            return [([SimpleNamespace(start=start,end=start+2,form="보이",tag="VV"),
                      SimpleNamespace(start=start+2,end=start+3,form="지",tag="EC")],0.0)]
        analyzer = SimpleNamespace(glue=lambda chunks, **kwargs: ("".join(chunks),[False]),
                                   space=lambda text: text, analyze=analyze)
        lines = ["문제가 보이지 않는다.","이것은 보이","지 않는다."]
        text = " ".join(lines)+"\n\n"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400,height=500)
            for index, line in enumerate(lines):
                page.insert_text((50,100+20*index),line,fontname="korea",fontsize=10)
            record = {"markdown": text,"chunks": [{"metadata": {"page_number": 1},"text": text,
                "page_boxes": [{"index": 0,"class": "text","bbox": [49,88,350,154],"pos": [0,len(text)]}]}],
                "pages": [{"page": 1,"ocr_performed": False,"sdk_to_source": [1,0,0,1,0,0]}]}
            result = refine_record(record,pdf,kiwi=analyzer)
        self.assertEqual(result["markdown"],"문제가 보이지 않는다. 이것은 보이지 않는다.\n\n")
        self.assertEqual(result["korean_refinement"]["edits"][0]["evidence"]["source_witness"],"보이지")

    def test_three_page_chain_has_one_owner_without_duplicating_body(self):
        from pubnexus.korean_boundaries import refine_record
        analyzer = SimpleNamespace(
            glue=lambda chunks, **kwargs: ("".join(chunks), [False]), space=lambda text: text,
            analyze=lambda text, **kwargs: [([SimpleNamespace(start=text.index("색소"),
                end=text.index("색소")+2, form="색소", tag="NNG")], 0.0)])
        rows = [["멜라닌색소가 있다.", "이 문장이 마지막으로 남긴 멜라닌색"],
                ["소가 만들어지는 바로 그 멜라닌색"], ["소가 만들어지는 현상을 계속해서 관찰한다."]]
        chunks = []
        with fitz.open() as pdf:
            for number, lines in enumerate(rows, 1):
                page = pdf.new_page(width=500, height=500)
                for index, line in enumerate(lines):
                    page.insert_text((50,100+20*index),line,fontname="korea",fontsize=10)
                bounds = fitz.Rect()
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        bounds |= fitz.Rect(line["bbox"])
                text = " ".join(lines)+"\n\n"
                chunks.append({"metadata": {"page_number": number}, "text": text, "page_boxes": [
                    {"index": 0, "class": "text", "bbox": list(bounds), "pos": [0,len(text)]}]})
            record = {"markdown": "".join(c["text"] for c in chunks), "chunks": chunks,
                      "pages": [{"page": n, "ocr_performed": False, "sdk_to_source": [1,0,0,1,0,0]} for n in (1,2,3)]}
            result = refine_record(record,pdf,kiwi=analyzer)
        expected = chunks[0]["text"].rstrip()+chunks[1]["text"].strip()+chunks[2]["text"].lstrip()
        self.assertEqual(result["markdown"], expected)
        self.assertEqual([c["text"] for c in result["chunks"]][1:], ["", ""])
        self.assertEqual([s["page"] for s in result["chunks"][0]["page_boxes"][0]["korean_source_locations"]], [1,2,3])

    def test_protected_kinds_ocr_and_cancellation_do_not_call_the_model(self):
        from pubnexus.korean_boundaries import refine_record, KoreanCancelled
        class NoModel:
            def __getattr__(self, name):
                raise AssertionError("Protected content reached Kiwi")
        with fitz.open() as pdf:
            page = pdf.new_page(width=400,height=500)
            page.insert_text((50,100),"멜라닌색",fontname="korea",fontsize=10)
            page.insert_text((50,120),"소가 있다.",fontname="korea",fontsize=10)
            text = "멜라닌색 소가 있다.\n\n"
            for kind in ("table", "caption", "picture", "list-item", "code", "footnote", "section-header"):
                record = {"markdown": text, "chunks": [{"text": text,"metadata": {"page_number": 1},
                    "page_boxes": [{"index": 0,"class": kind,"bbox": [49,88,350,134],"pos": [0,len(text)]}]}],
                    "pages": [{"page": 1,"ocr_performed": False,"sdk_to_source": [1,0,0,1,0,0]}]}
                self.assertEqual(refine_record(record,pdf,kiwi=NoModel())["markdown"],text)
            record["chunks"][0]["page_boxes"][0]["class"] = "text"
            record["pages"][0]["ocr_performed"] = True
            self.assertEqual(refine_record(record,pdf,kiwi=NoModel())["markdown"],text)
            with self.assertRaises(KoreanCancelled):
                refine_record(record,pdf,kiwi=NoModel(),cancel=lambda: True)

    def test_reference_section_is_not_rewritten(self):
        from pubnexus.korean_boundaries import refine_record
        analyzer = SimpleNamespace(
            glue=lambda chunks, **kwargs: ("".join(chunks), [False]), space=lambda text: text,
            analyze=lambda text, **kwargs: [([SimpleNamespace(start=text.index("색소"),
                end=text.index("색소")+2, form="색소", tag="NNG")], 0.0)])
        heading, paragraph = "## 참고문헌\n\n", "멜라닌색 소가 있다.\n\n"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((50,70), "참고문헌", fontname="korea", fontsize=12)
            page.insert_text((50,100), "멜라닌색", fontname="korea", fontsize=10)
            page.insert_text((50,120), "소가 있다.", fontname="korea", fontsize=10)
            record = {"markdown": heading+paragraph, "chunks": [{"text": heading+paragraph,
                "metadata": {"page_number": 1}, "page_boxes": [
                    {"index": 0, "class": "section-header", "bbox": [49,55,350,84], "pos": [0,len(heading)]},
                    {"index": 1, "class": "text", "bbox": [49,88,350,134], "pos": [len(heading),len(heading+paragraph)]}]}],
                "pages": [{"page": 1, "ocr_performed": False, "sdk_to_source": [1,0,0,1,0,0]}]}
            result = refine_record(record,pdf,kiwi=analyzer)
        self.assertEqual(result["markdown"], record["markdown"])
        self.assertEqual(result["korean_refinement"]["edits"], [])

    def test_attested_conjugated_verb_is_rejoined_without_rewriting_its_letters(self):
        from pubnexus.korean_boundaries import refine_record
        analyzer = SimpleNamespace(
            glue=lambda chunks, **kwargs: ("".join(chunks), [False]), space=lambda text: text,
            analyze=lambda text, **kwargs: [([SimpleNamespace(start=text.index("통해"),
                end=text.index("통해")+2, form="통하", tag="VV")], 0.0)])
        lines = ["검사를 통해 확인한다.", "이 검사를 통", "해 진단한다."]
        text = " ".join(lines)+"\n\n"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            for index, line in enumerate(lines):
                page.insert_text((50,100+20*index), line, fontname="korea", fontsize=10)
            record = {"markdown": text, "chunks": [{"metadata": {"page_number": 1}, "text": text,
                "page_boxes": [{"index": 0, "class": "text", "bbox": [49,88,350,154], "pos": [0,len(text)]}]}],
                "pages": [{"page": 1, "ocr_performed": False, "sdk_to_source": [1,0,0,1,0,0]}]}
            result = refine_record(record,pdf,kiwi=analyzer)
        self.assertEqual(result["markdown"], "검사를 통해 확인한다. 이 검사를 통해 진단한다.\n\n")
        self.assertEqual(result["korean_refinement"]["edits"][0]["evidence"]["source_witness"], "통해")

    def test_adjacent_page_compound_joins_with_witness_and_keeps_local_floats(self):
        from pubnexus.korean_boundaries import refine_record
        analyzer = SimpleNamespace(
            glue=lambda chunks, **kwargs: ("".join(chunks), [False]), space=lambda text: text,
            analyze=lambda text, **kwargs: [([SimpleNamespace(start=text.index("색소"),
                end=text.index("색소")+2, form="색소", tag="NNG")], 0.0)])
        left_lines = ["멜라닌색소가 있다.", "이 문장의 끝에 있는 멜라닌색"]
        right_lines = ["소가 만들어지는 현상에 해당한다.", "이것은 이어지는 본문이다."]
        left, right = " ".join(left_lines)+"\n\n", " ".join(right_lines)+"\n\n"
        image = "\n![](images/example/figure.png)\n\n"
        caption = "그림 1. 원래 캡션을 유지한다.\n\n"
        with fitz.open() as pdf:
            for rows in (left_lines, right_lines):
                page = pdf.new_page(width=400, height=500)
                for index, row in enumerate(rows):
                    page.insert_text((50, 350+20*index), row, fontname="korea", fontsize=10)
            bounds = []
            for page in pdf:
                rectangles = [fitz.Rect(line["bbox"]) for b in page.get_text("dict")["blocks"] for line in b.get("lines", [])]
                box = rectangles[0]
                for rectangle in rectangles[1:]:
                    box |= rectangle
                bounds.append(list(box))
            record = {"markdown": left+image+caption+right, "chunks": [
                {"metadata": {"page_number": 1}, "text": left, "page_boxes": [
                    {"index": 0, "class": "text", "bbox": bounds[0], "pos": [0, len(left)]}]},
                {"metadata": {"page_number": 2}, "text": image+caption+right, "page_boxes": [
                    {"index": 0, "class": "picture", "bbox": [50, 50, 200, 200], "pos": [0, len(image)]},
                    {"index": 1, "class": "caption", "bbox": [50, 210, 300, 230], "pos": [len(image), len(image+caption)]},
                    {"index": 2, "class": "text", "bbox": bounds[1], "pos": [len(image+caption), len(image+caption+right)]}]}],
                "pages": [{"page": n, "ocr_performed": False, "sdk_to_source": [1,0,0,1,0,0]} for n in (1,2)]}
            before = copy.deepcopy(record)
            result = refine_record(record, pdf, kiwi=analyzer)
        merged = left.rstrip()+right.lstrip()
        self.assertEqual(result["markdown"], merged+image+caption)
        self.assertIn("멜라닌색소가 만들어지는", result["markdown"])
        self.assertEqual(result["chunks"][1]["text"], image+caption)
        self.assertEqual(result["chunks"][1]["page_boxes"][2]["pos"][0], result["chunks"][1]["page_boxes"][2]["pos"][1])
        self.assertEqual([l["page"] for l in result["chunks"][0]["page_boxes"][0]["korean_source_locations"]], [1,2])
        self.assertEqual(len(result["korean_refinement"]["cross_page_joins"]), 1)
        self.assertEqual(result["markdown_raw"], before["markdown"])
        self.assertEqual(record, before)

    def test_rotated_sdk_box_matches_native_unrotated_physical_lines(self):
        from pubnexus.korean_boundaries import refine_record
        analyzer = SimpleNamespace(
            glue=lambda chunks, **kwargs: ("".join(chunks), [False]),
            space=lambda text: text,
            analyze=lambda text, **kwargs: [([SimpleNamespace(start=text.index("색소"),
                end=text.index("색소")+2, form="색소", tag="NNG")], 0.0)])
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((50, 100), "멜라닌색", fontname="korea", fontsize=10)
            page.insert_text((50, 120), "소가 있다.", fontname="korea", fontsize=10)
            page.set_rotation(90)
            text = "멜라닌색 소가 있다.\n\n"
            record = {"markdown": text, "chunks": [{"metadata": {"page_number": 1},
                "text": text, "page_boxes": [{"index": 0, "class": "text",
                    "bbox": list(fitz.Rect(49, 88, 360, 134) * page.rotation_matrix), "pos": [0, len(text)]}]}],
                "pages": [{"page": 1, "sdk_to_source": list(page.derotation_matrix), "ocr_performed": False}]}
            result = refine_record(record, pdf, kiwi=analyzer)
        self.assertEqual(result["markdown"], "멜라닌색소가 있다.\n\n")
        lines = result["korean_refinement"]["edits"][0]["native_lines"]
        self.assertAlmostEqual(lines[0][0], 50, places=1)

    def test_only_native_line_boundary_is_corrected_and_false_glue_is_vetoed(self):
        from pubnexus.korean_boundaries import refine_record

        class Analyzer:
            def glue(self, chunks, return_space_insertions=False):
                return ("".join(chunks), [False])

            def space(self, text):
                return text.replace("보호하며일반적으로", "보호하며 일반적으로")

            def analyze(self, text, top_n=1):
                start = text.find("색소")
                tokens = [SimpleNamespace(start=start, end=start + 2, form="색소", tag="NNG")] if start >= 0 else []
                return [(tokens, 0.0)]

        cases = [
            ("우리 몸의 멜라닌색", "소가 만들어진다.", "우리 몸의 멜라닌색소가 만들어진다.\n\n"),
            ("피부 장벽을 보호하며", "일반적으로 경과를 관찰한다.", "피부 장벽을 보호하며 일반적으로 경과를 관찰한다.\n\n"),
        ]
        with tempfile.TemporaryDirectory(prefix="pubnexus-korean-test-") as temporary:
            for left, right, expected in cases:
                with self.subTest(left=left):
                    path = Path(temporary) / "native.pdf"
                    with fitz.open() as pdf:
                        page = pdf.new_page(width=400, height=500)
                        page.insert_text((50, 100), left, fontname="korea", fontsize=10)
                        page.insert_text((50, 120), right, fontname="korea", fontsize=10)
                        pdf.save(path)
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    markdown = left + " " + right + "\n\n"
                    record = {"markdown": markdown, "chunks": [{"metadata": {"page_number": 1},
                        "text": markdown, "page_boxes": [{"index": 0, "class": "text",
                            "bbox": [49, 88, 360, 134], "pos": [0, len(markdown)]}]}],
                        "pages": [{"page": 1, "ocr_performed": False, "sdk_to_source": [1, 0, 0, 1, 0, 0]}]}
                    if left.startswith("우리"):
                        record["markdown_raw"] = "Original SDK before another verified stage.\n\n"
                        record["chunks_raw"] = [{"text": record["markdown_raw"]}]
                    before = copy.deepcopy(record)
                    with fitz.open(path) as pdf:
                        result = refine_record(record, pdf, kiwi=Analyzer())
                        repeated = refine_record(result, pdf, kiwi=Analyzer())
                    self.assertEqual(result["markdown"], expected)
                    self.assertEqual(result["markdown_raw"], before.get("markdown_raw", markdown))
                    self.assertEqual(result["chunks_raw"], before.get("chunks_raw", before["chunks"]))
                    self.assertEqual(result["korean_input_markdown"], markdown)
                    self.assertEqual(record, before)
                    self.assertEqual(repeated, result)
                    self.assertEqual(re.sub(r"\s+", "", result["markdown"]), re.sub(r"\s+", "", markdown))
                    self.assertEqual(result["chunks"][0]["page_boxes"][0]["pos"], [0, len(expected)])
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
