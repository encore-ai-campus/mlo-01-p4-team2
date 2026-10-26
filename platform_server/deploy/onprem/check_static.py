"""Verify that collected assets match the source used to render versioned URLs."""
import hashlib
import os
from pathlib import Path
import posixpath
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "frontend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.conf import settings
from django.contrib.staticfiles import finders


def main():
    static_root = Path(settings.STATIC_ROOT)
    sources = {}
    for finder in finders.get_finders():
        for path, storage in finder.list(['CVS', '.*', '*~']):
            name = posixpath.join(getattr(storage, 'prefix', '') or '', path.replace('\\', '/'))
            sources.setdefault(name, Path(storage.path(path)))
    if not sources:
        raise SystemExit('No source static files found')
    for name, source in sources.items():
        target = static_root / name
        if not target.is_file() or hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
            raise SystemExit(f"Missing or changed collected static file: {name}")
    sections = {name.split('/', 1)[0] for name in sources}
    for target in static_root.rglob("*"):
        if not target.is_file():
            continue
        name = target.relative_to(static_root).as_posix()
        if name not in sources:
            raise SystemExit(f"Stale collected static file: {name}")
    missing = {"campus", "accounts", "learning"} - sections
    if missing:
        raise SystemExit(f"Missing collected asset sections: {', '.join(sorted(missing))}")
    print(f"Verified {len(sources)} collected static files against source")


if __name__ == "__main__":
    main()
