import re
import unittest

from chunking import chunk_markdown_document, split_markdown_sections


class ChunkingTests(unittest.TestCase):
    def test_short_section_stays_isolated_with_metadata(self) -> None:
        markdown = """# Produkt ABC

## Anwendung
Dieses Produkt wird morgens und abends verwendet. Trage es gleichmaessig auf und massiere es sanft ein.

## Lagerhinweis
Kuehl und trocken lagern. Vor direkter Sonne schuetzen.

## Nachhaltigkeit
Die Verpackung ist recyclingfaehig und kann im Wertstoffkreislauf entsorgt werden.
"""

        chunks = chunk_markdown_document(markdown, title="Produkt ABC")
        lager_chunks = [chunk for chunk in chunks if chunk.metadata["section"] == "Lagerhinweis"]

        self.assertEqual(len(lager_chunks), 1)
        self.assertTrue(lager_chunks[0].text.startswith("Document: Produkt ABC\nSection: Lagerhinweis\n"))
        self.assertIn("Kuehl und trocken lagern", lager_chunks[0].text)
        self.assertNotIn("recyclingfaehig", lager_chunks[0].text.lower())
        self.assertNotIn("gleichmaessig auf", lager_chunks[0].text.lower())

    def test_heading_like_lines_are_promoted_to_sections(self) -> None:
        text = """Ein kurzer Produktueberblick.

Lagerhinweis
Kuehl und trocken lagern. Nach dem Oeffnen gut verschliessen und aufrecht lagern.

Anwendung
Einmal taeglich anwenden. Nicht mit den Augen in Kontakt bringen.
"""

        sections = split_markdown_sections(text, default_section="Produktinfo")
        titles = [section.title for section in sections]

        self.assertIn("Lagerhinweis", titles)
        self.assertIn("Anwendung", titles)

    def test_large_section_splits_with_overlap_without_crossing_sections(self) -> None:
        sentences = []
        for index in range(1, 65):
            marker = f"S{index:02d}"
            sentences.append(
                f"{marker} Die Anwendung beschreibt den Ablauf sehr detailliert und bleibt im selben Themenblock."
            )

        markdown = "# Produkt XYZ\n\n## Anwendung\n" + " ".join(sentences) + "\n\n## Lagerhinweis\nKuehl lagern."
        chunks = chunk_markdown_document(markdown, title="Produkt XYZ")

        anwendung_chunks = [chunk for chunk in chunks if chunk.metadata["section"] == "Anwendung"]
        self.assertGreater(len(anwendung_chunks), 1)

        for chunk in anwendung_chunks:
            self.assertNotIn("Section: Lagerhinweis", chunk.text)
            self.assertNotIn("Kuehl lagern.", chunk.text)

        for current, following in zip(anwendung_chunks, anwendung_chunks[1:]):
            current_markers = set(re.findall(r"S\d{2}", current.text))
            next_markers = set(re.findall(r"S\d{2}", following.text))
            self.assertTrue(current_markers.intersection(next_markers))


if __name__ == "__main__":
    unittest.main()
