"""Tests for litkit.corrections: hand-checked fixes must apply exactly and safely."""

import os

import pytest

from conftest import make_paper
from litkit import corrections, index


def write_corrections(path, rows, header=None):
    """Write a corrections CSV from a list of dicts."""
    import csv

    fields = header or ["old_key", "title", "authors", "first_author", "year", "journal", "doi", "new_key"]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})
    return path


@pytest.fixture
def library(tmp_path):
    """A library holding one paper whose row carries wrong metadata."""
    root = str(tmp_path / "Lit")
    paths = index.ensure_layout(root)
    make_paper(os.path.join(paths["pdfs"], "Bad_1910_Junk.pdf"), "T", "A", 2001, "10.1/x", "abs")
    index.write_index(paths["index"], [{
        "key": "Bad_1910_Junk", "filename": "Bad_1910_Junk.pdf",
        "title": "se230101893p", "authors": "Alroy", "year": "1910", "source": "test",
    }])
    return root, paths


class TestLoadCorrections:
    def test_missing_file_raises_a_named_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            corrections.load_corrections(str(tmp_path / "nope.csv"))

    def test_a_file_without_old_key_is_rejected(self, tmp_path):
        path = write_corrections(str(tmp_path / "c.csv"), [], header=["title", "authors"])
        with pytest.raises(ValueError, match="old_key"):
            corrections.load_corrections(path)

    def test_blank_rows_are_ignored(self, tmp_path):
        path = write_corrections(str(tmp_path / "c.csv"), [{"old_key": ""}, {"old_key": "a"}])
        assert len(corrections.load_corrections(path)) == 1


class TestApplyCorrections:
    def test_fields_are_corrected_and_the_file_renamed(self, tmp_path, library):
        root, paths = library
        path = write_corrections(str(tmp_path / "c.csv"), [{
            "old_key": "Bad_1910_Junk",
            "title": "A multispecies overkill simulation",
            "authors": "Alroy, John", "first_author": "Alroy", "year": "2001",
            "journal": "Science", "doi": "10.1126/science.1059342",
            "new_key": "Alroy_2001_MultispeciesOverkillSimulation",
        }])

        result = corrections.apply_corrections(root, path)

        assert result["applied"] == 1 and result["renamed"] == 1
        row = index.read_index(paths["index"])[0]
        assert row["key"] == "Alroy_2001_MultispeciesOverkillSimulation"
        assert row["year"] == "2001"
        assert row["doi"] == "10.1126/science.1059342"
        assert os.path.exists(os.path.join(paths["pdfs"], row["filename"]))
        assert not os.path.exists(os.path.join(paths["pdfs"], "Bad_1910_Junk.pdf"))

    def test_the_index_and_folder_still_agree(self, tmp_path, library):
        root, _ = library
        path = write_corrections(str(tmp_path / "c.csv"), [{
            "old_key": "Bad_1910_Junk", "title": "A real title here now",
            "new_key": "Alroy_2001_RealTitle",
        }])
        corrections.apply_corrections(root, path)
        report = index.audit(root)
        assert report["missing_files"] == [] and report["unindexed_files"] == []

    def test_empty_cells_do_not_erase_existing_values(self, tmp_path, library):
        root, paths = library
        path = write_corrections(str(tmp_path / "c.csv"), [
            {"old_key": "Bad_1910_Junk", "title": "A real title here now"}
        ])
        corrections.apply_corrections(root, path)
        row = index.read_index(paths["index"])[0]
        assert row["authors"] == "Alroy"
        assert row["source"] == "test"

    def test_an_unknown_key_is_reported_not_fatal(self, tmp_path, library):
        root, _ = library
        path = write_corrections(str(tmp_path / "c.csv"), [{"old_key": "NoSuchRow", "title": "x"}])
        result = corrections.apply_corrections(root, path)
        assert result["missing"] == ["NoSuchRow"]
        assert result["applied"] == 0

    def test_dry_run_changes_nothing(self, tmp_path, library):
        root, paths = library
        before = open(paths["index"], "rb").read()
        path = write_corrections(str(tmp_path / "c.csv"), [{
            "old_key": "Bad_1910_Junk", "title": "A real title", "new_key": "Alroy_2001_Real",
        }])
        corrections.apply_corrections(root, path, dry_run=True)
        assert open(paths["index"], "rb").read() == before
        assert os.path.exists(os.path.join(paths["pdfs"], "Bad_1910_Junk.pdf"))

    def test_a_new_key_colliding_with_an_existing_one_is_disambiguated(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        for name in ("Bad_1910_Junk.pdf", "Alroy_2001_Real.pdf"):
            make_paper(os.path.join(paths["pdfs"], name), "T", "A", 2001, "10.1/x", "abs")
        index.write_index(paths["index"], [
            {"key": "Bad_1910_Junk", "filename": "Bad_1910_Junk.pdf", "title": "junk_0105"},
            {"key": "Alroy_2001_Real", "filename": "Alroy_2001_Real.pdf", "title": "Already here"},
        ])
        path = write_corrections(str(tmp_path / "c.csv"), [{
            "old_key": "Bad_1910_Junk", "title": "A real title", "new_key": "Alroy_2001_Real",
        }])

        corrections.apply_corrections(root, path)

        rows = index.read_index(paths["index"])
        assert len({r["key"] for r in rows}) == 2
        assert index.audit(root)["missing_files"] == []

    def test_main_exits_zero(self, tmp_path, library, capsys):
        root, _ = library
        path = write_corrections(str(tmp_path / "c.csv"), [{"old_key": "Bad_1910_Junk", "title": "A real title"}])
        assert corrections.main([root, path]) == 0
        assert "applied 1" in capsys.readouterr().out


class TestFindDuplicates:
    def test_rows_sharing_a_doi_are_grouped(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        index.write_index(paths["index"], [
            {"key": "a", "doi": "10.1/x", "title": "One"},
            {"key": "b", "doi": "10.1/X", "title": "Two"},
            {"key": "c", "doi": "10.2/y", "title": "Three"},
        ])
        groups = corrections.find_duplicates(root)
        assert len(groups) == 1
        assert {r["key"] for r in groups[0]} == {"a", "b"}

    def test_rows_sharing_a_title_are_grouped_when_there_is_no_doi(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        index.write_index(paths["index"], [
            {"key": "a", "doi": "", "title": "Status of multi-species spawning aggregations"},
            {"key": "b", "doi": "", "title": "STATUS OF MULTI-SPECIES SPAWNING AGGREGATIONS!"},
        ])
        assert len(corrections.find_duplicates(root)) == 1

    def test_a_clean_index_reports_none(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        index.write_index(paths["index"], [
            {"key": "a", "doi": "10.1/x", "title": "One"},
            {"key": "b", "doi": "10.2/y", "title": "Two"},
        ])
        assert corrections.find_duplicates(root) == []
