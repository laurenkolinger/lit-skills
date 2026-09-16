"""Tests for litkit.annotate: merge judgement columns without damaging machine-read ones."""

import csv
import os

import pytest

from litkit import annotate, index


def write_annotations(path, rows):
    """Write an annotations CSV."""
    fields = ["key"] + list(annotate.ANNOTATION_FIELDS)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})
    return path


@pytest.fixture
def library(tmp_path):
    """A library with two rows awaiting annotation."""
    root = str(tmp_path / "Lit")
    paths = index.ensure_layout(root)
    index.write_index(paths["index"], [
        {"key": "Smith_2016_Reefs", "filename": "Smith_2016_Reefs.pdf", "title": "Reefs",
         "authors": "Smith, T", "topic_tags": "needs review", "citations": "42"},
        {"key": "Adams_2001_Corals", "filename": "Adams_2001_Corals.pdf", "title": "Corals",
         "authors": "Adams, A", "topic_tags": "needs review", "citations": "7"},
    ])
    return root, paths


class TestSplitTags:
    def test_splits_and_strips(self):
        assert annotate.split_tags(" coral reef , mesophotic ") == ["coral reef", "mesophotic"]

    @pytest.mark.parametrize("empty", [None, "", "   ", ",,,"])
    def test_empty_input_yields_no_tags(self, empty):
        assert annotate.split_tags(empty) == []


class TestCheckTags:
    def test_known_tags_pass(self):
        row = {"topic_tags": "coral reef, mesophotic", "method_tags": "AUV, photogrammetry",
               "region_tags": "USVI", "taxa_tags": "scleractinia",
               "vicar_relevance": "reef research"}
        assert annotate.check_tags(row) == []

    def test_an_invented_tag_is_reported(self):
        problems = annotate.check_tags({"topic_tags": "coral reef, underwater vibes"})
        assert len(problems) == 1 and "underwater vibes" in problems[0]

    def test_a_tag_in_the_wrong_facet_is_reported(self):
        problems = annotate.check_tags({"region_tags": "AUV"})
        assert len(problems) == 1

    def test_empty_facets_are_fine(self):
        assert annotate.check_tags({"topic_tags": "", "method_tags": None}) == []


class TestApplyAnnotations:
    def test_annotations_reach_the_row(self, tmp_path, library):
        root, paths = library
        path = write_annotations(str(tmp_path / "a.csv"), [{
            "key": "Smith_2016_Reefs", "summary": "Smith surveyed 12 reefs and found cover fell 30 percent.",
            "key_findings": "cover fell 30 percent; the decline held across depths",
            "topic_tags": "coral reef, monitoring", "method_tags": "diver survey",
            "region_tags": "USVI", "taxa_tags": "scleractinia", "vicar_relevance": "reef research",
        }])

        result = annotate.apply_annotations(root, [path])

        assert result["annotated"] == 1
        row = {r["key"]: r for r in index.read_index(paths["index"])}["Smith_2016_Reefs"]
        assert "30 percent" in row["summary"]
        assert row["topic_tags"] == "coral reef, monitoring"
        assert row["vicar_relevance"] == "reef research"

    def test_machine_read_columns_are_untouched(self, tmp_path, library):
        root, paths = library
        path = write_annotations(str(tmp_path / "a.csv"), [
            {"key": "Smith_2016_Reefs", "summary": "A summary.", "topic_tags": "coral reef"}
        ])
        annotate.apply_annotations(root, [path])
        row = {r["key"]: r for r in index.read_index(paths["index"])}["Smith_2016_Reefs"]
        assert row["citations"] == "42"
        assert row["title"] == "Reefs"
        assert row["authors"] == "Smith, T"

    def test_blank_cells_do_not_erase_existing_values(self, tmp_path, library):
        root, paths = library
        first = write_annotations(str(tmp_path / "a.csv"), [
            {"key": "Smith_2016_Reefs", "summary": "First summary.", "topic_tags": "coral reef"}
        ])
        annotate.apply_annotations(root, [first])
        second = write_annotations(str(tmp_path / "b.csv"), [
            {"key": "Smith_2016_Reefs", "key_findings": "a finding"}
        ])
        annotate.apply_annotations(root, [second])
        row = {r["key"]: r for r in index.read_index(paths["index"])}["Smith_2016_Reefs"]
        assert row["summary"] == "First summary."
        assert row["key_findings"] == "a finding"

    def test_several_files_merge_together(self, tmp_path, library):
        root, _ = library
        a = write_annotations(str(tmp_path / "a.csv"), [{"key": "Smith_2016_Reefs", "summary": "A"}])
        b = write_annotations(str(tmp_path / "b.csv"), [{"key": "Adams_2001_Corals", "summary": "B"}])
        result = annotate.apply_annotations(root, [a, b])
        assert result["annotated"] == 2
        # These annotations carried summaries but no tags, so both rows stay on the
        # still_blank list. That list tracks tagging, not annotation in general.
        assert sorted(result["still_blank"]) == ["Adams_2001_Corals", "Smith_2016_Reefs"]

    def test_still_blank_lists_rows_that_never_got_tags(self, tmp_path, library):
        root, _ = library
        path = write_annotations(str(tmp_path / "a.csv"), [
            {"key": "Smith_2016_Reefs", "topic_tags": "coral reef"}
        ])
        result = annotate.apply_annotations(root, [path])
        assert result["still_blank"] == ["Adams_2001_Corals"]

    def test_an_unknown_key_is_reported_not_fatal(self, tmp_path, library):
        root, _ = library
        path = write_annotations(str(tmp_path / "a.csv"), [{"key": "NoSuchRow", "summary": "x"}])
        result = annotate.apply_annotations(root, [path])
        assert result["unmatched"] == ["NoSuchRow"]

    def test_a_missing_file_is_reported_not_fatal(self, tmp_path, library):
        root, _ = library
        result = annotate.apply_annotations(root, [str(tmp_path / "nope.csv")])
        assert "not found" in result["log"]
        assert result["annotated"] == 0

    def test_bad_tags_are_reported_but_still_written_by_default(self, tmp_path, library):
        root, paths = library
        path = write_annotations(str(tmp_path / "a.csv"), [
            {"key": "Smith_2016_Reefs", "topic_tags": "coral reef, made up tag"}
        ])
        result = annotate.apply_annotations(root, [path])
        assert len(result["tag_problems"]) == 1
        row = {r["key"]: r for r in index.read_index(paths["index"])}["Smith_2016_Reefs"]
        assert "made up tag" in row["topic_tags"]

    def test_strict_mode_refuses_to_write_on_a_bad_tag(self, tmp_path, library):
        root, paths = library
        before = open(paths["index"], "rb").read()
        path = write_annotations(str(tmp_path / "a.csv"), [
            {"key": "Smith_2016_Reefs", "topic_tags": "made up tag"}
        ])
        with pytest.raises(ValueError, match="vocabulary"):
            annotate.apply_annotations(root, [path], strict=True)
        assert open(paths["index"], "rb").read() == before

    def test_dry_run_changes_nothing(self, tmp_path, library):
        root, paths = library
        before = open(paths["index"], "rb").read()
        path = write_annotations(str(tmp_path / "a.csv"), [
            {"key": "Smith_2016_Reefs", "summary": "A summary."}
        ])
        annotate.apply_annotations(root, [path], dry_run=True)
        assert open(paths["index"], "rb").read() == before

    def test_a_summary_with_commas_and_quotes_round_trips(self, tmp_path, library):
        root, paths = library
        nasty = 'The team measured "cover", then, oddly, re-measured it.'
        path = write_annotations(str(tmp_path / "a.csv"), [
            {"key": "Smith_2016_Reefs", "summary": nasty}
        ])
        annotate.apply_annotations(root, [path])
        row = {r["key"]: r for r in index.read_index(paths["index"])}["Smith_2016_Reefs"]
        assert row["summary"] == nasty

    def test_main_exits_zero(self, tmp_path, library, capsys):
        root, _ = library
        path = write_annotations(str(tmp_path / "a.csv"), [{"key": "Smith_2016_Reefs", "summary": "A"}])
        assert annotate.main([root, path]) == 0
        assert "annotated 1" in capsys.readouterr().out
