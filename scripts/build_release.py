"""Build a deterministic release archive for manual installation."""

from __future__ import annotations

import argparse
import json
import re
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "household_tasks"
OUTPUT = ROOT / "dist" / "household_tasks.zip"
ARCHIVE_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _version() -> str:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text("utf-8"))
    version = str(manifest["version"])
    if SEMVER_PATTERN.fullmatch(version) is None:
        raise ValueError(f"Manifest version {version!r} is not valid SemVer 2.0.0")
    return version


def build(tag: str | None) -> Path:
    """Validate the tag and create a byte-reproducible zip archive."""
    version = _version()
    expected_tag = f"v{version}"
    if tag is not None and tag != expected_tag:
        raise ValueError(
            f"Release tag {tag!r} does not match expected tag {expected_tag!r}"
        )

    OUTPUT.parent.mkdir(exist_ok=True)
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(INTEGRATION.rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            relative = Path("household_tasks") / source.relative_to(INTEGRATION)
            info = ZipInfo(relative.as_posix(), ARCHIVE_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)

    digest = sha256(OUTPUT.read_bytes()).hexdigest()
    OUTPUT.with_suffix(".zip.sha256").write_text(
        f"{digest}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    return OUTPUT


def main() -> None:
    """Run the release builder."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    args = parser.parse_args()
    build(args.tag)


if __name__ == "__main__":
    main()
