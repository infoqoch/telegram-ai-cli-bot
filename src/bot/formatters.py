"""Message formatting utilities for Telegram."""

import html
import re

from src.ai import get_provider_icon, infer_provider_from_model
from .constants import get_model_badge


def escape_html(text: str) -> str:
    """Escape text for safe embedding in Telegram HTML messages."""
    return html.escape(str(text)) if text else ""


def markdown_to_telegram_html(text: str) -> str:
    """Convert markdown to Telegram HTML format."""
    # Save code blocks with unique markers
    code_blocks: list[str] = []
    inline_codes: list[str] = []
    
    def save_code_block(match: re.Match) -> str:
        code_blocks.append(match.group(0))
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"
    
    def save_inline_code(match: re.Match) -> str:
        inline_codes.append(match.group(1))
        return f"\x00INLINECODE{len(inline_codes) - 1}\x00"
    
    # Extract code blocks and inline code
    text = re.sub(r'```(\w*)\n?([\s\S]*?)```', save_code_block, text)
    text = re.sub(r'`([^`]+)`', save_inline_code, text)
    
    # Escape HTML in remaining text
    text = html.escape(text)
    
    # Convert markdown to HTML
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)  # bold
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)      # italic
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)      # strikethrough
    
    # Restore code blocks
    for i, block in enumerate(code_blocks):
        match = re.match(r'```(\w*)\n?([\s\S]*?)```', block)
        if match:
            lang = match.group(1)
            code = html.escape(match.group(2).strip())
            if lang:
                replacement = f'<pre><code class="language-{lang}">{code}</code></pre>'
            else:
                replacement = f'<pre>{code}</pre>'
            text = text.replace(f"\x00CODEBLOCK{i}\x00", replacement)
    
    # Restore inline code
    for i, code in enumerate(inline_codes):
        escaped_code = html.escape(code)
        text = text.replace(f"\x00INLINECODE{i}\x00", f'<code>{escaped_code}</code>')
    
    return text


def truncate_message(text: str, max_length: int = 40) -> str:
    """Truncate message with ellipsis."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def format_session_list(sessions: list[dict], summaries: dict[str, str]) -> str:
    """Format session list with summaries."""
    if not sessions:
        return "📭 No saved sessions."

    lines = []
    for s in sessions:
        current_mark = " ⬅️" if s.get("is_current") else ""
        summary = summaries.get(s["full_session_id"], "(no content)")

        lines.append(
            f"<b>/s_{s['session_id']}</b> ({s['history_count']}){current_mark}\n"
            f"{escape_html(summary)}"
        )

    return f"📋 <b>Saved Sessions ({len(sessions)})</b>\n\n" + "\n\n".join(lines)


def format_session_quick_list(sessions: list[dict], histories: dict[str, list[str]]) -> str:
    """Format quick session list with last messages."""
    if not sessions:
        return "📭 No saved sessions."

    lines = []
    for s in sessions:
        history = histories.get(s["full_session_id"], [])
        last_msg = truncate_message(history[-1]) if history else "-"
        current_mark = " ⬅️" if s.get("is_current") else ""
        model = s.get("model", "sonnet")
        emoji = get_model_badge(model)
        provider = s.get("ai_provider") or infer_provider_from_model(model)
        provider_icon = get_provider_icon(provider)
        name = s.get("name", "")
        name_display = f" <b>{escape_html(name)}</b>" if name else ""

        lines.append(
            f"/s_{s['session_id']}{name_display} {provider_icon} {emoji}{model} ({s['history_count']}){current_mark}\n"
            f"   └ Recent: {escape_html(last_msg)}\n"
            f"   └ /h_{s['session_id']} /r_{s['session_id']} /d_{s['session_id']}"
        )

    return f"📋 <b>Saved Sessions ({len(sessions)})</b>\n\n" + "\n\n".join(lines)
