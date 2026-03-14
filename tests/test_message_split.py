"""Tests for split_message()."""

import pytest
from src.bot.formatters import split_message


class TestSplitMessage:
    """Tests for the split_message function."""

    # ------------------------------------------------------------------
    # No split needed
    # ------------------------------------------------------------------

    def test_short_message_returns_single_chunk(self):
        """Text at or below max_length is returned as a single chunk."""
        text = "Hello, world!"
        result = split_message(text, max_length=4000)
        assert result == [text]

    def test_exact_max_length_returns_single_chunk(self):
        """Text exactly at max_length is not split."""
        text = "a" * 4000
        result = split_message(text, max_length=4000)
        assert result == [text]

    def test_empty_string_returns_single_empty_chunk(self):
        """Empty string returns a list with one empty string."""
        result = split_message("", max_length=4000)
        assert result == [""]

    # ------------------------------------------------------------------
    # Split at newline
    # ------------------------------------------------------------------

    def test_split_at_last_newline_within_window(self):
        """When text exceeds max_length and contains a newline, splits there."""
        line1 = "a" * 100
        line2 = "b" * 100
        text = line1 + "\n" + line2
        # max_length large enough to fit both lines → no split needed
        result = split_message(text, max_length=4000)
        assert result == [text]

    def test_split_two_chunks_on_newline(self):
        """Oversized text with a newline splits into two clean chunks."""
        line1 = "a" * 50
        line2 = "b" * 50
        text = line1 + "\n" + line2
        # max_length = 60, so line1+"\n" = 51 chars fits; line2 spills over
        result = split_message(text, max_length=60)
        assert len(result) == 2
        assert result[0] == line1
        assert result[1] == line2

    def test_split_prefers_last_newline_in_window(self):
        """Splits at the LAST newline within the window, not the first."""
        # "aaa\nbbb\nccc" with max_length=8
        # window "aaa\nbbb\n" → last \n at index 7 → chunk="aaa\nbbb"
        text = "aaa\nbbb\nccc"
        result = split_message(text, max_length=8)
        assert result[0] == "aaa\nbbb"
        assert result[1] == "ccc"

    def test_multiline_long_message_splits_on_newlines(self):
        """A long message with many lines splits cleanly between lines."""
        lines = ["Line {:04d}: {}".format(i, "x" * 80) for i in range(50)]
        text = "\n".join(lines)
        max_length = 400
        chunks = split_message(text, max_length=max_length)
        # Every chunk must be within max_length
        for chunk in chunks:
            assert len(chunk) <= max_length, f"Chunk too long: {len(chunk)}"
        # Reassembled text must equal original
        assert "\n".join(chunks) == text

    # ------------------------------------------------------------------
    # Fallback: no newline in window
    # ------------------------------------------------------------------

    def test_no_newline_falls_back_to_char_split(self):
        """When no newline exists in window, splits at max_length boundary."""
        text = "a" * 9000
        result = split_message(text, max_length=4000)
        assert len(result) == 3
        assert result[0] == "a" * 4000
        assert result[1] == "a" * 4000
        assert result[2] == "a" * 1000

    def test_no_newline_single_long_line_exact_split(self):
        """Single line longer than max_length is split at max_length."""
        text = "x" * 8001
        result = split_message(text, max_length=4000)
        assert result[0] == "x" * 4000
        assert result[1] == "x" * 4000
        assert result[2] == "x"

    # ------------------------------------------------------------------
    # Empty chunk prevention
    # ------------------------------------------------------------------

    def test_consecutive_newlines_do_not_produce_empty_chunks(self):
        """Consecutive newlines at the split boundary produce no empty chunks."""
        # Build a text where the split point lands on a blank line
        block = "a" * 3990 + "\n\n" + "b" * 100
        result = split_message(block, max_length=4000)
        for chunk in result:
            assert chunk != "", "Empty chunk found in result"

    def test_trailing_newlines_no_empty_chunk(self):
        """Text ending with newlines does not produce a trailing empty chunk."""
        text = ("a" * 3999 + "\n") * 2
        result = split_message(text, max_length=4000)
        for chunk in result:
            assert chunk != "", "Empty chunk found in result"

    # ------------------------------------------------------------------
    # HTML tag safety
    # ------------------------------------------------------------------

    def test_html_tag_not_split_when_newline_available(self):
        """HTML tag is kept intact when a newline falls before it in the window."""
        # Construct text: 3990 a's + newline + <b>bold text</b>
        prefix = "a" * 3990
        html_part = "<b>bold text</b>"
        text = prefix + "\n" + html_part
        result = split_message(text, max_length=4000)
        # The split should occur at the newline, keeping the HTML tag whole
        assert result[0] == prefix
        assert result[1] == html_part

    def test_html_tag_may_split_when_no_newline(self):
        """Without a newline, HTML tags may be split (caller must handle parse errors)."""
        tag_content = "<b>" + "x" * 3999 + "</b>"  # longer than 4000
        result = split_message(tag_content, max_length=4000)
        # Each chunk must be within max_length
        for chunk in result:
            assert len(chunk) <= 4000
        # Reassembled must equal original
        assert "".join(result) == tag_content

    def test_html_newline_boundary_chunk_lengths(self):
        """All chunks produced for HTML-heavy content are within max_length."""
        lines = [f"<b>Line {i}</b>: " + "x" * 200 for i in range(30)]
        text = "\n".join(lines)
        result = split_message(text, max_length=1000)
        for chunk in result:
            assert len(chunk) <= 1000

    # ------------------------------------------------------------------
    # Custom max_length
    # ------------------------------------------------------------------

    def test_custom_max_length(self):
        """Works correctly with non-default max_length values."""
        text = "ab\ncd\nef"
        result = split_message(text, max_length=5)
        for chunk in result:
            assert len(chunk) <= 5
        assert "\n".join(result) == text
