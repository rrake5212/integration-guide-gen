import unittest

from guidegen.fetch import chunk_text, html_to_text, select_sections, split_sections


class HtmlTests(unittest.TestCase):
    def test_headings_paragraphs_and_table_cells(self) -> None:
        html = """<html><body>
        <h2>Web application flow</h2>
        <p>Send the user to https://example.com/oauth/authorize.</p>
        <table><tr><td>repo</td><td>Grants full access</td></tr></table>
        <script>ignore_me()</script>
        </body></html>"""
        text = html_to_text(html)
        self.assertIn("## Web application flow", text)
        self.assertIn("repo | Grants full access", text)
        self.assertIn("https://example.com/oauth/authorize", text)
        self.assertNotIn("ignore_me", text)


class SectionTests(unittest.TestCase):
    def test_nav_and_language_sections_are_dropped(self) -> None:
        nav = "## Apps\n" + "\n".join(f"- item {i}" for i in range(15))
        body = "## Web application flow\n" + ("OAuth token scope redirect. " * 40)
        python = "## Python\n" + ("token = exchange(code) " * 40)
        sections = select_sections(f"{nav}\n\n{body}\n\n{python}")
        headings = [section.heading for section in sections]
        self.assertIn("Web application flow", headings)
        self.assertNotIn("Apps", headings)
        self.assertNotIn("Python", headings)

    def test_split_sections_keeps_preamble(self) -> None:
        sections = split_sections("intro\n## A\nbody")
        self.assertEqual(sections[0].heading, "")
        self.assertEqual(sections[1].heading, "A")

    def test_chunk_text_bounds(self) -> None:
        text = "\n".join(f"line {i} " + "x" * 80 for i in range(300))
        chunks = chunk_text(text, max_chars=2000, overlap=200)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(chunk) <= 2100 for chunk in chunks))
        self.assertEqual(chunk_text("short", 2000), ["short"])


if __name__ == "__main__":
    unittest.main()
