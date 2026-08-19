import unittest
from unittest.mock import patch

from sherpa_read import clean_selected_text, get_selected_text


class CleanSelectedTextTests(unittest.TestCase):
    def test_removes_invisible_characters(self) -> None:
        self.assertEqual(clean_selected_text("zero\u200b width\u00ad"), "zero width")

    def test_repairs_ascii_and_unicode_line_hyphenation(self) -> None:
        self.assertEqual(
            clean_selected_text("inter-\nnational co\u2010  \r\noperate non\u2011\nbreaking"),
            "international cooperate nonbreaking",
        )

    def test_flattens_line_breaks_and_whitespace(self) -> None:
        self.assertEqual(
            clean_selected_text("  first\nsecond\t\tthird\u2028fourth  "),
            "first second third fourth",
        )


class GetSelectedTextTests(unittest.TestCase):
    @patch("sherpa_read._read_primary_selection", return_value="inter-\nnational")
    @patch("sherpa_read._copy_selection")
    def test_prefers_primary_selection(self, copy_selection, _read_primary) -> None:
        self.assertEqual(get_selected_text(0, 0), "international")
        copy_selection.assert_not_called()

    @patch("sherpa_read.time.sleep")
    @patch("sherpa_read._read_clipboard", return_value="copied\ntext")
    @patch("sherpa_read._copy_selection", return_value=True)
    @patch("sherpa_read._read_primary_selection", return_value="")
    def test_falls_back_to_copying_selection(
        self,
        _read_primary,
        _copy_selection,
        _read_clipboard,
        sleep,
    ) -> None:
        self.assertEqual(get_selected_text(), "copied text")
        self.assertEqual(sleep.call_count, 2)

    @patch("sherpa_read.time.sleep")
    @patch("sherpa_read._copy_selection", return_value=False)
    @patch("sherpa_read._read_primary_selection", return_value="")
    def test_returns_empty_when_copy_shortcut_is_unavailable(
        self,
        _read_primary,
        _copy_selection,
        _sleep,
    ) -> None:
        self.assertEqual(get_selected_text(), "")


if __name__ == "__main__":
    unittest.main()
