"""Tests for litkit.refresh: DOI-only citation updates."""

import datetime
import os

import pytest

from conftest import make_paper
from litkit import enrich, index, refresh

TODAY = datetime.date(2026, 9, 16)


@pytest.fixture
def library(tmp_path):
    root = str(tmp_path / "Lit")
    paths = index.ensure_layout(root)
    make_paper(os.path.join(paths["pdfs"], "A_2016_X.pdf"), "T", "A", 2016, "10.1/a", "abs")
    make_paper(os.path.join(paths["pdfs"], "B_2016_Y.pdf"), "T", "A", 2016, "10.1/b", "abs")
    make_paper(os.path.join(paths["pdfs"], "C_2016_Z.pdf"), "T", "A", 2016, "10.1/c", "abs")
    index.write_index(paths["index"], [
        {"key": "A_2016_X", "filename": "A_2016_X.pdf", "doi": "10.1/a", "year": "2016", "citations": ""},
        {"key": "B_2016_Y", "filename": "B_2016_Y.pdf", "doi": "10.1/b", "year": "2016", "citations": "5"},
        {"key": "C_2016_Z", "filename": "C_2016_Z.pdf", "doi": "", "year": "2016", "citations": ""},
    ])
    return root, paths


class TestSelection:
    def test_by_default_only_rows_with_a_doi_and_no_count_are_chosen(self, library):
        root, paths = library
        rows = index.read_index(paths["index"])
        chosen = refresh.rows_needing_refresh(rows)
        assert [r["key"] for r in chosen] == ["A_2016_X"]

    def test_all_chooses_every_row_with_a_doi(self, library):
        root, paths = library
        rows = index.read_index(paths["index"])
        chosen = refresh.rows_needing_refresh(rows, refresh_all=True)
        assert sorted(r["key"] for r in chosen) == ["A_2016_X", "B_2016_Y"]

    def test_a_row_with_no_doi_is_never_chosen(self, library):
        root, paths = library
        rows = index.read_index(paths["index"])
        for mode in (dict(), dict(refresh_all=True), dict(keys={"C_2016_Z"})):
            assert "C_2016_Z" not in [r["key"] for r in refresh.rows_needing_refresh(rows, **mode)]

    def test_explicit_keys_override_the_has_a_count_rule(self, library):
        root, paths = library
        rows = index.read_index(paths["index"])
        chosen = refresh.rows_needing_refresh(rows, keys={"B_2016_Y"})
        assert [r["key"] for r in chosen] == ["B_2016_Y"]


class TestRefresh:
    def test_a_count_is_written_with_its_date_and_label(self, library, monkeypatch):
        root, paths = library
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: {
            "display_name": "A paper", "publication_year": 2016, "cited_by_count": 1438,
        })
        result = refresh.refresh(root, today=TODAY)
        assert result["updated"] == 1
        row = {r["key"]: r for r in index.read_index(paths["index"])}["A_2016_X"]
        assert row["citations"] == "1438"
        assert row["citations_retrieved"] == "2026-09-16"
        assert row["impact"] == "high impact"
        assert row["citations_per_year"]

    def test_it_never_searches_by_title(self, library, monkeypatch):
        """A title search is how the wrong counts arrived; refresh must not use one."""
        root, _ = library
        called = []
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: None)
        monkeypatch.setattr(enrich, "fetch_by_title", lambda *a, **k: called.append(1) or None)
        refresh.refresh(root, today=TODAY)
        assert called == []

    def test_an_unresolvable_doi_leaves_the_row_alone(self, library, monkeypatch):
        root, paths = library
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: None)
        result = refresh.refresh(root, today=TODAY)
        assert result["updated"] == 0
        assert result["unresolved"] == ["A_2016_X"]
        row = {r["key"]: r for r in index.read_index(paths["index"])}["A_2016_X"]
        assert row["citations"] == ""

    def test_a_lookup_that_raises_is_caught(self, library, monkeypatch):
        root, _ = library

        def boom(doi):
            raise RuntimeError("network gone")

        monkeypatch.setattr(enrich, "fetch_by_doi", boom)
        result = refresh.refresh(root, today=TODAY)
        assert result["updated"] == 0 and result["unresolved"] == ["A_2016_X"]

    def test_dry_run_changes_nothing(self, library, monkeypatch):
        root, paths = library
        before = open(paths["index"], "rb").read()
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: {"cited_by_count": 10, "publication_year": 2016})
        refresh.refresh(root, dry_run=True, today=TODAY)
        assert open(paths["index"], "rb").read() == before

    def test_an_existing_journal_is_not_overwritten(self, library, monkeypatch):
        root, paths = library
        rows = index.read_index(paths["index"])
        for r in rows:
            if r["key"] == "A_2016_X":
                r["journal"] = "Kept Journal"
        index.write_index(paths["index"], rows)
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: {
            "cited_by_count": 10, "publication_year": 2016,
            "primary_location": {"source": {"display_name": "Other Journal"}},
        })
        refresh.refresh(root, today=TODAY)
        row = {r["key"]: r for r in index.read_index(paths["index"])}["A_2016_X"]
        assert row["journal"] == "Kept Journal"

    def test_main_exits_zero(self, library, monkeypatch, capsys):
        root, _ = library
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: {"cited_by_count": 3, "publication_year": 2016})
        assert refresh.main([root]) == 0
        assert "updated 1" in capsys.readouterr().out
