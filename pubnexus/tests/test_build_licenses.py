"""Notice packaging must follow actual selected modules and keep license variants."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from email.message import Message

_script = Path(__file__).resolve().parents[2] / "tools" / "collect_licenses.py"
_spec = importlib.util.spec_from_file_location("collect_licenses", _script)
licenses = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(licenses)


class BuildLicenseTests(unittest.TestCase):
    def test_license_spellings_and_nested_third_party_notices(self):
        for name in ("LICENSE", "LICENCE.rst", "COPYING.txt", "NOTICE.md", "AUTHORS",
                     "thing.dist-info/licenses/nested/THIRD_PARTY.txt"):
            self.assertTrue(licenses.license_file(Path(name)), name)
        self.assertFalse(licenses.license_file(Path("package/module.py")))

    def test_selected_module_retains_copyright_and_exact_version_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "example").mkdir()
            (root / "example/module.py").write_text("x = 1", encoding="utf-8")
            (root / "example-1.dist-info").mkdir()
            notice = root / "example-1.dist-info/LICENCE.rst"
            notice.write_text("Copyright 2026 Example. MIT permission grant.", encoding="utf-8")
            toc = root / "Analysis.toc"
            toc.write_text(repr([("example.module", str(root / "example/module.py"), "PYMODULE")]), encoding="utf-8")
            (root / "LICENSE").write_text("Application license", encoding="utf-8")

            class Distribution:
                metadata = Message()
                metadata["Name"] = "example"
                version = "1.0"
                files = [Path("example/module.py"), Path("example-1.dist-info/LICENCE.rst")]

                def locate_file(self, path):
                    return root / path

            result = licenses.collect(toc, root / "out", root, "1.2.2", [Distribution()])
            self.assertEqual(result["packages"][0]["version"], "1.0")
            copied = root / "out" / result["packages"][0]["notices"][0]["path"]
            self.assertEqual(copied.read_bytes(), notice.read_bytes())
            self.assertIn("/tree/v1.2.2", (root / "out/SOURCE.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
