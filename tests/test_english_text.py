"""Prevent untranslated application messages and documentation from returning."""
import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile(r"[\u0400-\u04ff]")


class EnglishTextTests(unittest.TestCase):
    def test_application_strings_are_english(self):
        for path in (ROOT / "прога").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    with self.subTest(file=path.name, line=node.lineno):
                        self.assertIsNone(CYRILLIC.search(node.value))

    def test_documentation_is_english_except_source_directory(self):
        for path in [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]:
            with self.subTest(file=str(path.relative_to(ROOT))):
                text = path.read_text(encoding="utf-8").replace("прога/", "source/")
                self.assertIsNone(CYRILLIC.search(text))

    def test_windows_image_languages_are_preserved(self):
        tree = ast.parse((ROOT / "прога/V2.py").read_text(encoding="utf-8"))
        choices = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "WINDOWS_ISO_CHOICES"
                               for t in node.targets))
        self.assertEqual({value[1] for value in choices.values()}, {"Russian", "English"})
        self.assertEqual(sum(value[0] == "10" for value in choices.values()), 4)


if __name__ == "__main__":
    unittest.main()
