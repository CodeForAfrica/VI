"""Check every declared runtime pin against packages installed in the shipping image."""
from importlib import metadata
from pathlib import Path
import re


def check_pins(requirements, installed_version=metadata.version):
    errors = []
    checked = 0
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        if not match:
            errors.append(f"Unrecognized/unpinned requirement: {line!r}; use an explicit package==version pin")
            continue
        package, expected = match.groups()
        checked += 1
        try:
            actual = installed_version(package)
        except metadata.PackageNotFoundError:
            errors.append(f"{package}: MISSING (required {expected}); rebuild the production image")
            continue
        if actual != expected:
            errors.append(f"{package}: installed {actual}, required {expected}; rebuild/check requirements and Docker cache")
    if not checked:
        errors.append("No runtime pins found; refusing to report dependency validation success")
    return checked, errors


def main():
    print("[dependencies] Comparing installed runtime packages with requirements.txt.", flush=True)
    count, errors = check_pins(Path("requirements.txt").read_text())
    for error in errors:
        print(f"[dependencies] ERROR: {error}", flush=True)
    if errors:
        raise SystemExit(f"[dependencies] FAILED: {len(errors)} dependency errors; no publication/deployment permitted")
    print(f"[dependencies] PASS: all {count} installed package versions match the declared pins.", flush=True)


if __name__ == "__main__":
    main()
