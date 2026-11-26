"""Search the copy of docs.derivative.ca that ships inside the installed TouchDesigner.

The index is built on first use from the offline help folder and cached in a local SQLite
file (full-text search, no network). It is rebuilt whenever that folder changes, for example
after a TouchDesigner update. TouchDesigner does not need to be running.

Page layout facts this parser relies on: research/PHASE3_KNOWLEDGE_RESULTS.md.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote

from td_mcp.errors import DocsError

# Bump when the parsing or table layout changes, so cached indexes are rebuilt.
INDEX_VERSION = 1
DEFAULT_APP = "/Applications/TouchDesigner.app"
_DOCS_IN_APP = "Contents/Resources/tfs/Samples/Learn/OfflineHelp/https.docs.derivative.ca"
_BASE_URL = "https://docs.derivative.ca/"
MAX_CHUNK_CHARS = 1200
MAX_RESULTS = 20
# Without a page filter, one long page must not fill every result slot.
_MAX_HITS_PER_PAGE = 2
# Rows fetched before identical text and per-page limits are applied.
_CANDIDATES = 400
_FAMILIES = ("TOP", "CHOP", "SOP", "DAT", "COMP", "MAT", "POP")
_TYPE_TOKEN = re.compile(r"([A-Za-z0-9]+?)(TOP|CHOP|SOP|DAT|COMP|MAT|POP)")
_STOPWORDS = frozenset(
    {"a", "an", "and", "are", "can", "do", "does", "for", "from", "how", "i", "in", "is", "it",
     "of", "on", "or", "the", "to", "what", "when", "with"}
)
_BLOCK_TAGS = frozenset(
    {"blockquote", "br", "dd", "div", "dl", "dt", "li", "ol", "p", "pre", "table", "tr", "ul"}
)
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
     "track", "wbr"}
)
_HEADINGS = ("h1", "h2", "h3")
# Glossary pop-up text and the lists of every operator in a family, repeated across pages.
_SKIPPED_CLASSES = frozenset(
    ("mw-editsection", "mw-lingo-tooltip", "mw-lingo-definition", "catList")
)


def docs_dir() -> Path:
    """The offline help folder: TD_MCP_DOCS_DIR, else inside TD_APP's TouchDesigner."""
    override = os.environ.get("TD_MCP_DOCS_DIR", "").strip()
    if override:
        found = Path(override).expanduser()
    else:
        found = Path(os.environ.get("TD_APP", "").strip() or DEFAULT_APP) / _DOCS_IN_APP
    if not found.is_dir():
        raise DocsError(
            f"No TouchDesigner offline help at {found}. Set TD_APP to your TouchDesigner.app, "
            "or TD_MCP_DOCS_DIR to the folder holding the offline help's .htm pages."
        )
    return found


def cache_path() -> Path:
    folder = os.environ.get("TD_MCP_CACHE_DIR", "").strip() or "~/.td-mcp"
    return Path(folder).expanduser() / "docs-index.sqlite"


@dataclass(frozen=True)
class Page:
    name: str  # the wiki page name, for example "Noise_TOP"
    title: str
    kind: str  # "operator" | "class" | "article"
    article_id: str
    sections: list[tuple[str, str]]  # (heading, text)

    @property
    def url(self) -> str:
        return _BASE_URL + quote(self.name, safe="/:_()-.,")


class _ContentParser(HTMLParser):
    """Collect the article body as (heading, text) sections.

    Left out: the table of contents, "[edit]" links, scripts, glossary pop-ups, operator
    navigation lists, and, on class pages, the members inherited from parent classes
    (repeated on hundreds of pages; each parent has its own).
    """

    def __init__(self, title: str, kind: str) -> None:
        super().__init__(convert_charrefs=True)
        self._title = title
        self._kind = kind
        self._stack: list[tuple[str, bool]] = []  # (tag, skipped)
        self._headings: list[str | None] = [None, None, None]
        self._heading_text: list[str] | None = None
        self._text: list[str] = []
        self._heading = ""
        self.sections: list[tuple[str, str]] = []
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        if tag in _BLOCK_TAGS:
            self._text.append("\n")
        if tag in _VOID_TAGS:
            return
        classes = (dict(attrs).get("class") or "").split()
        skipped = (
            tag in ("script", "style")
            or dict(attrs).get("id") == "toc"
            or not _SKIPPED_CLASSES.isdisjoint(classes)
        )
        self._stack.append((tag, skipped or self._skipping()))
        if tag in _HEADINGS and not self._skipping():
            self._heading_text = []

    def handle_endtag(self, tag: str) -> None:
        if self.done or tag in _VOID_TAGS:
            return
        if tag in _HEADINGS and self._heading_text is not None:
            self._close_heading(int(tag[1]), _squash("".join(self._heading_text)))
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break
        if tag in _BLOCK_TAGS:
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if self.done or self._skipping():
            return
        if self._heading_text is not None:
            self._heading_text.append(data)
        else:
            self._text.append(data)

    def _skipping(self) -> bool:
        return bool(self._stack) and self._stack[-1][1]

    def _close_heading(self, level: int, text: str) -> None:
        self._heading_text = None
        self.flush()
        inherited = (
            self._kind == "class"
            and level == 1
            and text.endswith(" Class")
            and _key(text) != _key(self._title)
        )
        if inherited:
            self.done = True
            return
        self._headings[level - 1] = text
        self._headings[level:] = [None] * (len(self._headings) - level)
        self._heading = " > ".join(h for h in self._headings if h)

    def flush(self) -> None:
        text = _tidy("".join(self._text))
        self._text = []
        if text:
            self.sections.append((self._heading, text))


def _squash(text: str) -> str:
    return " ".join(text.split())


def _tidy(text: str) -> str:
    lines = (_squash(line.replace("⊞", "")) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _key(name: str) -> str:
    """Compare page names loosely: "Noise_TOP", "Noise TOP" and "noiseTOP" are one key."""
    return re.sub(r"[ _]", "", name).lower()


def _setting(source: str, name: str) -> Any:
    """A value from the page's embedded MediaWiki settings (JSON), or None."""
    match = re.search(rf'"{name}":("(?:[^"\\]|\\.)*"|\[[^\]]*\]|-?\d+|true|false)', source)
    if match is None:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def _kind(title: str, categories: list[str]) -> str:
    for family in _FAMILIES:
        if family + "s" in categories and title.endswith(" " + family):
            return "operator"
    if "Python Reference" in categories or title.endswith((" Class", " Module")):
        return "class"
    return "article"


def parse_page(source: str) -> Page | None:
    """One help file as a Page, or None when it is not an article worth indexing."""
    if _setting(source, "wgNamespaceNumber") != 0 or _setting(source, "wgIsRedirect"):
        return None
    name = _setting(source, "wgPageName")
    title = _setting(source, "wgTitle")
    start = source.find('id="mw-content-text"')
    if not name or not title or start < 0 or name.startswith("Release_Notes"):
        return None
    end = source.find('class="printfooter"', start)
    # Back up to the "<" of each marker's tag, so the parser gets whole tags.
    body = source[source.rfind("<", 0, start) : source.rfind("<", 0, end) if end > 0 else None]
    kind = _kind(title, _setting(source, "wgCategories") or [])
    parser = _ContentParser(title, kind)
    parser.feed(body)
    parser.close()
    parser.flush()
    if not parser.sections:
        return None
    article_id = str(_setting(source, "wgArticleId") or name)
    return Page(name, title, kind, article_id, parser.sections)


def chunks(text: str, size: int = MAX_CHUNK_CHARS) -> Iterator[str]:
    """Split at line breaks into pieces of at most `size` characters."""
    piece = ""
    for line in text.split("\n"):
        while len(line) > size:
            cut = line.rfind(" ", 0, size)
            cut = cut if cut > 0 else size
            if piece:
                yield piece
                piece = ""
            yield line[:cut].strip()
            line = line[cut:].strip()
        if piece and len(piece) + 1 + len(line) > size:
            yield piece
            piece = ""
        piece = piece + "\n" + line if piece else line
    if piece:
        yield piece


def _fingerprint(source: Path, files: list[Path]) -> str:
    digest = hashlib.sha256(f"{INDEX_VERSION}\0{source}".encode())
    for path in files:
        info = path.stat()
        digest.update(f"\0{path.relative_to(source)}\0{info.st_size}\0{info.st_mtime_ns}".encode())
    return digest.hexdigest()


def _cached_fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        with contextlib.closing(sqlite3.connect(path)) as db:
            row = db.execute("SELECT value FROM meta WHERE key = 'fingerprint'").fetchone()
    except sqlite3.DatabaseError:
        return None  # damaged or not ours: rebuild
    return row[0] if row else None


def _build(target: Path, files: list[Path], fingerprint: str) -> None:
    pages: dict[str, Page] = {}
    for path in files:
        page = parse_page(path.read_text(encoding="utf-8-sig", errors="replace"))
        if page is None:
            continue
        # A renamed article is saved under both names; keep the file named after the page.
        if page.article_id not in pages or path.stem == page.name:
            pages[page.article_id] = page
    if not pages:
        raise DocsError(
            f"No documentation pages found in {files[0].parent if files else 'the help folder'}. "
            "Check TD_APP or TD_MCP_DOCS_DIR."
        )
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".docs-index-", suffix=".sqlite", dir=target.parent)
    os.close(handle)
    try:
        with contextlib.closing(sqlite3.connect(temp)) as db:
            db.executescript(
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                "CREATE TABLE pages (id INTEGER PRIMARY KEY, name TEXT, title TEXT, kind TEXT,"
                " url TEXT, name_key TEXT, title_key TEXT);"
                "CREATE VIRTUAL TABLE chunks USING fts5(title, heading, body,"
                " page_id UNINDEXED, tokenize = 'porter unicode61');"
            )
            for number, page in enumerate(sorted(pages.values(), key=lambda p: p.name), start=1):
                db.execute(
                    "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (number, page.name, page.title, page.kind, page.url,
                     _key(page.name), _key(page.title)),
                )
                db.executemany(
                    "INSERT INTO chunks (title, heading, body, page_id) VALUES (?, ?, ?, ?)",
                    [
                        (page.title, heading, piece, number)
                        for heading, text in page.sections
                        for piece in chunks(text)
                    ],
                )
            db.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fingerprint,))
            db.commit()
        os.replace(temp, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


_build_lock = threading.Lock()


def ensure_index(source: Path | None = None, target: Path | None = None) -> Path:
    """The path of an index matching the help folder, building it first if needed."""
    source = source or docs_dir()
    target = target or cache_path()
    try:
        files = sorted(source.rglob("*.htm"))
        fingerprint = _fingerprint(source, files)
        with _build_lock:
            if _cached_fingerprint(target) != fingerprint:
                _build(target, files, fingerprint)
    except (OSError, sqlite3.Error) as exc:
        raise DocsError(
            f"Cannot build the documentation index at {target} from {source}: {exc}. "
            "TD_MCP_CACHE_DIR moves the index."
        ) from exc
    return target


def _words(query: str) -> list[str]:
    words = re.findall(r"\w+", query)
    return [w for w in words if w.lower() not in _STOPWORDS] or words


def _term(word: str) -> str:
    """One word as a quoted FTS5 term, so no input is read as query syntax."""
    typed = _TYPE_TOKEN.fullmatch(word)
    if typed:
        # "noiseTOP" is written "Noise TOP" in operator pages.
        return f'("{word}" OR "{typed.group(1)} {typed.group(2)}")'
    return f'"{word}"'


def _named_pages(db: sqlite3.Connection, words: list[str]) -> list[int]:
    """Reference pages whose whole title is in the query: "feedback loop TOP" names the
    "Feedback TOP" page even though that page never says "loop"."""
    present = set()
    for word in words:
        typed = _TYPE_TOKEN.fullmatch(word)
        present.update([word.lower(), *(g.lower() for g in typed.groups())] if typed else
                       [word.lower()])
    found = []
    for number, title in db.execute("SELECT id, title FROM pages WHERE kind != 'article'"):
        title_words = {w.lower() for w in re.findall(r"\w+", title)}
        if len(title_words) > 1 and title_words <= present:
            found.append(number)
    return found


def _page_ids(db: sqlite3.Connection, page: str) -> list[int]:
    wanted = _key(page.removeprefix(_BASE_URL))
    rows = db.execute(
        "SELECT id FROM pages WHERE name_key = ? OR title_key = ?", (wanted, wanted)
    ).fetchall()
    if rows:
        return [row[0] for row in rows]
    names = {row[0]: row[1] for row in db.execute("SELECT name_key, name FROM pages")}
    close = [names[k] for k in difflib.get_close_matches(wanted, list(names), n=5, cutoff=0.6)]
    hint = f" Closest: {', '.join(close)}." if close else ""
    raise DocsError(f"No documentation page named {page!r}.{hint}")


def _within(ids: list[int]) -> str:
    # Page numbers come from this index, never from the caller.
    return f" AND pages.id IN ({','.join(str(int(i)) for i in ids)})"


def _ranked(
    db: sqlite3.Connection, terms: list[str], named: list[int], filters: str, values: list[str]
) -> Iterator[tuple[bool, tuple[str, str, str, str, str, str]]]:
    """(has every word, row), best first: pages the query names, then passages with every
    word, then passages with any word. Rows can repeat between tiers."""
    every = " AND ".join(terms)
    some = " OR ".join(terms)
    # (match, has every word, page restriction, order before relevance)
    tiers = [(every, True, "", ""), (some, False, "", "")]
    if named:
        # A named page that lacks some word is best introduced by its summary.
        tiers = [
            (every, True, _within(named), ""),
            (some, False, _within(named), "chunks.heading != 'Summary', "),
            *tiers,
        ]
    for match, complete, extra, first in tiers:
        sql = (
            "SELECT pages.title, pages.name, pages.kind, pages.url, chunks.heading, chunks.body"
            " FROM chunks JOIN pages ON pages.id = chunks.page_id"
            f" WHERE chunks MATCH ?{filters}{extra}"
            # bm25 is negative, best first. Title and heading matches outweigh body matches,
            # and reference pages outrank articles of similar relevance.
            f" ORDER BY {first}bm25(chunks, 10.0, 4.0, 1.0)"
            " * (CASE pages.kind WHEN 'article' THEN 1.0 ELSE 1.3 END), chunks.rowid"
            f" LIMIT {_CANDIDATES}"
        )
        for row in db.execute(sql, [match, *values]):
            yield complete, row


def search(
    query: str,
    kind: str | None = None,
    page: str | None = None,
    offset: int = 0,
    limit: int = 5,
    source: Path | None = None,
    target: Path | None = None,
) -> dict[str, Any]:
    """Best-matching documentation passages. Raises DocsError."""
    if offset < 0 or limit <= 0:
        raise DocsError("offset must be >= 0 and limit must be > 0")
    limit = min(limit, MAX_RESULTS)
    words = _words(query)
    if not words:
        raise DocsError("query must contain at least one word or number")
    end = offset + limit
    path = ensure_index(source, target)
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    per_page: dict[str, int] = {}
    with contextlib.closing(sqlite3.connect(path)) as db:
        filters, values = "", []
        if kind:
            filters += " AND pages.kind = ?"
            values.append(kind)
        if page:
            filters += _within(_page_ids(db, page))
        named = [] if page else _named_pages(db, words)
        terms = [_term(w) for w in words]
        for complete, row in _ranked(db, terms, named, filters, values):
            title, name, found_kind, url, heading, body = row
            # Shared sections (such as every TOP's Common page) repeat word for word.
            if body in seen or (not page and per_page.get(name, 0) >= _MAX_HITS_PER_PAGE):
                continue
            seen.add(body)
            per_page[name] = per_page.get(name, 0) + 1
            hits.append(
                {"page": name, "title": title, "kind": found_kind, "section": heading,
                 "text": body, "all_words": complete, "url": url}
            )
            if len(hits) > end:  # one extra shows whether there is a next page
                break
    return {
        "query": query,
        "offset": offset,
        "next_offset": end if len(hits) > end else None,
        "results": hits[offset:end],
    }
