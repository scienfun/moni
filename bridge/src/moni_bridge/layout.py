"""말풍선 줄바꿈과 페이지 나누기. 글자 폭 측정은 호출자가 넘긴다."""

from __future__ import annotations

from typing import Callable

Measure = Callable[[str], float]


def _break_word(word: str, measure: Measure, max_width: float) -> list[str]:
    parts: list[str] = []
    current = ""
    for char in word:
        if current and measure(current + char) > max_width:
            parts.append(current)
            current = char
        else:
            current += char
    if current:
        parts.append(current)
    return parts


def wrap_text(text: str, measure: Measure, max_width: float) -> list[str]:
    """공백 기준으로 줄을 나누고, 한 단어가 너무 길면 글자 단위로 자른다."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}" if current else word
            if measure(candidate) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            pieces = _break_word(word, measure, max_width)
            lines.extend(pieces[:-1])
            current = pieces[-1]
        if current:
            lines.append(current)
    return lines


def paginate(
    lines: list[str],
    per_page: int,
    measure: Measure,
    max_width: float,
    max_pages: int = 2,
) -> list[list[str]]:
    """최대 페이지를 넘는 내용은 잘라 마지막 줄에 말줄임표를 붙인다."""
    limit = per_page * max_pages
    kept = lines[:limit]
    if len(lines) > limit:
        last = kept[-1]
        while last and measure(last + "...") > max_width:
            last = last[:-1]
        kept[-1] = last + "..."
    return [kept[i : i + per_page] for i in range(0, len(kept), per_page)] or [[]]
