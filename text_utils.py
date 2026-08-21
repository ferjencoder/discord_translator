from __future__ import annotations

import re
import secrets
from dataclasses import dataclass


# Protect structures that should never be changed by machine translation.
# Order matters: code blocks and URLs must be consumed before shorter patterns.
PROTECTED_PATTERN = re.compile(
    r"(" 
    r"```[\s\S]*?```"                       # fenced code block
    r"|`[^`\n]+`"                            # inline code
    r"|https?://[^\s<>]+"                    # URL
    r"|@(?:everyone|here)\b"                  # broadcast mentions (kept literal; pings disabled)
    r"|<a?:[A-Za-z0-9_]+:\d+>"              # custom emoji
    r"|<@!?\d+>"                             # user mention
    r"|<@&\d+>"                              # role mention
    r"|<#\d+>"                               # channel mention
    r"|<t:\d+(?::[tTdDfFR])?>"               # Discord timestamp
    r"|</[^:>]{1,80}:\d+>"                   # slash command mention
    r"|\b[Kk]\s*:\s*\d+\s+[Xx]\s*:\s*\d+\s+[Yy]\s*:\s*\d+\b"  # TB coordinates
    r")",
    re.MULTILINE,
)

class TokenRestoreError(ValueError):
    pass


@dataclass(frozen=True)
class ProtectedText:
    text: str
    replacements: dict[str, str]


def protect_text(text: str) -> ProtectedText:
    replacements: dict[str, str] = {}
    nonce = secrets.token_hex(5).upper()

    def repl(match: re.Match[str]) -> str:
        placeholder = f"ZXQ{nonce}PB{len(replacements):04d}QXZ"
        replacements[placeholder] = match.group(0)
        return placeholder

    return ProtectedText(PROTECTED_PATTERN.sub(repl, text), replacements)


def restore_text(text: str, replacements: dict[str, str]) -> str:
    restored = text
    for placeholder, original in replacements.items():
        if placeholder not in restored:
            raise TokenRestoreError(f"Translator altered protected token {placeholder}")
        restored = restored.replace(placeholder, original)

    return restored


def _protected_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in PROTECTED_PATTERN.finditer(text)]


def _move_cut_outside_protected_span(text: str, start: int, cut: int) -> int:
    for span_start, span_end in _protected_spans(text):
        if span_start < cut < span_end:
            if span_start > start:
                return span_start
            # One protected token is itself longer than the Discord payload limit.
            # There is no perfect split in that case, so hard-cut at the limit.
            return cut
    return cut


def chunk_text(text: str, max_length: int = 1900) -> list[str]:
    """Split Discord text without cutting normal protected tokens when possible."""
    if max_length < 50:
        raise ValueError("max_length must be >= 50")
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    start = 0

    while start < len(text):
        remaining = len(text) - start
        if remaining <= max_length:
            chunks.append(text[start:])
            break

        hard_cut = start + max_length
        hard_cut = _move_cut_outside_protected_span(text, start, hard_cut)
        if hard_cut <= start:
            hard_cut = start + max_length

        # Prefer readable boundaries, but do not create tiny chunks just to find one.
        search_floor = start + max(1, int(max_length * 0.55))
        cut = hard_cut
        segment = text[search_floor:hard_cut]
        for delimiter in ("\n\n", "\n", ". ", "! ", "? ", " "):
            pos = segment.rfind(delimiter)
            if pos != -1:
                cut = search_floor + pos + len(delimiter)
                break

        cut = _move_cut_outside_protected_span(text, start, cut)
        if cut <= start:
            cut = hard_cut

        chunk = text[start:cut].rstrip()
        if not chunk:
            chunk = text[start:hard_cut]
            cut = hard_cut
        chunks.append(chunk)
        start = cut
        while start < len(text) and text[start] == " ":
            start += 1

    return chunks


def clean_preview(text: str, max_chars: int = 120) -> str:
    one_line = re.sub(r"\s+", " ", text or "").strip()
    if len(one_line) <= max_chars:
        return one_line
    return one_line[: max_chars - 1].rstrip() + "…"
