"""Unit tests for litkit.index: deduplication, CSV round trips, cell safety, audit."""

import csv
import os

import pytest

from litkit import index


def write_pdf(path, size_bytes):
    """Create a file of a given size that starts with the PDF magic bytes."""
    with open(path, "wb") as handle:
        handle.write(b"%PDF-1.4\n")
        handle.write(b"x" * max(0, size_bytes - 9))
    return path


class TestNormalizeDoi:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("10.1038/S41598", "10.1038/s41598"),
            ("https://doi.org/10.1038/x", "10.1038/x"),
            ("https://dx.doi.org/10.1038/x", "10.1038/x"),
            ("doi:10.1038/x", "10.1038/x"),
            ("  10.1038/x  ", "10.1038/x"),
            ("10.1038/x.", "10.1038/x"),
            ("10.1038/x)", "10.1038/x"),
        ],
    )
    def test_accepted_forms(self, raw, expected):
        assert index.normalize_doi(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "n/a", "12.345/x", "not a doi"])
    def test_rejected_forms_become_empty(self, raw):
        assert index.normalize_doi(raw) == ""


class TestSanitizeCell:
    def test_newlines_collapse_so_a_row_stays_one_row(self):
        assert index.sanitize_cell("line one\nline two\r\nline three") == "line one line two line three"

    def test_tabs_collapse(self):
        assert index.sanitize_cell("a\tb") == "a b"

    @pytest.mark.parametrize("trigger", ["=", "+", "-", "@"])
    def test_formula_triggers_are_escaped(self, trigger):
        result = index.sanitize_cell(f"{trigger}HYPERLINK('http://evil')")
        assert result.startswith("'")

    def test_ordinary_text_is_untouched(self):
        assert index.sanitize_cell("Coral reef resilience") == "Coral reef resilience"

    def test_control_characters_are_stripped(self):
        assert index.sanitize_cell("a\x00b\x07c") == "abc"

    def test_none_becomes_empty(self):
        assert index.sanitize_cell(None) == ""

    def test_numbers_survive_as_text(self):
        assert index.sanitize_cell(41) == "41"


class TestReadWriteIndex:
    def test_missing_file_reads_as_empty(self, tmp_path):
        assert index.read_index(str(tmp_path / "nope.csv")) == []

    def test_round_trip_preserves_values(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        rows = [
            {"key": "Smith_2016_Reefs", "title": "Reefs", "first_author": "Smith", "year": "2016"},
            {"key": "Adams_2001_Corals", "title": "Corals", "first_author": "Adams", "year": "2001"},
        ]
        index.write_index(path, rows)
        loaded = index.read_index(path)
        assert len(loaded) == 2
        assert {r["key"] for r in loaded} == {"Smith_2016_Reefs", "Adams_2001_Corals"}

    def test_rows_sort_by_first_author_then_year(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        index.write_index(
            path,
            [
                {"key": "c", "first_author": "Smith", "year": "2016"},
                {"key": "a", "first_author": "Adams", "year": "2001"},
                {"key": "b", "first_author": "Smith", "year": "2001"},
            ],
        )
        assert [r["key"] for r in index.read_index(path)] == ["a", "b", "c"]

    def test_header_is_exactly_the_declared_columns(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        index.write_index(path, [{"key": "x"}])
        with open(path, newline="", encoding="utf-8") as handle:
            assert next(csv.reader(handle)) == index.COLUMNS

    def test_unknown_keys_are_dropped_not_appended_as_columns(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        index.write_index(path, [{"key": "x", "bogus_column": "should vanish"}])
        loaded = index.read_index(path)
        assert "bogus_column" not in loaded[0]

    def test_write_is_atomic_and_leaves_no_temp_file(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        index.write_index(path, [{"key": "x"}])
        assert not os.path.exists(f"{path}.tmp")

    def test_empty_row_list_writes_a_header_only_file(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        assert index.write_index(path, []) == 0
        assert index.read_index(path) == []


class TestFindDuplicate:
    def test_doi_match_wins(self):
        rows = [{"key": "Smith_2016_Reefs", "doi": "10.1/x", "title": "Totally different words here"}]
        by_doi, by_title = index.build_lookup(rows)
        match, reason = index.find_duplicate({"doi": "https://doi.org/10.1/X", "title": "Other"}, by_doi, by_title)
        assert match["key"] == "Smith_2016_Reefs"
        assert reason.startswith("doi")

    def test_title_match_is_the_fallback(self):
        rows = [{"key": "Smith_2016_Reefs", "doi": "", "title": "Coral reef resilience in the Caribbean"}]
        by_doi, by_title = index.build_lookup(rows)
        match, reason = index.find_duplicate(
            {"doi": "", "title": "CORAL REEF RESILIENCE IN THE CARIBBEAN!"}, by_doi, by_title
        )
        assert match["key"] == "Smith_2016_Reefs"
        assert reason == "title"

    def test_a_genuinely_new_paper_is_not_a_duplicate(self):
        rows = [{"key": "Smith_2016_Reefs", "doi": "10.1/x", "title": "Coral reef resilience"}]
        by_doi, by_title = index.build_lookup(rows)
        match, _ = index.find_duplicate({"doi": "10.2/y", "title": "Mangrove carbon storage"}, by_doi, by_title)
        assert match is None

    def test_empty_doi_and_title_never_match_anything(self):
        rows = [{"key": "Smith_2016_Reefs", "doi": "", "title": ""}]
        by_doi, by_title = index.build_lookup(rows)
        match, _ = index.find_duplicate({"doi": "", "title": ""}, by_doi, by_title)
        assert match is None


class TestDedupeCandidates:
    def test_largest_file_wins_within_a_doi_group(self, tmp_path):
        big = write_pdf(str(tmp_path / "big.pdf"), 50_000)
        small = write_pdf(str(tmp_path / "small.pdf"), 5_000)
        kept, dropped = index.dedupe_candidates(
            [
                {"path": small, "doi": "10.1/x", "title": "A paper"},
                {"path": big, "doi": "10.1/x", "title": "A paper"},
            ]
        )
        assert len(kept) == 1
        assert kept[0]["path"] == big
        assert dropped[0]["dropped_for"] == big

    def test_title_grouping_when_doi_is_absent(self, tmp_path):
        a = write_pdf(str(tmp_path / "a.pdf"), 20_000)
        b = write_pdf(str(tmp_path / "b.pdf"), 10_000)
        kept, dropped = index.dedupe_candidates(
            [
                {"path": a, "doi": "", "title": "Coral reef resilience"},
                {"path": b, "doi": "", "title": "CORAL REEF RESILIENCE"},
            ]
        )
        assert len(kept) == 1 and len(dropped) == 1

    def test_records_with_neither_doi_nor_title_are_all_kept(self, tmp_path):
        a = write_pdf(str(tmp_path / "a.pdf"), 20_000)
        b = write_pdf(str(tmp_path / "b.pdf"), 20_000)
        kept, dropped = index.dedupe_candidates(
            [{"path": a, "doi": "", "title": ""}, {"path": b, "doi": "", "title": ""}]
        )
        assert len(kept) == 2 and dropped == []

    def test_distinct_papers_all_survive(self, tmp_path):
        a = write_pdf(str(tmp_path / "a.pdf"), 20_000)
        b = write_pdf(str(tmp_path / "b.pdf"), 20_000)
        kept, dropped = index.dedupe_candidates(
            [{"path": a, "doi": "10.1/x", "title": "One"}, {"path": b, "doi": "10.2/y", "title": "Two"}]
        )
        assert len(kept) == 2 and dropped == []

    def test_a_missing_path_is_treated_as_size_zero_and_loses(self, tmp_path):
        real = write_pdf(str(tmp_path / "real.pdf"), 20_000)
        ghost = str(tmp_path / "ghost.pdf")
        kept, _ = index.dedupe_candidates(
            [{"path": ghost, "doi": "10.1/x", "title": "A"}, {"path": real, "doi": "10.1/x", "title": "A"}]
        )
        assert kept[0]["path"] == real

    def test_empty_input_is_handled(self):
        assert index.dedupe_candidates([]) == ([], [])


class TestAssignKeys:
    def test_keys_and_filenames_agree(self):
        records = [{"authors": "Olinger, Lauren", "year": 2019, "title": "Growth estimates of sponges"}]
        index.assign_keys(records)
        assert records[0]["filename"] == f"{records[0]['key']}.pdf"

    def test_collisions_inside_a_batch_are_disambiguated(self):
        records = [
            {"authors": "Smith, T", "year": 2016, "title": "Coral reef structure"},
            {"authors": "Smith, T", "year": 2016, "title": "Coral reef structure"},
        ]
        index.assign_keys(records)
        assert records[0]["key"] != records[1]["key"]

    def test_collisions_against_the_existing_library_are_respected(self):
        existing = {"Smith_2016_CoralReefStructure"}
        records = [{"authors": "Smith, T", "year": 2016, "title": "Coral reef structure"}]
        index.assign_keys(records, taken=existing)
        assert records[0]["key"] not in existing


class TestLayoutAndAudit:
    def test_ensure_layout_creates_every_folder(self, tmp_path):
        paths = index.ensure_layout(str(tmp_path))
        for key in ("pdfs", "ingest", "processed", "logs"):
            assert os.path.isdir(paths[key])

    def test_ensure_layout_is_safe_to_run_twice(self, tmp_path):
        index.ensure_layout(str(tmp_path))
        index.ensure_layout(str(tmp_path))
        assert os.path.isdir(index.library_paths(str(tmp_path))["pdfs"])

    def test_audit_reports_agreement(self, tmp_path):
        paths = index.ensure_layout(str(tmp_path))
        write_pdf(os.path.join(paths["pdfs"], "Smith_2016_Reefs.pdf"), 20_000)
        index.write_index(paths["index"], [{"key": "Smith_2016_Reefs", "filename": "Smith_2016_Reefs.pdf"}])
        report = index.audit(str(tmp_path))
        assert report["rows"] == 1 and report["files"] == 1
        assert report["missing_files"] == [] and report["unindexed_files"] == []
        assert report["duplicate_keys"] == [] and report["duplicate_filenames"] == []

    def test_audit_catches_a_row_with_no_file(self, tmp_path):
        paths = index.ensure_layout(str(tmp_path))
        index.write_index(paths["index"], [{"key": "Ghost_2016_X", "filename": "Ghost_2016_X.pdf"}])
        assert index.audit(str(tmp_path))["missing_files"] == ["Ghost_2016_X.pdf"]

    def test_audit_catches_a_file_with_no_row(self, tmp_path):
        paths = index.ensure_layout(str(tmp_path))
        write_pdf(os.path.join(paths["pdfs"], "Orphan_2016_X.pdf"), 20_000)
        index.write_index(paths["index"], [])
        assert index.audit(str(tmp_path))["unindexed_files"] == ["Orphan_2016_X.pdf"]

    def test_main_returns_nonzero_when_they_disagree(self, tmp_path, capsys):
        paths = index.ensure_layout(str(tmp_path))
        index.write_index(paths["index"], [{"key": "Ghost_2016_X", "filename": "Ghost_2016_X.pdf"}])
        assert index.main([str(tmp_path)]) == 1
        assert "missing" in capsys.readouterr().out

    def test_main_without_arguments_explains_itself(self, capsys):
        assert index.main([]) == 2
        assert "usage" in capsys.readouterr().err


class TestLibraryRoot:
    def test_an_explicit_path_wins(self, monkeypatch):
        monkeypatch.setenv(index.LIBRARY_HOME_VAR, "/from/env")
        assert index.library_root("/explicit") == "/explicit"

    def test_the_environment_is_used_when_no_path_is_given(self, monkeypatch):
        monkeypatch.setenv(index.LIBRARY_HOME_VAR, "/from/env")
        assert index.library_root() == "/from/env"

    def test_it_falls_back_to_the_working_directory(self, monkeypatch):
        monkeypatch.delenv(index.LIBRARY_HOME_VAR, raising=False)
        assert index.library_root() == os.getcwd()

    def test_a_blank_environment_value_is_ignored(self, monkeypatch):
        monkeypatch.setenv(index.LIBRARY_HOME_VAR, "   ")
        assert index.library_root() == os.getcwd()
