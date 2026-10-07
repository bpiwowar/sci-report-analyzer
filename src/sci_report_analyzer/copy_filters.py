"""Filters a copied text can go through (Shift+copy), each turning the Markdown of a report
(the notes, with their citations substituted; the excerpts) into what a form expects: rich
HTML (pasted formatted) and its plain text. Those enabled are chosen in the settings."""

from __future__ import annotations

import html as _html
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass

import markdown

from .db.app_settings import get_setting, set_setting
from .i18n import N_

ENABLED_KEY = "copy.filters"


@dataclass(frozen=True)
class Filter:
    label: str
    description: str
    apply: Callable[[str], tuple[str, str]]  # Markdown -> (HTML, plain text)


def enabled() -> list[str]:
    """The names of the filters enabled (in their order in FILTERS)."""
    on = set(get_setting(ENABLED_KEY) or [])
    return [n for n in FILTERS if n in on]


def set_enabled(names: list[str]) -> None:
    set_setting(ENABLED_KEY, [n for n in FILTERS if n in names])


# ---- Tags turned into text (e.g. #influence into an icon), with a colour -------------------

TAG_RULES_KEY = "copy.tag-rules"


@dataclass(frozen=True)
class TagRule:
    """A tag (without "#") replaced, on its line, by a text before that line's (e.g. an
    icon) and one after, in a colour (#RRGGBB; "": none; ``highlight``: as background), in
    bold if ``bold``; that of the line's text too if ``whole``. (Marmotte keeps these, not
    superscripts, font sizes or strikethroughs.)"""

    tag: str
    before: str = ""
    after: str = ""
    color: str = ""
    whole: bool = False
    bold: bool = False
    highlight: bool = False


# Until some are set: the "rayonnement" excerpts marked in their categories as a globe.
DEFAULT_TAG_RULES = [TagRule("influence", "🌍")]


def tag_rules() -> list[TagRule]:
    saved = get_setting(TAG_RULES_KEY)
    if saved is None:
        return list(DEFAULT_TAG_RULES)
    return [TagRule(**r) for r in saved if r.get("tag")]


def set_tag_rules(rules: list[TagRule]) -> None:
    set_setting(TAG_RULES_KEY, [asdict(r) for r in rules if r.tag.strip("# ")])


def _tag_re(tag: str) -> re.Pattern:
    return re.compile(r"(?<!\S)#" + re.escape(tag.strip("# ")) + r"(?![\w/-])[ \t]*")


def apply_tag_rules(lines: list[str], rules: list[TagRule]) -> list[str]:
    """The lines with a tag of the rules (not headings; not in code): that tag removed, the
    rule's texts around the line's text (after its list marker), styled as it says (HTML
    spans, <strong>)."""
    found = [(_tag_re(r.tag), r) for r in rules]

    def styled(text: str, r: TagRule) -> str:
        if not text:
            return text
        if r.bold:
            text = f"<strong>{text}</strong>"
        c = _flatten_hex(r.color)
        if HEX_RE.match(c):
            text = (
                f'<span style="{"background-color" if r.highlight else "color"}:{c}">{text}</span>'
            )
        return text

    def apply(line: str) -> str:
        if HEADING_RE.match(line):
            return line
        for pattern, r in found:
            hit = False

            def drop(p: str, pattern=pattern) -> str:
                nonlocal hit
                q = pattern.sub("", p)
                hit = hit or q != p
                return q

            stripped = _outside_code(line, drop)
            if not hit:
                continue
            marker = LIST_RE.match(stripped)
            head = marker.group(0) if marker else ""
            text = stripped[len(head) :].strip()
            before, after = (_html.escape(x.strip(), quote=False) for x in (r.before, r.after))
            if not r.whole:
                before, after = styled(before, r), styled(after, r)
            text = " ".join(x for x in (before, text, after) if x)
            line = head + (styled(text, r) if r.whole else text)
        return line

    return _outside_fences(lines, apply)


# ---- Marmotte: a CKEditor with only basic formatting (no headings, tables, lists) ----------

# The headings: "#" numbered as a banner ("1."), "##" a bold dash line, deeper as they are.
HEADING_TEMPLATES = {1: "<strong>--- {num} {title} ---</strong>\n", 2: "<strong>- {title}</strong>"}
DEFAULT_TEMPLATE = "{title}"
NUMBER_FORMATS = {1: "{c1}.", 2: "{c1}.{c2}"}
DROP_TAGS = ("question", "to-check")  # (a line with one of them: left out)

TAG_RE = re.compile(r"(?<!\S)#(?:" + "|".join(DROP_TAGS) + r")(?![\w/-])")
# Any other tag (#positif, #a/b…): removed, its line kept ("#1" is not a tag).
ANY_TAG_RE = re.compile(r"(?<!\S)#[\w/-]*[^\W\d][\w/-]*[ \t]*")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
CALLOUT_RE = re.compile(r"^\s*>\s*\[!")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s")  # (a task: "- [ ] …", "- [x] …")
TABLE_ROW_RE = re.compile(r"^\s*\|?[^|\n]*\|.*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)")
CODE_SPAN_RE = re.compile(r"(`[^`]*`)")

HIGHLIGHT_DEFAULT = "#FFF3A3"  # (Obsidian's, opaque)
MARK_RE = re.compile(r"<mark\b([^>]*)>(.*?)</mark>", re.DOTALL | re.IGNORECASE)
STYLE_BG_RE = re.compile(r"background(?:-color)?\s*:\s*([^;\"']+)", re.IGNORECASE)
HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
SPACER = "<p><br></p>"  # (an empty line, as CKEditor writes it: kept when pasted)
TABLE_LEFT_OUT = "<p><em>[tableau omis &mdash; à reformater manuellement]</em></p>"


def _outside_fences(lines: list[str], f: Callable[[str], str | None]) -> list[str]:
    """Each line outside the fenced code blocks through ``f`` (None: left out)."""
    out, fenced = [], False
    for line in lines:
        if FENCE_RE.match(line):
            fenced = not fenced
            out.append(line)
        elif fenced:
            out.append(line)
        elif (x := f(line)) is not None:
            out.append(x)
    return out


def _outside_code(line: str, f: Callable[[str], str]) -> str:
    parts = CODE_SPAN_RE.split(line)
    return "".join(f(p) if i % 2 == 0 else p for i, p in enumerate(parts))


def strip_comments(text: str) -> str:
    """Without the Obsidian comments (``%% … %%``, over lines too), but in code blocks."""
    parts = re.split(r"(?m)(^\s*(?:```|~~~).*$(?:.|\n)*?^\s*(?:```|~~~).*$)", text)
    return "".join(
        p if i % 2 else re.sub(r"%%.*?%%", "", p, flags=re.DOTALL) for i, p in enumerate(parts)
    )


def strip_callouts_and_tags(lines: list[str]) -> list[str]:
    """Without the callouts, the tasks and the lines tagged with DROP_TAGS; the other tags
    removed (a line of tags only: left out)."""
    callout = False

    def keep(line: str) -> str | None:
        nonlocal callout
        if callout:  # (until a line neither blank nor quoted)
            if not line.strip() or line.lstrip().startswith(">"):
                return None
            callout = False
        if CALLOUT_RE.match(line):
            callout = True
            return None
        if TAG_RE.search(line) or CHECKBOX_RE.match(line):
            return None
        if HEADING_RE.match(line):
            return line
        stripped = _outside_code(line, lambda p: ANY_TAG_RE.sub("", p)).rstrip()
        if stripped != line.rstrip() and not re.sub(r"^\s*(?:[-*+]|\d+\.)\s*", "", stripped):
            return None
        return stripped

    return _outside_fences(lines, keep)


def replace_tables(lines: list[str]) -> list[str]:
    """Each table replaced by a note that it is left out (the editor has none)."""
    out, i, fenced = [], 0, False
    while i < len(lines):
        line = lines[i]
        if FENCE_RE.match(line):
            fenced = not fenced
        elif (
            not fenced
            and i + 1 < len(lines)
            and TABLE_ROW_RE.match(line)
            and TABLE_SEP_RE.match(lines[i + 1])
        ):
            i += 2
            while (
                i < len(lines) and TABLE_ROW_RE.match(lines[i]) and not TABLE_SEP_RE.match(lines[i])
            ):
                i += 1
            out += ["", SPACER, TABLE_LEFT_OUT, SPACER, ""]
            continue
        out.append(line)
        i += 1
    return out


def blank_before_lists(lines: list[str]) -> list[str]:
    """A blank line before a list following a paragraph (as Obsidian reads it)."""
    out, fenced = [], False
    for line in lines:
        if FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced and out and LIST_RE.match(line):
            prev = out[-1]
            if prev.strip() and not LIST_RE.match(prev) and not HEADING_RE.match(prev):
                out.append("")
        out.append(line)
    return out


def _flatten_hex(colour: str) -> str:
    """An opaque #RRGGBB (an alpha blended over white: the editors ignore it)."""
    c = colour.strip()
    if not HEX_RE.match(c):
        return c
    h = c[1:]
    if len(h) in (3, 4):
        h = "".join(ch * 2 for ch in h)
    if len(h) == 8:
        r, g, b, a = (int(h[i : i + 2], 16) for i in (0, 2, 4, 6))
        r, g, b = (round(v * a / 255 + 255 * (1 - a / 255)) for v in (r, g, b))
        return f"#{r:02X}{g:02X}{b:02X}"
    return "#" + h[:6].upper()


def _highlights(md: str) -> str:
    """Obsidian's ``==text==`` as <mark> (not in code)."""
    return "\n".join(
        _outside_fences(
            md.split("\n"),
            lambda line: _outside_code(line, lambda p: re.sub(r"==(.+?)==", r"<mark>\1</mark>", p)),
        )
    )


def normalize_highlights(html: str) -> str:
    """Each <mark> as a <span> with its background colour (kept when pasted)."""

    def repl(m: re.Match) -> str:
        found = STYLE_BG_RE.search(m.group(1))
        colour = found.group(1).strip() if found else HIGHLIGHT_DEFAULT
        return f'<span style="background-color:{_flatten_hex(colour)}">{m.group(2)}</span>'

    return MARK_RE.sub(repl, html)


def _lists_to_paragraphs(html: str) -> str:
    """The lists as paragraphs ("• …", "1. …"; nested ones indented): the editor drops
    <ul>/<li>."""
    tokens = re.split(r"(</?(?:ul|ol|li)\b[^>]*>)", html)
    out, stack, cur = [], [], None  # (stack: [tag, items so far])

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            text = re.sub(r"</p>\s*<p>", "<br>", cur)
            text = re.sub(r"</?p>", "", text).replace("\n", " ").strip()
            if text:
                tag, n = stack[-1]
                marker = f"{n}." if tag == "ol" else "•"
                indent = "&nbsp;" * 4 * (len(stack) - 1)
                out.append(f"<p data-bullet>{indent}{marker} {text}</p>")
        cur = None

    for tok in tokens:
        m = re.match(r"<(/?)(ul|ol|li)\b", tok)
        if not m:
            if not stack:
                out.append(tok)
            elif cur is not None:
                cur += tok
            continue
        closing, tag = m.groups()
        flush()
        if tag == "li":
            if not closing and stack:
                stack[-1][1] += 1
                cur = ""
        elif not closing:
            stack.append([tag, 0])
        elif stack:
            stack.pop()
            if not stack:
                out.append("\n")
    return "".join(out)


def _body(md: str) -> str:
    """Markdown → HTML, an empty paragraph between paragraphs (the editor collapses their
    margins), but between the items of a list."""
    html = markdown.markdown(
        _highlights(md), extensions=["extra", "sane_lists", "nl2br"], output_format="html"
    )
    html = _lists_to_paragraphs(html)
    out, bullet = [], False
    for is_bullet, body in re.findall(r"<p( data-bullet)?>(.*?)</p>", html, flags=re.DOTALL):
        if out and not (bullet and is_bullet):
            out.append(SPACER)
        out.append(f"<p>{body}</p>")
        bullet = bool(is_bullet)
    return "\n".join(out)


def _render(lines: list[str]) -> str:
    """The headings from their templates (numbered), the rest through Markdown."""
    parts, buf, fenced, counts = [], [], False, [0] * 7

    def flush() -> None:
        md = "\n".join(buf).strip("\n")
        if md.strip():
            parts.append(_body(md))
        buf.clear()

    for line in lines:
        if FENCE_RE.match(line):
            fenced = not fenced
        m = None if fenced else HEADING_RE.match(line)
        if not m:
            buf.append(line)
            continue
        flush()
        level = len(m.group(1))
        counts[level] += 1
        counts[level + 1 :] = [0] * (6 - level)
        num = NUMBER_FORMATS.get(level, "").format(c1=counts[1], c2=counts[2])
        template = HEADING_TEMPLATES.get(level, DEFAULT_TEMPLATE)
        if parts:
            parts.append(SPACER)
        title = template.format(num=num, title=_html.escape(m.group(2).strip()))
        parts.append("<p>" + "<br>\n".join(title.split("\n")) + "</p>")
        parts.append(SPACER)
    flush()
    return "\n".join(parts)


def marmotte(md: str, rules: list[TagRule] | None = None) -> tuple[str, str]:
    """For Marmotte's forms (a CKEditor with only basic formatting): the whole text, its
    comments, callouts, tasks and tags left out (with the lines tagged #question or
    #to-check; those of the tag ``rules``, None: the settings', turned into their texts),
    "#" headings as numbered banners, "##" as bold lines, lists as paragraphs, tables left
    out."""
    lines = strip_comments(md).splitlines()
    lines = apply_tag_rules(lines, tag_rules() if rules is None else rules)
    lines = blank_before_lists(replace_tables(strip_callouts_and_tags(lines)))
    html = normalize_highlights(_render(lines))
    html = re.sub(r"(?:<p><br></p>\s*){2,}", SPACER + "\n", html).strip()
    plain = re.sub(r"<br>\n?", "\n", html)
    plain = re.sub(r"<[^>]+>", "", plain.replace("</p>", "\n"))
    return html, re.sub(r"\n\s*\n(?:\s*\n)+", "\n\n", _html.unescape(plain)).strip()


FILTERS: dict[str, Filter] = {
    "marmotte": Filter(
        N_("Marmotte (CNRS)"),
        N_(
            "Rich text for Marmotte's forms (basic formatting only): # headings numbered as "
            "banners, ## as bold lines, lists as paragraphs; comments (%% … %%), callouts, "
            "tasks, tags (but those turned into a text: see below) and the lines tagged "
            "#question or #to-check left out, tables too. Marmotte keeps bold, italics, "
            "underlining, text colours, highlights, emojis and symbols (★ ✦ ◆ ➤), not "
            "superscripts, font sizes or strikethroughs."
        ),
        marmotte,
    ),
}
