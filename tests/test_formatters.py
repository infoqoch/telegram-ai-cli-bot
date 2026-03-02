"""메시지 포맷터 테스트.

Telegram HTML 변환 및 문자열 처리 기능 검증:
- 마크다운 → Telegram HTML 변환
- 코드 블록/인라인 코드 처리
- XSS 방지 (HTML 이스케이프)
- 문자열 자르기
"""

import pytest

from src.bot.formatters import markdown_to_telegram_html, truncate_message


class TestMarkdownToTelegramHtml:
    """마크다운 → Telegram HTML 변환 테스트."""

    def test_bold(self):
        """**bold** → <b>bold</b> 변환."""
        result = markdown_to_telegram_html("**bold text**")
        assert result == "<b>bold text</b>"
    
    def test_italic(self):
        """*italic* → <i>italic</i> 변환."""
        result = markdown_to_telegram_html("*italic text*")
        assert result == "<i>italic text</i>"
    
    def test_strikethrough(self):
        """~~strike~~ → <s>strike</s> 변환."""
        result = markdown_to_telegram_html("~~strike~~")
        assert result == "<s>strike</s>"
    
    def test_inline_code(self):
        """`code` → <code>code</code> 변환."""
        result = markdown_to_telegram_html("use `code` here")
        assert result == "use <code>code</code> here"
    
    def test_code_block(self):
        """```python 코드블록 → <pre><code> 변환."""
        result = markdown_to_telegram_html("```python\nprint('hello')\n```")
        assert '<pre><code class="language-python">' in result
        assert "print(&#x27;hello&#x27;)" in result
    
    def test_html_escape(self):
        """HTML 태그 이스케이프 (XSS 방지)."""
        result = markdown_to_telegram_html("test <script>alert('xss')</script>")
        assert "&lt;script&gt;" in result
        assert "<script>" not in result
    
    def test_mixed_formatting(self):
        """복합 포맷팅 처리."""
        result = markdown_to_telegram_html("**bold** and *italic* and `code`")
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result
        assert "<code>code</code>" in result


class TestTruncateMessage:
    """문자열 자르기 테스트."""

    def test_short_message(self):
        """짧은 메시지는 그대로 반환."""
        result = truncate_message("short", 40)
        assert result == "short"
    
    def test_exact_length(self):
        """정확히 최대 길이면 그대로 반환."""
        result = truncate_message("a" * 40, 40)
        assert result == "a" * 40
    
    def test_long_message(self):
        """긴 메시지는 잘라서 ... 추가."""
        result = truncate_message("a" * 50, 40)
        assert result == "a" * 40 + "..."
        assert len(result) == 43
