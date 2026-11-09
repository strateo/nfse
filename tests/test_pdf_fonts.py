from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
import re

from lxml import etree

from nfse_nacional import danfse
from nfse_nacional.pdf_fonts import FontConfigurationError, FontSet
from test_danfse import SAMPLE, make_nfse


ROOT = Path(__file__).resolve().parents[1]
KOREAN_FONT = Path("C:/Windows/Fonts/malgun.ttf")
GREEK_FONT = Path("C:/Windows/Fonts/arial.ttf")


def with_customer_name(name: str) -> bytes:
    root = etree.fromstring(make_nfse(SAMPLE))
    ns = {"n": danfse.NFSE_NS}
    root.find("n:infNFSe/n:DPS/n:infDPS/n:toma/n:xNome", ns).text = name
    return etree.tostring(root)


class PdfFontsTest(unittest.TestCase):
    @unittest.skipUnless(KOREAN_FONT.is_file(), "requires Windows Malgun Gothic font")
    def test_non_latin_requires_explicit_font_and_embeds_fallback(self):
        source = with_customer_name("한국어 음악 회사")
        with self.assertRaises(FontConfigurationError):
            danfse.render_danfse(source)
        pdf = danfse.render_danfse(source, font_set=FontSet([KOREAN_FONT]))
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertIn(b"/Subtype /TrueType", pdf)

    @unittest.skipUnless(GREEK_FONT.is_file(), "requires Windows Arial font")
    def test_missing_glyph_error_does_not_echo_fiscal_name(self):
        private_name = "PrivateCompany㐀"
        with self.assertRaises(FontConfigurationError) as caught:
            danfse.render_danfse(with_customer_name(private_name), font_set=FontSet([GREEK_FONT]))
        self.assertNotIn(private_name, str(caught.exception))
        self.assertIn("U+3400", str(caught.exception))

    def test_missing_font_error_is_actionable_without_exposing_path(self):
        private_path = Path("C:/private/fiscal/client-font.ttf")
        with self.assertRaises(FontConfigurationError) as caught:
            FontSet([private_path])
        self.assertIn("font file is unavailable", str(caught.exception))
        self.assertNotIn(str(private_path), str(caught.exception))

    @unittest.skipUnless(KOREAN_FONT.is_file() and GREEK_FONT.is_file(), "requires Windows Malgun Gothic and Arial fonts")
    def test_concurrent_renders_keep_fallback_sets_isolated(self):
        ko = with_customer_name("한국어")
        greek = with_customer_name("Ελληνική")
        korean_set, greek_set = FontSet([KOREAN_FONT]), FontSet([GREEK_FONT])
        jobs = [(ko, korean_set), (greek, greek_set)] * 4
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda pair: danfse.render_danfse(pair[0], font_set=pair[1]), jobs))
        for result in results:
            self.assertTrue(result.startswith(b"%PDF-"))
        korean_fonts = [b"MalgunGothic" in pdf for pdf in results]
        self.assertEqual(korean_fonts, [True, False, True, False, True, False, True, False])
        self.assertTrue(all(b"ArialMT" not in pdf for pdf, is_korean in zip(results, korean_fonts) if is_korean))
        self.assertTrue(all(b"MalgunGothic" not in pdf for pdf, is_korean in zip(results, korean_fonts) if not is_korean))


if __name__ == "__main__":
    unittest.main()
