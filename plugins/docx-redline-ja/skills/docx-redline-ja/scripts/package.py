"""Reading a .docx package into memory and writing it back.

Only ``word/document.xml`` is parsed and replaced.  Every other entry goes back
with its original bytes, in its original order and with its original
timestamp, and ``[Content_Types].xml`` always comes first, where readers
expect it.  Nothing is extracted to disk.

Reading refuses what a Word package never contains: absolute or ``..`` entry
names, names that differ only in case, and sizes far beyond any manuscript.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lxml import etree as ET

CONTENT_TYPES = "[Content_Types].xml"
DOCUMENT = "word/document.xml"

#: Limits for an untrusted package.  A real manuscript is far below them; a zip
#: bomb is far above.  Sizes are the uncompressed sizes the zip declares, which
#: ``zipfile`` enforces while reading.
MAX_ENTRIES = 10_000
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_DOCUMENT_BYTES = 256 * 1024**2


class PackageError(Exception):
    """The file is not a .docx this tool can safely open."""


@dataclass
class Package:
    """The entries of a package, in order, and the parsed ``word/document.xml``.

    Each entry is ``(name, date_time, data)``.  Directory entries are dropped.
    """

    entries: list[tuple[str, tuple, bytes]]
    tree: ET._ElementTree

    @property
    def root(self) -> ET._Element:
        return self.tree.getroot()

    def names(self) -> list[str]:
        return [name for name, _, _ in self.entries]


def _part_name(info: zipfile.ZipInfo) -> str | None:
    """The entry's normalized name, ``None`` for a directory entry, or raise."""
    name = info.filename
    if name.endswith("/"):
        return None
    path = PurePosixPath(name.replace("\\", "/"))
    # ":" never appears in an OPC part name; on Windows "C:evil.xml" would be a
    # drive-relative path for anyone who later extracts the output.
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in name:
        raise PackageError(f"unsafe entry name in package: {name!r}")
    return str(path)


def read_package(path: Path) -> Package:
    """Read every entry of ``path`` and parse its ``word/document.xml``."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageError(f"{path} is not a readable .docx (zip) package: {exc}") from exc

    entries: list[tuple[str, tuple, bytes]] = []
    seen: set[str] = set()
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise PackageError(f"package has {len(infos)} entries (limit {MAX_ENTRIES})")
        total = sum(info.file_size for info in infos)
        if total > MAX_TOTAL_BYTES:
            raise PackageError(f"package expands to {total} bytes (limit {MAX_TOTAL_BYTES})")
        for info in infos:
            name = _part_name(info)
            if name is None:
                continue
            if name.casefold() in seen:
                raise PackageError(f"duplicate entry in package: {info.filename!r}")
            seen.add(name.casefold())
            if name == DOCUMENT and info.file_size > MAX_DOCUMENT_BYTES:
                raise PackageError(
                    f"{DOCUMENT} is {info.file_size} bytes (limit {MAX_DOCUMENT_BYTES})"
                )
            try:
                data = archive.read(info)
            except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
                raise PackageError(f"corrupt entry {info.filename!r}: {exc}") from exc
            entries.append((name, info.date_time, data))

    document = next((data for name, _, data in entries if name == DOCUMENT), None)
    if document is None:
        raise PackageError(f"{DOCUMENT} not found in the package")
    parser = ET.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = ET.fromstring(document, parser)
    except ET.XMLSyntaxError as exc:
        raise PackageError(f"{DOCUMENT} is not well-formed XML: {exc}") from exc
    return Package(entries, root.getroottree())


def package_bytes(package: Package) -> bytes:
    """The package as a zip, with the current ``word/document.xml`` in it."""
    document = ET.tostring(package.tree, xml_declaration=True, encoding="UTF-8", standalone=True)
    entries = sorted(package.entries, key=lambda entry: entry[0] != CONTENT_TYPES)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, date_time, data in entries:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, document if name == DOCUMENT else data)
    return buffer.getvalue()


def entry_names(data: bytes) -> list[str]:
    """Sorted entry names of the zip held in ``data``."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return sorted(archive.namelist())


def write_atomically(data: bytes, path: Path) -> None:
    """Write ``path`` whole or not at all.

    The data goes to a new, uniquely named file in the same directory, which
    then replaces ``path``.  A fixed staging name could be one of the inputs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(staging, 0o666 & ~mask)  # mkstemp creates the file as 0600
        os.replace(staging, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(staging)
        raise
