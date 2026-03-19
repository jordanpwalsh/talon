"""Telegram-specific message formatting.

Telegram only supports: <b>, <i>, <u>, <s>, <code>, <pre>, <a>, <blockquote>.
No headings, lists, or images — convert those to plain text equivalents.
"""

import html
import re

# Matches a markdown table: lines that start/end with |, with a separator row
_TABLE_RE = re.compile(
    r"((?:^\|.+\|[ ]*\n)(?:^\|[ :]*-[-| :]*\|[ ]*\n)(?:^\|.+\|[ ]*\n?)+)",
    re.MULTILINE,
)


def _format_prose(text: str) -> str:
    """Convert Markdown formatting in non-code, non-table text to HTML."""
    p = html.escape(text)
    # Headings → bold text
    p = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", p, flags=re.MULTILINE)
    # Horizontal rules → empty line
    p = re.sub(r"^-{3,}$", "", p, flags=re.MULTILINE)
    # Blockquotes
    p = re.sub(
        r"((?:^&gt; .+\n?)+)",
        lambda m: "<blockquote>"
        + m.group(0).replace("&gt; ", "").strip()
        + "</blockquote>",
        p,
        flags=re.MULTILINE,
    )
    # Bold
    p = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p)
    p = re.sub(r"__(.+?)__", r"<b>\1</b>", p)
    # Italic
    p = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", p)
    p = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", p)
    # Strikethrough
    p = re.sub(r"~~(.+?)~~", r"<s>\1</s>", p)
    # Links [text](url)
    p = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', p)
    return p


def md_to_telegram_html(text: str) -> str:
    """Convert common Markdown to Telegram-safe HTML."""
    # First, handle fenced code blocks (preserve as-is)
    # Use placeholders so bold/italic regexes don't touch them
    placeholders: list[str] = []

    def _placeholder(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"\x00PH{len(placeholders) - 1}\x00"

    # Protect fenced code blocks
    text = re.sub(r"```[\s\S]*?```", _placeholder, text)
    # Protect inline code
    text = re.sub(r"`[^`]+`", _placeholder, text)
    # Protect tables
    text = _TABLE_RE.sub(_placeholder, text)

    # Now convert markdown formatting on the remaining text
    text = _format_prose(text)

    # Restore placeholders with proper HTML
    def _restore(match: re.Match) -> str:
        idx = int(match.group(1))
        original = placeholders[idx]
        if original.startswith("```"):
            inner = original[3:].rstrip("`")
            if "\n" in inner:
                inner = inner.split("\n", 1)[1]
            return f"<pre>{html.escape(inner)}</pre>"
        elif original.startswith("`"):
            inner = original.strip("`")
            return f"<code>{html.escape(inner)}</code>"
        elif original.startswith("|"):
            return f"<pre>{html.escape(original.rstrip())}</pre>"
        return html.escape(original)

    return re.sub(r"\x00PH(\d+)\x00", _restore, text)
