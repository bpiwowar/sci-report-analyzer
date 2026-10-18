"""An excerpt's basic formatting, as inline Markdown: its bold (``**…**``) and italic
(``*…*``) words, read in its PDF (see vrPdf.formatted in pdf_viewer.js), carried over to the
text selected (``formatted_like``); its plain text (``plain``: to match, search, classify,
read its years or reword it; old excerpts, plain, unchanged)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# Escaped by a backslash (as vrPdf.formatted does).
_SPECIAL = "\\`*_[]#<>"
_ESCAPE = re.compile("([" + re.escape(_SPECIAL) + "])")
_UNESCAPE = re.compile(r"\\([" + re.escape(_SPECIAL) + "])")
# Emphasis: a run of one to three stars, around text neither starting nor ending with a
# space (CommonMark's flanking: "2 * 3 * 4" is not emphasis).
_EMPHASIS = re.compile(r"(?<![\\*])(\*{1,3})(?=[^\s*])(.+?)(?<=[^\s\\*])\1(?!\*)")
_PRIVATE = 0xE000  # (see html)
_TOKEN = re.compile(r"\\.|\*+|[^\\*]+", re.S)
_EDGES = re.compile(r"(\W*)(.*?)(\W*)$", re.S)


def escape(text: str) -> str:
    return _ESCAPE.sub(r"\\\1", text)


def plain(text: str) -> str:
    """``text`` without its emphasis markers and escapes (plain text: as is)."""
    previous = None
    while previous != text:  # (from the outside in)
        previous, text = text, _EMPHASIS.sub(r"\2", text)
    return _UNESCAPE.sub(r"\1", text)


def _styled_words(md: str) -> list[tuple[str, bool, bool]]:
    """The words of vrPdf.formatted's Markdown (its markers always paired), with whether
    each is bold, italic (a word partly so: not)."""
    bold = italic = False
    words: list[tuple[str, bool, bool]] = []
    glued = False  # (the next characters continue the last word)
    for token in _TOKEN.findall(md):
        if token.startswith("*"):
            n = len(token)
            bold, italic = bold ^ (n >= 2), italic ^ (n in (1, 3))
            continue
        chars = token[1] if token.startswith("\\") else token
        for i, part in enumerate(re.split(r"(\s+)", chars)):
            if i % 2:  # (a space)
                glued = False
            elif part:
                if glued:  # (its punctuation not counting)
                    w, b, it = words[-1]
                    if not _key(part):
                        words[-1] = (w + part, b, it)
                    elif not _key(w):
                        words[-1] = (w + part, bold, italic)
                    else:
                        words[-1] = (w + part, b and bold, it and italic)
                else:
                    words.append((part, bold, italic))
                glued = True
    return words


def _key(word: str) -> str:
    return "".join(c for c in word.lower() if c.isalnum())


def formatted_like(text: str, md: str | None) -> str:
    """``text`` (plain, e.g. selected in a PDF) with the bold and italic words of ``md`` (the
    same passage, as Markdown: see vrPdf.formatted; its words read from the page may differ a
    little at its ends), aligned word by word; none formatted (or no ``md``): ``text`` as is.
    Its spaces collapsed."""
    words = text.split()
    styled = _styled_words(md or "")
    if not words or not any(b or i for _w, b, i in styled):
        return text
    styles = [(False, False)] * len(words)
    matcher = SequenceMatcher(
        None, [_key(w) for w in words], [_key(w) for w, _b, _i in styled], autojunk=False
    )
    for a, b, size in matcher.get_matching_blocks():
        if size < min(2, len(words)):  # (a word alone: by chance)
            continue
        for k in range(size):
            styles[a + k] = styled[b + k][1:]
    if not any(b or i for b, i in styles):
        return text
    out: list[str] = []
    start = 0
    for end in range(1, len(words) + 1):
        if end == len(words) or styles[end] != styles[start]:
            bold, italic = styles[start]
            mark = ("**" if bold else "") + ("*" if italic else "")
            group = " ".join(words[start:end])
            if mark:  # (its punctuation at its ends: outside)
                before, inner, after = _EDGES.match(group).groups()  # type: ignore[union-attr]
                if inner:
                    out.append(escape(before) + mark + escape(inner) + mark[::-1] + escape(after))
                    start = end
                    continue
            out.append(escape(group))
            start = end
    return " ".join(out)


def html(text: str) -> str:
    """``text`` as HTML (escaped), its bold and italics as such (e.g. in a list)."""
    # (its escaped characters out of the way: private-use ones, then back, escaped)
    out = _UNESCAPE.sub(lambda m: chr(_PRIVATE + _SPECIAL.index(m.group(1))), text)
    out, previous = escape_html(out), None
    while previous != out:  # (as plain: from the outside in)
        previous, out = out, _EMPHASIS.sub(_tag, out)
    return "".join(
        escape_html(_SPECIAL[ord(c) - _PRIVATE])
        if _PRIVATE <= ord(c) < _PRIVATE + len(_SPECIAL)
        else c
        for c in out
    )


def _tag(m: re.Match[str]) -> str:
    n, inner = len(m.group(1)), m.group(2)
    if n in (1, 3):
        inner = f"<i>{inner}</i>"
    return f"<b>{inner}</b>" if n >= 2 else inner


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
