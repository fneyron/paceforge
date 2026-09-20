"""The Tailwind CSS is prebuilt and committed (scripts/build-css.sh). A class
used by a template but absent from the built file renders as nothing in
production — silently. This guards the classes most likely to be missed:
arbitrary values, responsive table cells, opacity variants."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app/static/css/tailwind.css").read_text(encoding="utf-8")
PATTERNS = [
    r"(?:sm|md|lg):(?:table-cell|hidden|flex|grid-cols-\d|block)",
    r"(?:text|w|h|min-w|max-w|leading)-\[[0-9.]+(?:px|cm|rem|ch)\]",
    r"(?:bg|text|border)-[a-z]+-\d{2,3}/\d{2}",
    r"group-hover:[a-z0-9-]+",
    r"focus-within:[a-z0-9-]+",
    r"disabled:[a-z0-9-]+",
    r"peer-checked:[a-z0-9-]+",
]


def _css_has(cls: str) -> bool:
    # Tailwind escapes [ ] : / . % in selectors: text-[10px] → .text-\[10px\]
    escaped = re.sub(r"([\[\]:/.%])", r"\\\1", cls)
    return re.search(r"\." + re.escape(escaped) + r"(?=[\s{:,>~+)\\])", CSS) is not None


def test_template_classes_exist_in_built_css():
    missing = set()
    for tpl in (ROOT / "app/templates").rglob("*.html"):
        text = tpl.read_text(encoding="utf-8")
        for pat in PATTERNS:
            for m in re.finditer(pat, text):
                cls = m.group(0)
                if not _css_has(cls):
                    missing.add(f"{tpl.relative_to(ROOT)}: {cls}")
    assert not missing, "Rebuild the CSS (scripts/build-css.sh). Missing:\n" + "\n".join(sorted(missing))
