"""Keep the language switcher line identical across every README.

Six translations means every new language edits seven files, and a
hand-maintained switcher is where that goes wrong: one file quietly ends up
listing five languages, or links to a sibling with the wrong relative depth
(the English README sits at the repo root, the translations one level down in
docs/i18n/). Both failures look fine in review and break for the reader.

So the switcher is generated. Run this after adding a language; it rewrites
the line between the two markers in every file and verifies each target
exists.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (file, label). Order is the order readers see.
LANGS = [
    ("README.md", "English"),
    ("docs/i18n/README.zh-CN.md", "简体中文"),
    ("docs/i18n/README.ja.md", "日本語"),
    ("docs/i18n/README.ko.md", "한국어"),
    ("docs/i18n/README.fr.md", "Français"),
    ("docs/i18n/README.es.md", "Español"),
    ("docs/i18n/README.de.md", "Deutsch"),
    ("docs/i18n/README.pt-BR.md", "Português"),
]

BEGIN = "<!-- langs -->"
END = "<!-- /langs -->"


def switcher_for(current: str) -> str:
    parts = []
    here = (ROOT / current).parent
    for path, label in LANGS:
        if path == current:
            parts.append(f"**{label}**")
        else:
            rel = pathlib.os.path.relpath(ROOT / path, here)
            parts.append(f"[{label}]({rel})")
    return " · ".join(parts)


def main() -> int:
    missing = [p for p, _ in LANGS if not (ROOT / p).exists()]
    if missing:
        print("missing translations:", ", ".join(missing))
        return 1

    changed = 0
    for path, _ in LANGS:
        f = ROOT / path
        text = f.read_text()
        if BEGIN not in text or END not in text:
            print(f"{path}: no {BEGIN} … {END} markers — skipped")
            continue
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        new = f"{head}{BEGIN}\n{switcher_for(path)}\n{END}{tail}"
        if new != text:
            f.write_text(new)
            changed += 1
    print(f"{len(LANGS)} languages, {changed} file(s) updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
