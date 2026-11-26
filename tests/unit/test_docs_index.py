"""The offline documentation index, built from small hand-written pages.

The pages copy the structure of TouchDesigner's offline help (research/PHASE3_KNOWLEDGE_RESULTS.md),
not its text. The real folder is covered by tests/integration/test_knowledge.py.
"""

import json
import stat

import pytest

from td_mcp import docs_index
from td_mcp.errors import DocsError


def page(name, body, *, categories=(), namespace=0, article_id=None, title=None, redirect=False):
    settings = {
        "wgNamespaceNumber": namespace,
        "wgPageName": name,
        "wgTitle": title or name.replace("_", " "),
        "wgCategories": list(categories),
        "wgIsRedirect": redirect,
        "wgArticleId": article_id or abs(hash(name)) % 100000,
    }
    config = json.dumps(settings, separators=(",", ":"))
    return (
        f"<!DOCTYPE html><html><head><script>RLCONF={config};</script></head><body>"
        '<div>navigationword</div><div id="mw-content-text" class="mw-body-content">'
        f'{body}</div><div class="printfooter">footerword</div></body></html>'
    )


def heading(text, level=2):
    return (
        f'<h{level}><span class="mw-headline">{text}</span>'
        f'<span class="mw-editsection">[<a>edit</a>]</span></h{level}>'
    )


FEEDBACK = page(
    "Feedback_TOP",
    heading("Summary")
    + '<p>The Feedback <span class="mw-lingo-term">TOP</span> keeps the previous frame.<br></p>'
    + '<div id="toc"><ul><li>contentsword</li></ul></div>'
    + heading("Parameters - Common Page")
    + "<p><span>Resolution</span> <code>resolution</code> - shared resolution text. ⊞</p>"
    + '<div class="catList"><p>catword</p></div>'
    + '<div class="mw-lingo-tooltip"><div class="mw-lingo-definition"><p>popupword</p></div></div>'
    + "<script>var hidden = 'scriptword';</script>",
    categories=["TOPs"],
)
BLUR = page(
    "Blur_TOP",
    heading("Summary") + "<p>The Blur TOP softens an image in a loop.</p>"
    + heading("Parameters - Common Page")
    + "<p><span>Resolution</span> <code>resolution</code> - shared resolution text. ⊞</p>",
    categories=["TOPs"],
)
NOISE_CLASS = page(
    "NoiseTOP_Class",
    "<p>This class describes a noise operator.</p>"
    + heading("Members") + "<p>ownmember is specific.</p>"
    + heading("TOP Class", 1) + heading("Members") + "<p>inheritedmember is shared.</p>",
    categories=["TOP Classes", "Python Reference"],
    title="NoiseTOP Class",
)
ARTICLE = page(
    "Feedback_Tricks",
    heading("Idea") + "<p>A feedback loop &amp; a blur.</p>"
    + heading("Detail", 3) + "<p>smaller words</p>",
)


@pytest.fixture
def docs(tmp_path):
    folder = tmp_path / "help"
    (folder / "Sub").mkdir(parents=True)
    (folder / "Feedback_TOP.htm").write_text("﻿" + FEEDBACK, encoding="utf-8")
    (folder / "Blur_TOP.htm").write_text(BLUR, encoding="utf-8")
    (folder / "NoiseTOP_Class.htm").write_text(NOISE_CLASS, encoding="utf-8")
    (folder / "Sub" / "Tricks.htm").write_text(ARTICLE, encoding="utf-8")
    return folder


@pytest.fixture
def find(docs, tmp_path):
    def run(query, **options):
        return docs_index.search(query, source=docs, target=tmp_path / "cache" / "i.sqlite",
                                 **options)

    return run


def all_text(result):
    return " ".join(hit["text"] + " " + hit["section"] for hit in result["results"])


def test_a_passage_carries_its_page_section_kind_and_url(find):
    (hit,) = find("softens")["results"]
    assert hit == {
        "page": "Blur_TOP",
        "title": "Blur TOP",
        "kind": "operator",
        "section": "Summary",
        "text": "The Blur TOP softens an image in a loop.",
        "all_words": True,
        "url": "https://docs.derivative.ca/Blur_TOP",
    }


@pytest.mark.parametrize(
    "hidden", ["navigationword", "footerword", "contentsword", "popupword", "scriptword",
               "catword", "edit", "inheritedmember"],
)
def test_page_furniture_and_inherited_members_are_not_indexed(find, hidden):
    assert find(hidden)["results"] == []


def test_a_class_keeps_its_own_members(find):
    result = find("ownmember")
    assert result["results"][0]["kind"] == "class"
    assert result["results"][0]["section"] == "Members"


def test_text_is_tidied(find):
    result = find("resolution", page="Feedback TOP")
    assert result["results"][0]["text"] == "Resolution resolution - shared resolution text."
    assert find("smaller")["results"][0]["section"] == "Idea > Detail"
    assert "loop & a blur" in all_text(find("tricks idea"))


def test_a_page_named_in_the_query_comes_first_even_without_every_word(find):
    results = find("feedback loop TOP")["results"]
    assert (results[0]["page"], results[0]["section"]) == ("Feedback_TOP", "Summary")
    assert results[0]["all_words"] is False
    assert {"Blur_TOP", "Feedback_Tricks"} <= {hit["page"] for hit in results}


def test_a_type_name_finds_the_operator_page(find):
    assert find("feedbackTOP")["results"][0]["page"] == "Feedback_TOP"


def test_identical_text_is_returned_once(find):
    texts = [hit["text"] for hit in find("shared resolution text", limit=20)["results"]]
    assert len(texts) == len(set(texts)) == 1


def test_passages_with_some_words_follow_those_with_all(find):
    results = find("softens frame")["results"]
    assert {hit["page"] for hit in results} == {"Blur_TOP", "Feedback_TOP"}
    assert not any(hit["all_words"] for hit in results)


def test_kind_and_page_filters(find):
    assert {h["kind"] for h in find("feedback", kind="article")["results"]} == {"article"}
    for spelled in ("Blur_TOP", "Blur TOP", "blurTOP", "https://docs.derivative.ca/Blur_TOP"):
        assert {h["page"] for h in find("loop", page=spelled)["results"]} == {"Blur_TOP"}


def test_an_unknown_page_suggests_real_ones(find):
    with pytest.raises(DocsError, match="Closest: .*Blur_TOP"):
        find("loop", page="Blurr_TOP")


@pytest.mark.parametrize("query", ['loop"', "NEAR(loop)", "loop AND OR NOT", "title:loop -x *"])
def test_query_syntax_is_treated_as_words(find, query):
    assert find(query)["results"]


def test_no_match_is_an_empty_result(find):
    result = find("xyzzyplugh")
    assert result["results"] == [] and result["next_offset"] is None


def test_paging(find):
    first = find("loop", limit=1)
    assert len(first["results"]) == 1 and first["next_offset"] == 1
    second = find("loop", limit=1, offset=1)
    assert second["results"] != first["results"] and second["next_offset"] is None
    assert find("loop", limit=500)["next_offset"] is None


@pytest.mark.parametrize(
    "query, options", [("  !! ", {}), ("loop", {"limit": 0}), ("loop", {"offset": -1})]
)
def test_bad_requests_are_refused(find, query, options):
    with pytest.raises(DocsError):
        find(query, **options)


def test_only_articles_are_indexed(docs, find):
    (docs / "Talk.htm").write_text(page("Talk", "<p>talkword</p>", namespace=1))
    (docs / "Old.htm").write_text(page("Old", "<p>redirectword</p>", redirect=True))
    (docs / "R.htm").write_text(page("Release_Notes/2025", "<p>releaseword</p>"))
    (docs / "junk.htm").write_text("<html>not a wiki page</html>")
    for word in ("talkword", "redirectword", "releaseword"):
        assert find(word)["results"] == []


def test_an_article_saved_twice_is_indexed_once_under_its_own_name(docs, find):
    twice = page("Canonical_Page", "<p>twinword</p>", article_id=7)
    (docs / "Alias.htm").write_text(
        page("Alias_Page", "<p>twinword old</p>", article_id=7), encoding="utf-8"
    )
    (docs / "Canonical_Page.htm").write_text(twice, encoding="utf-8")
    assert [h["page"] for h in find("twinword")["results"]] == ["Canonical_Page"]


def test_long_text_is_split_without_losing_words():
    text = "\n".join(["alpha " * 50, "beta " * 600, "gamma"])
    pieces = list(docs_index.chunks(text, size=500))
    assert all(len(piece) <= 500 for piece in pieces)
    assert " ".join(pieces).split() == text.split()


def test_the_index_is_reused_until_the_help_folder_changes(docs, tmp_path, monkeypatch):
    target = tmp_path / "cache" / "i.sqlite"
    builds = []
    real = docs_index._build
    monkeypatch.setattr(docs_index, "_build", lambda *a: builds.append(1) or real(*a))
    docs_index.ensure_index(docs, target)
    docs_index.ensure_index(docs, target)
    assert len(builds) == 1
    (docs / "New.htm").write_text(page("New_Page", "<p>freshword</p>"))
    assert docs_index.search("freshword", source=docs, target=target)["results"]
    assert len(builds) == 2
    assert sorted(p.name for p in target.parent.iterdir()) == ["i.sqlite"]
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_a_damaged_index_is_rebuilt(docs, tmp_path):
    target = tmp_path / "i.sqlite"
    target.write_bytes(b"not a database" * 100)
    assert docs_index.search("softens", source=docs, target=target)["results"]


def test_an_empty_help_folder_is_an_error_and_leaves_no_index(tmp_path):
    (tmp_path / "help").mkdir()
    target = tmp_path / "cache" / "i.sqlite"
    with pytest.raises(DocsError, match="No documentation pages"):
        docs_index.search("x", source=tmp_path / "help", target=target)
    assert not target.exists()


def test_an_unwritable_cache_is_explained(docs, tmp_path):
    (tmp_path / "file").write_text("")
    with pytest.raises(DocsError, match="TD_MCP_CACHE_DIR"):
        docs_index.ensure_index(docs, tmp_path / "file" / "i.sqlite")


def test_locations_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("TD_MCP_DOCS_DIR", raising=False)
    monkeypatch.setenv("TD_APP", str(tmp_path / "Nope.app"))
    with pytest.raises(DocsError, match="TD_MCP_DOCS_DIR"):
        docs_index.docs_dir()
    monkeypatch.setenv("TD_MCP_DOCS_DIR", str(tmp_path))
    assert docs_index.docs_dir() == tmp_path
    monkeypatch.setenv("TD_MCP_CACHE_DIR", str(tmp_path / "c"))
    assert docs_index.cache_path() == tmp_path / "c" / "docs-index.sqlite"
