"""Fetch the public-domain prose the benchmark measures against.

Nothing is committed: the texts are downloaded on first run and cached under
``benchmarks/corpus/``, which ``.gitignore`` excludes.  That keeps the
repository free of redistributed literary text (see ``TEST_DATA.md``) and keeps
the benchmark honest about where its input came from.

Both works are long out of copyright.  The Japanese source matters most here,
because the whole claim under test is about text that has no spaces between
words.
"""

from __future__ import annotations

import io
import re
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "corpus"

USER_AGENT = "docx-redline-ja-benchmark/0.1 (+https://github.com/akchan/docx-redline-ja)"


@dataclass(frozen=True)
class Source:
    language: str
    title: str
    author: str
    url: str
    attribution: str


SOURCES = {
    "ja": Source(
        language="ja",
        title="吾輩は猫である",
        author="夏目漱石",
        url="https://www.aozora.gr.jp/cards/000148/files/789_ruby_5639.zip",
        attribution="Aozora Bunko card 789 (https://www.aozora.gr.jp/cards/000148/card789.html)",
    ),
    "en": Source(
        language="en",
        title="Alice's Adventures in Wonderland",
        author="Lewis Carroll",
        url="https://www.gutenberg.org/cache/epub/11/pg11.txt",
        attribution="Project Gutenberg ebook 11 (https://www.gutenberg.org/ebooks/11)",
    ),
}


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _strip_aozora_markup(text: str) -> str:
    """Remove ruby, ruby-start marks and editor annotations from an Aozora text."""
    text = re.sub(r"《[^》]*》", "", text)          # ruby readings
    text = text.replace("｜", "")                    # explicit ruby start
    text = re.sub(r"［＃[^］]*］", "", text)        # editor annotations
    return text


def _aozora_body(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        name = next(item for item in archive.namelist() if item.endswith(".txt"))
        text = archive.read(name).decode("shift_jis", errors="replace")
    # The header ends at the first run of dashes; the trailing licence block
    # starts at the line of "底本" (source edition) notes.
    parts = re.split(r"-{20,}", text)
    body = parts[2] if len(parts) >= 3 else text
    body = body.split("底本：")[0]
    return _strip_aozora_markup(body)


def _gutenberg_body(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    start = re.search(r"\*\*\* START OF THE PROJECT GUTENBERG EBOOK.*?\*\*\*", text, re.S)
    end = re.search(r"\*\*\* END OF THE PROJECT GUTENBERG EBOOK.*?\*\*\*", text, re.S)
    if start:
        text = text[start.end():]
    if end and start:
        text = text[: end.start() - start.end()]
    # Gutenberg marks italics with underscores in the plain-text edition.  They
    # are an encoding artefact, not the author's prose, and a tool that reads a
    # DOCX would never see them.
    text = re.sub(r"_([^_\n]+)_", r"\1", text)
    return text


def raw_text(language: str, *, refresh: bool = False) -> str:
    """Return the cached body text for a language, downloading it if needed."""
    source = SOURCES[language]
    cached = CACHE / f"{language}.txt"
    if cached.exists() and not refresh:
        return cached.read_text(encoding="utf-8")

    print(f"downloading {source.title} from {source.url}", file=sys.stderr)
    raw = _download(source.url)
    body = _aozora_body(raw) if language == "ja" else _gutenberg_body(raw)
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(body, encoding="utf-8")
    return body


def _sentences(text: str, language: str) -> list[str]:
    """Split into sentences, keeping the terminator attached."""
    if language == "ja":
        return [part for part in re.split(r"(?<=[。！？])", text) if part.strip()]
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def paragraphs(
    language: str,
    *,
    count: int = 50,
    target_chars: int = 300,
    refresh: bool = False,
) -> list[str]:
    """``count`` paragraphs of roughly ``target_chars`` each, built from sentences.

    The source line breaks are not usable directly: Aozora wraps prose at the
    printed book's width and puts a whole chapter in one block, while Gutenberg
    opens with a table of contents.  Re-grouping whole sentences into even
    paragraphs gives every arm of the benchmark the same, ordinary-looking
    document, and keeps each ``old_text`` inside one paragraph as both skills
    require.
    """
    text = raw_text(language, refresh=refresh)
    blocks = re.split(r"\n\s*\n", text)
    flat: list[str] = []
    for block in blocks:
        joined = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if language == "ja":
            joined = joined.replace(" ", "").replace("\u3000", "")
        if len(joined) < 80 or joined.count("CHAPTER") > 2:
            continue  # chapter headings and the English table of contents
        flat.extend(_sentences(joined, language))

    out: list[str] = []
    current: list[str] = []
    size = 0
    joiner = "" if language == "ja" else " "
    for sentence in flat:
        current.append(sentence)
        size += len(sentence)
        if size >= target_chars:
            out.append(joiner.join(current))
            current, size = [], 0
            if len(out) >= count:
                break
    if len(out) < count:
        raise RuntimeError(f"only built {len(out)} paragraphs for {language}, wanted {count}")
    return out


def attribution_lines() -> list[str]:
    return [
        f"{source.author}, {source.title} — {source.attribution}"
        for source in SOURCES.values()
    ]


if __name__ == "__main__":
    for language in SOURCES:
        found = paragraphs(language)
        total = sum(len(paragraph) for paragraph in found)
        print(f"{language}: {len(found)} paragraphs, {total} characters")
        print(f"  first: {found[0][:60]}…")
