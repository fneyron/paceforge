"""The stylesheet minified for the image (Dockerfile): comments and whitespace out, nothing else.

app/static/css/interface.css stays readable in git, with its comments; the image serves it at the same URL without
them (≈ 140 KB → ≈ 107 KB, 30 → 20 KB gzipped). Only what CSS ignores goes: comments, a run of whitespace (one space
kept where a space separates two things: a descendant combinator, the words of a value, calc()'s operators), the
whitespace next to { } ; , and after a declaration's colon, the last semicolon of a block. Strings are copied as they
are, and so is a custom property's value (« --x: a, b »: the browser keeps its text, and scripts read it). No rule is
merged, reordered or rewritten: a browser reads the same rules from both (tests/browser/css-minified.cjs).

    python -m app.css_minify app/static/css/interface.css    minifies the file in place
"""
import sys
from pathlib import Path

# whitespace around these never matters; a colon only before what follows it (« a :hover » is not « a:hover »)
_TIGHT = frozenset("{};,")
_SPACE = " \t\r\n\f"


def _string_end(css: str, i: int) -> int:
    """The index just past the string that starts at i (its quote), escapes included."""
    quote, j, n = css[i], i + 1, len(css)
    while j < n and css[j] != quote:
        j += 2 if css[j] == "\\" else 1
    return j + 1


def _custom_value_end(css: str, i: int) -> int:
    """The index of the « ; » or « } » that ends the custom property value starting at i (outside any bracket)."""
    depth, n = 0, len(css)
    while i < n:
        c = css[i]
        if c in "\"'":
            i = _string_end(css, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}" and depth:
            depth -= 1
        elif c in ";}" and not depth:
            return i
        i += 1
    return n


def minify(css: str) -> str:
    out: list[str] = []
    i, n = 0, len(css)
    pending_space = False  # whitespace seen since the last character written
    declaration = 0  # where the current declaration (or selector) starts in `out`

    def last() -> str:
        return out[-1][-1] if out and out[-1] else ""

    while i < n:
        c = css[i]
        if c == "/" and css.startswith("/*", i):  # a comment: gone (it is not whitespace to CSS either)
            end = css.find("*/", i + 2)
            after = css[end + 2:end + 3] if end >= 0 else ""
            i = n if end < 0 else end + 2
            # glued to two tokens (« .a/**/.b »), it keeps them apart without making a combinator: kept, empty
            if (not pending_space and out and last() not in _TIGHT and after and after not in _SPACE
                    and after not in _TIGHT and not css.startswith("/*", i)):
                out.append("/**/")
            continue
        if c in _SPACE:
            pending_space = True
            i += 1
            continue
        if pending_space and out and last() not in _TIGHT and last() != ":" and c not in _TIGHT:
            out.append(" ")
        pending_space = False
        if c in "\"'":  # a string, escapes included, as it is
            end = _string_end(css, i)
            out.append(css[i:end])
            i = end
            continue
        if c == "\\":  # an escaped character (« .w-1\/2 »): it and the one it escapes, as they are
            out.append(css[i:i + 2])
            i += 2
            continue
        if c == ":" and "".join(out[declaration:]).lstrip().startswith("--"):
            # a custom property: its value as written, without the whitespace that starts or ends it (the browser
            # drops it too); an empty one keeps a space
            end = _custom_value_end(css, i + 1)
            value = css[i + 1:end].strip(_SPACE)
            out.append(":" + (value or " "))
            i = end
            continue
        if c == "}" and out and out[-1] == ";":  # the block's last semicolon
            out.pop()
        out.append(c)
        if c in "{};":
            declaration = len(out)
        i += 1
    return "".join(out).strip()


def main(paths: list[str]) -> int:
    for name in paths:
        path = Path(name)
        source = path.read_text(encoding="utf-8")
        path.write_text(minify(source) + "\n", encoding="utf-8")
        print(f"{name}: {len(source.encode())} → {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
