"""Extract current Django messages and reject incomplete French catalogues."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from babel.messages.pofile import read_po
from babel.messages.checkers import python_format

ROOT = Path(__file__).resolve().parent.parent


def extract_messages(destination, root=ROOT):
    for directory in ("dashboard", "config"):
        for source in (root / directory).rglob("*"):
            if source.suffix not in (".py", ".html") or "__pycache__" in source.parts:
                continue
            target = destination / source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    (destination / "locale").mkdir()
    subprocess.run([
        sys.executable, "-m", "django", "makemessages", "--locale", "fr",
        "--no-location", "--no-obsolete", "--no-wrap",
    ], cwd=destination, check=True)
    return destination / "locale/fr/LC_MESSAGES/django.po"


def missing_translations(extracted, translated):
    errors = []
    for message in extracted:
        if not message.id:
            continue
        key = message.id[0] if isinstance(message.id, tuple) else message.id
        actual = translated.get(key, context=message.context)
        if actual is None:
            reason = "missing"
        elif actual.fuzzy:
            reason = "fuzzy/unconfirmed"
        elif not actual.string or isinstance(actual.string, (tuple, list)) and not all(actual.string):
            reason = "empty"
        else:
            actual.flags.update(message.flags)
            try:
                python_format(translated, actual)
            except Exception as error:
                errors.append(f"invalid placeholders: {key!r}: {error}")
            continue
        errors.append(f"{reason}: {key!r}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="Refresh .po message IDs; does not invent translations")
    options = parser.parse_args()
    catalogue = ROOT / "locale/fr/LC_MESSAGES/django.po"
    print("[translations] Extracting current marked Python/template UI messages.", flush=True)
    with tempfile.TemporaryDirectory(prefix="vi-translations-") as directory:
        extracted = extract_messages(Path(directory))
        if options.update:
            subprocess.run(["msgmerge", "--update", "--backup=none", "--no-fuzzy-matching", "--no-wrap", str(catalogue), str(extracted)], check=True)
            print("[translations] Updated source catalogue. Fill missing translations before testing.", flush=True)
        with extracted.open("rb") as source, catalogue.open("rb") as french:
            expected, actual = read_po(source), read_po(french)
        errors = missing_translations(expected, actual)
        for error in errors:
            print(f"[translations] ERROR {error}", file=sys.stderr)
        if errors:
            raise SystemExit(f"[translations] FAILED: {len(errors)} missing/fuzzy French messages. Edit locale/fr/LC_MESSAGES/django.po; rerun make run-local-dashboard.")
        subprocess.run(["msgfmt", "--check", "--check-format", "--output-file=/dev/null", str(catalogue)], check=True)
        print(f"[translations] PASS: all {len(expected)} current marked UI messages have non-fuzzy French translations; formats validated.", flush=True)


if __name__ == "__main__":
    main()
