"""Regression check for the Version 0.2 stylesheet cascade.

The campus stylesheet must load after the legacy inline style block so the
new design tokens and responsive rules can override older presentation rules.
"""
import unittest
from pathlib import Path


class StylesheetCascadeTests(unittest.TestCase):
    def test_campus_stylesheet_loads_after_inline_styles(self):
        template_path = Path(__file__).resolve().parents[1] / "templates" / "base.html"
        template = template_path.read_text(encoding="utf-8")
        inline_style_end = template.index("</style>")
        campus_css_link = template.index("css/campus-v02.css")
        campus_v03_css_link = template.index("css/campus-v03.css")
        page_specific_head_block = template.index("{% block head %}")
        campus_v03_css_path = template_path.parents[1] / "static" / "css" / "campus-v03.css"

        self.assertGreater(campus_css_link, inline_style_end)
        self.assertGreater(campus_v03_css_link, campus_css_link)
        self.assertLess(campus_v03_css_link, page_specific_head_block)
        self.assertTrue(campus_v03_css_path.is_file())


if __name__ == "__main__":
    unittest.main()
