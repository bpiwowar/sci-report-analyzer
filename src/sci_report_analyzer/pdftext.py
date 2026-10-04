"""Text taken from a PDF (selected, copied, filed as an excerpt, quoted), cleaned the way
the Better Paste plugin of Obsidian cleans it: ligatures expanded, accents a LaTeX font
puts before their letter joined to it, invisible characters removed, the lines of a
paragraph joined again (a word hyphenated at a line end mended), and the space runs of
justified text collapsed.

The same rules run in the browser (``SCRIPT``: ``vrCleanPdfText``) for the clipboard, which
must be filled at once when copying: the tests check both on the same cases.
"""

from __future__ import annotations

import json
import re
import unicodedata

# The Latin ligatures of publishers' fonts (ff fi fl ffi ffl, the long s ones): a search for
# "financial" never finds the word with a ligature in it.
LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}

# The spacing accents a LaTeX font puts before their letter ("d´etecter"), as combining ones.
ACCENTS = {
    "\u00b4": "\u0301",
    "\u02dd": "\u030b",
    "\u02c6": "\u0302",
    "\u00a8": "\u0308",
    "\u02dc": "\u0303",
    "\u00b8": "\u0327",
    "\u02c7": "\u030c",
    "\u02d8": "\u0306",
    "\u02da": "\u030a",
    "\u02db": "\u0328",
    "\u02d9": "\u0307",
    "`": "\u0300",
    "^": "\u0302",
    "~": "\u0303",
}

# Shortest line a layout can have wrapped (a two-column paper's run near 45 characters), and
# how much shorter than the longest a wrapped line can be (the word that did not fit).
MIN_WRAP_WIDTH = 35
WRAP_TOLERANCE = 24

_LETTER = r"[^\W\d_]"
_SPLIT_ACCENT = re.compile("([" + re.escape("".join(ACCENTS)) + "])(" + _LETTER + ")")
_INVISIBLE = re.compile("[\u200b\ufeff\u202d\u202e]")
_SPACE = re.compile("[\u00a0\u1680\u2000-\u200a\u202f\u205f]")
_HYPHENATED = re.compile(r"[^\W_][-\u00ad\u2010\ufe63\uff0d]$")
_URL_END = re.compile(r"://\S*$|(?:^|\s)www\.\S+$", re.IGNORECASE)
_CJK = "\u0e00-\u0e7f\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
# A line starting a block of its own: a bullet, a numbered or enumerated item ("2.", "(a)",
# "[12]", "b)").
_NEW_BLOCK = re.compile(
    r"^(?:[\u2022\u2023\u25aa\u25ab\u25b8\u25e6\u00b7*+-]\s"
    r"|\d{1,9}[.)]\s|\([a-z0-9]{1,4}\)\s|\[\d{1,4}\]\s|[a-z0-9]{1,4}\)\s)",
    re.IGNORECASE,
)


def _join_accent(m: re.Match[str]) -> str:
    accent, letter = m.group(1), m.group(2)
    text, at, end = m.string, m.start(), m.end()
    if accent in "`^~":  # (also ASCII: only within a word, or a grave alone, as in "`a")
        before = text[at - 1] if at else ""
        after = text[end] if end < len(text) else ""
        in_word = before.isalpha()
        grave = (
            accent == "`"
            and not (before.isalnum() or before == "`")
            and not (after.isalnum() or after == "`")
        )
        if not in_word and not grave:
            return m.group(0)
    base = {"\u0131": "i", "\u0237": "j"}.get(letter, letter)  # (dotless i, j)
    composed = unicodedata.normalize("NFC", base + ACCENTS[accent])
    return composed if len(composed) == 1 else m.group(0)


def _ends_hyphenated(line: str) -> bool:
    return bool(_HYPHENATED.search(line))


def join_lines(previous: str, line: str) -> str:
    """Two lines of a paragraph, joined: a web address cut there keeps its break; a word
    hyphenated there is mended (but a compound, before a capital or after a digit); no
    space after a dash or between CJK characters."""
    if _URL_END.search(previous):
        return f"{previous}\n{line}"
    if previous.endswith("\u00ad"):  # (a soft hyphen only marks where the word was cut)
        return previous[:-1] + line
    if _ends_hyphenated(previous):
        broken = not "0" <= previous[-2] <= "9" and line[:1].islower()
        return (previous[:-1] if broken else previous) + line
    if re.search(r"\S[\u2013\u2014]$", previous):
        return previous + line
    if re.search(f"[{_CJK}]$", previous) and re.match(f"[{_CJK}]", line):
        return previous + line
    return f"{previous} {line}"


def wrap_width(lines: list[str]) -> int:
    """The width the lines were wrapped at: near the longest, when two lines at least are
    that long (else ``MIN_WRAP_WIDTH``)."""
    lengths = [len(x) for x in lines if x]
    longest = max(lengths, default=0)
    if len(lengths) < 2 or sum(n >= longest - WRAP_TOLERANCE for n in lengths) < 2:
        return MIN_WRAP_WIDTH
    return max(MIN_WRAP_WIDTH, longest - WRAP_TOLERANCE)


def clean_pdf_text(text: str, *, one_paragraph: bool = False) -> str:
    """``text`` taken from a PDF, cleaned (see above). Its paragraphs: a line short of the
    wrap width (but hyphenated) ends one, a blank line too, and a list item starts one;
    ``one_paragraph``: all joined (e.g. an excerpt)."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(LIGATURES.get(c, c) for c in text)
    text = _SPLIT_ACCENT.sub(_join_accent, text)
    text = _SPACE.sub(" ", _INVISIBLE.sub("", text))
    text = re.sub("\u00ad(?![ \t]*\n)", "", text)  # (within a word; at a line end: a cut)
    lines = [x.strip() for x in text.split("\n")]
    width = wrap_width(lines)
    paragraphs: list[str] = []
    previous = None  # (the line before, if the next one may join its paragraph)
    for line in lines:
        if not line:
            if not one_paragraph and paragraphs and paragraphs[-1]:
                paragraphs.append("")
            previous = previous if one_paragraph else None
            continue
        if previous is not None and (
            one_paragraph
            or (
                not _NEW_BLOCK.match(line)
                and not _URL_END.search(previous)
                and (len(previous) >= width or _ends_hyphenated(previous))
            )
        ):
            paragraphs[-1] = join_lines(paragraphs[-1], line)
        else:
            paragraphs.append(line)
        previous = line
    out = "\n".join(paragraphs).strip().replace("\u00ad", "")
    return re.sub(r"(\S) {2,}(?=\S)", r"\1 ", out)


# The same, in the browser: ``vrCleanPdfText(text, onePara)``.
_SCRIPT = r"""
<script>
window.vrCleanPdfText = (() => {
  const LIGATURES = %(ligatures)s, ACCENTS = %(accents)s;
  const MIN = %(min)d, TOLERANCE = %(tolerance)d;
  const CJK = '\u0e00-\u0e7f\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef';
  const esc = (s) => s.replace(/[\\^$.*+?()[\]{}|-]/g, '\\$&');
  const SPLIT = new RegExp('([' + esc(Object.keys(ACCENTS).join('')) + '])(\\p{L})', 'gu');
  const HYPHENATED = /[\p{L}\p{N}][-\u00ad\u2010\ufe63\uff0d]$/u;
  const URL_END = /:\/\/\S*$|(?:^|\s)www\.\S+$/i;
  const NEW_BLOCK = new RegExp('^(?:[\u2022\u2023\u25aa\u25ab\u25b8\u25e6\u00b7*+-]\\s'
    + '|\\d{1,9}[.)]\\s|\\([a-z0-9]{1,4}\\)\\s|\\[\\d{1,4}\\]\\s|[a-z0-9]{1,4}\\)\\s)', 'i');
  const isLetter = (c) => /\p{L}/u.test(c), isAlnum = (c) => /[\p{L}\p{N}]/u.test(c);
  const joinAccent = (m, accent, letter, at, text) => {
    if ('`^~'.includes(accent)) {
      const before = text[at - 1] || '', after = text[at + m.length] || '';
      const grave = accent === '`' && !(isAlnum(before) || before === '`')
        && !(isAlnum(after) || after === '`');
      if (!isLetter(before) && !grave) return m;
    }
    const base = letter === '\u0131' ? 'i' : letter === '\u0237' ? 'j' : letter;
    const composed = (base + ACCENTS[accent]).normalize('NFC');
    return [...composed].length === 1 ? composed : m;
  };
  const join = (previous, line) => {
    if (URL_END.test(previous)) return previous + '\n' + line;
    if (previous.endsWith('\u00ad')) return previous.slice(0, -1) + line;
    if (HYPHENATED.test(previous)) {
      const broken = !/\d/.test(previous[previous.length - 2]) && /^\p{Ll}/u.test(line);
      return (broken ? previous.slice(0, -1) : previous) + line;
    }
    if (/\S[\u2013\u2014]$/.test(previous)) return previous + line;
    if (new RegExp('[' + CJK + ']$').test(previous) && new RegExp('^[' + CJK + ']').test(line)) {
      return previous + line;
    }
    return previous + ' ' + line;
  };
  const wrapWidth = (lines) => {
    const lengths = lines.filter(x => x).map(x => [...x].length);
    const longest = Math.max(0, ...lengths);
    if (lengths.length < 2 || lengths.filter(n => n >= longest - TOLERANCE).length < 2) return MIN;
    return Math.max(MIN, longest - TOLERANCE);
  };
  return (text, onePara) => {
    text = text.normalize('NFC').replace(/\r\n?/g, '\n');
    text = text.replace(/[\ufb00-\ufb06]/g, (c) => LIGATURES[c]);
    text = text.replace(SPLIT, joinAccent);
    text = text.replace(/[\u200b\ufeff\u202d\u202e]/g, '')
      .replace(/[\u00a0\u1680\u2000-\u200a\u202f\u205f]/g, ' ');
    text = text.replace(/\u00ad(?![ \t]*\n)/g, '');
    const lines = text.split('\n').map(x => x.trim()), width = wrapWidth(lines);
    const paragraphs = [];
    let previous = null;  // (the line before, if the next one may join its paragraph)
    for (const line of lines) {
      if (!line) {
        if (!onePara && paragraphs.length && paragraphs[paragraphs.length - 1]) {
          paragraphs.push('');
        }
        if (!onePara) previous = null;
        continue;
      }
      if (previous !== null && (onePara || (!NEW_BLOCK.test(line) && !URL_END.test(previous)
          && ([...previous].length >= width || HYPHENATED.test(previous))))) {
        paragraphs[paragraphs.length - 1] = join(paragraphs[paragraphs.length - 1], line);
      } else paragraphs.push(line);
      previous = line;
    }
    const out = paragraphs.join('\n').trim().replace(/\u00ad/g, '');
    return out.replace(/(\S) {2,}(?=\S)/g, '$1 ');
  };
})();
</script>
"""
SCRIPT = _SCRIPT % {
    "ligatures": json.dumps(LIGATURES),
    "accents": json.dumps(ACCENTS),
    "min": MIN_WRAP_WIDTH,
    "tolerance": WRAP_TOLERANCE,
}
