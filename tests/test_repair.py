"""Tests for litkit.repair: only improve a row, never damage one."""

import os

import pytest

from conftest import make_paper
from litkit import index, repair


class TestIsSuspect:
    @pytest.mark.parametrize(
        "title",
        ["", "ecol-90-02-19 506..516", "Belize National", "A MATHEMATICAL REVIEW OF RESILIENCE"],
    )
    def test_bad_titles_are_flagged(self, title):
        suspect, reason = repair.is_suspect({"title": title, "authors": "Smith, T"})
        assert suspect is True and reason

    def test_a_good_row_is_not_flagged(self):
        row = {
            "title": "Flattening of Caribbean coral reefs: region-wide declines in complexity",
            "authors": "Alvarez-Filip, Lorenzo",
        }
        assert repair.is_suspect(row) == (False, "")

    def test_a_missing_author_is_flagged(self):
        suspect, reason = repair.is_suspect(
            {"title": "Coral reef ecosystem functioning and biodiversity", "authors": ""}
        )
        assert suspect is True and reason == "no authors"


class TestBetterTitle:
    def test_a_longer_real_title_wins(self):
        assert repair.better_title("Belize National", "Belize National report on reef fish stocks") is True

    def test_junk_never_replaces_a_real_title(self):
        assert repair.better_title("A real and complete paper title here", "ecol-90-02-19 506..516") is False

    def test_anything_real_replaces_junk(self):
        assert repair.better_title("se230101893p", "Multispecies overkill in the late Pleistocene") is True

    def test_a_shorter_title_does_not_win(self):
        assert repair.better_title("A full title with many useful words", "Short title") is False

    def test_all_caps_does_not_replace_mixed_case(self):
        assert repair.better_title("A mathematical review of resilience", "A MATHEMATICAL REVIEW OF RESILIENCE IN ECOLOGY") is False

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_an_empty_new_title_never_wins(self, empty):
        assert repair.better_title("An existing title of some length", empty) is False


class TestTidyCaps:
    def test_all_caps_becomes_title_case(self):
        assert repair.tidy_caps("A MATHEMATICAL REVIEW OF RESILIENCE IN ECOLOGY") == (
            "A Mathematical Review of Resilience in Ecology"
        )

    def test_mixed_case_is_left_alone(self):
        original = "Flattening of Caribbean coral reefs"
        assert repair.tidy_caps(original) == original

    @pytest.mark.parametrize("empty", [None, ""])
    def test_empty_input_is_safe(self, empty):
        assert repair.tidy_caps(empty) == ""


class TestRepairRun:
    def stage_library(self, tmp_path, title, authors="Smith, Tyler B."):
        """Build a library holding one paper whose row carries a bad title."""
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        pdf = os.path.join(paths["pdfs"], "Bad_2016_Ecol900219506.pdf")
        make_paper(pdf, title, authors, 2016, "10.1/repair", "An abstract about reef surveys.")
        index.write_index(
            paths["index"],
            [{
                "key": "Bad_2016_Ecol900219506",
                "filename": "Bad_2016_Ecol900219506.pdf",
                "title": "ecol-90-02-19 506..516",
                "authors": "",
                "year": "2016",
            }],
        )
        return root, paths

    def test_a_bad_row_is_repaired_and_its_file_renamed(self, tmp_path):
        root, paths = self.stage_library(tmp_path, "Coral cover decline across the Caribbean shelf")

        result = repair.repair(root, use_network=False)

        assert result["flagged"] == 1
        assert result["repaired"] == 1
        assert result["renamed"] == 1

        row = index.read_index(paths["index"])[0]
        assert "Coral cover decline" in row["title"]
        assert row["filename"] == row["key"] + ".pdf"
        assert os.path.exists(os.path.join(paths["pdfs"], row["filename"]))
        assert not os.path.exists(os.path.join(paths["pdfs"], "Bad_2016_Ecol900219506.pdf"))

    def test_the_index_and_the_folder_still_agree_after_a_repair(self, tmp_path):
        root, _ = self.stage_library(tmp_path, "Coral cover decline across the Caribbean shelf")
        repair.repair(root, use_network=False)
        report = index.audit(root)
        assert report["missing_files"] == [] and report["unindexed_files"] == []

    def test_dry_run_changes_nothing(self, tmp_path):
        root, paths = self.stage_library(tmp_path, "Coral cover decline across the Caribbean shelf")
        before = open(paths["index"], "rb").read()

        result = repair.repair(root, dry_run=True, use_network=False)

        assert result["repaired"] == 1
        assert open(paths["index"], "rb").read() == before
        assert os.path.exists(os.path.join(paths["pdfs"], "Bad_2016_Ecol900219506.pdf"))

    def test_a_clean_library_is_left_untouched(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        make_paper(
            os.path.join(paths["pdfs"], "Smith_2016_CoralCoverDeclineCaribbeanShelf.pdf"),
            "Coral cover decline across the Caribbean shelf", "Smith, Tyler B.", 2016, "10.1/a", "abs",
        )
        index.write_index(paths["index"], [{
            "key": "Smith_2016_CoralCoverDeclineCaribbeanShelf",
            "filename": "Smith_2016_CoralCoverDeclineCaribbeanShelf.pdf",
            "title": "Coral cover decline across the Caribbean shelf",
            "authors": "Smith, Tyler B.", "year": "2016",
        }])
        before = open(paths["index"], "rb").read()

        result = repair.repair(root, use_network=False)

        assert result["flagged"] == 0
        assert open(paths["index"], "rb").read() == before

    def test_a_row_whose_pdf_vanished_is_skipped_not_crashed(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        index.write_index(paths["index"], [{
            "key": "Ghost_2016_X", "filename": "Ghost_2016_X.pdf", "title": "x", "authors": "",
        }])
        result = repair.repair(root, use_network=False)
        assert result["flagged"] == 1 and result["repaired"] == 0
        assert "its PDF is missing" in result["log"]

    def test_repair_never_creates_a_duplicate_key(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        for name in ("a.pdf", "b.pdf"):
            make_paper(
                os.path.join(paths["pdfs"], name),
                "Coral cover decline across the Caribbean shelf", "Smith, Tyler B.", 2016,
                f"10.1/{name}", "abs",
            )
        index.write_index(paths["index"], [
            {"key": "a", "filename": "a.pdf", "title": "junk_one_0105", "authors": "", "year": "2016"},
            {"key": "b", "filename": "b.pdf", "title": "junk_two_0105", "authors": "", "year": "2016"},
        ])

        repair.repair(root, use_network=False)

        rows = index.read_index(paths["index"])
        assert len({r["key"] for r in rows}) == len(rows)
        assert len({r["filename"] for r in rows}) == len(rows)
        assert index.audit(root)["missing_files"] == []

    def test_main_reports_and_exits_zero(self, tmp_path, capsys):
        root, _ = self.stage_library(tmp_path, "Coral cover decline across the Caribbean shelf")
        assert repair.main([root, "--no-network"]) == 0
        assert "repaired 1" in capsys.readouterr().out


class TestAuthorsOnlyChangeOnAuthority:
    """Text-derived authors may name a file. They may never overwrite a row."""

    def stage(self, tmp_path, stored_author):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        make_paper(
            os.path.join(paths["pdfs"], "x_2016_X.pdf"),
            "Coral cover decline across the Caribbean shelf",
            "Smith, Tyler B.", 2016, "10.1/x", "An abstract about reef surveys.",
        )
        index.write_index(paths["index"], [{
            "key": "x_2016_X", "filename": "x_2016_X.pdf",
            "title": "Coral cover decline across the Caribbean shelf",
            "authors": stored_author, "first_author": stored_author, "year": "2016",
        }])
        return root, paths

    def test_without_a_match_a_junk_author_is_left_alone(self, tmp_path):
        root, paths = self.stage(tmp_path, "mwoodard")
        repair.repair(root, use_network=False)
        assert index.read_index(paths["index"])[0]["authors"] == "mwoodard"

    def test_with_a_match_the_junk_author_is_replaced(self, tmp_path, monkeypatch):
        from litkit import enrich

        root, paths = self.stage(tmp_path, "mwoodard")
        monkeypatch.setattr(
            enrich, "fetch_by_doi",
            lambda doi: {
                "display_name": "Coral cover decline across the Caribbean shelf",
                "publication_year": 2016,
                "cited_by_count": 12,
                "authorships": [{"author": {"display_name": "Tyler B. Smith"}}],
            },
        )
        repair.repair(root, use_network=True)
        assert index.read_index(paths["index"])[0]["authors"] == "Tyler B. Smith"

    def test_a_capitalized_title_is_never_installed_as_an_author(self, tmp_path):
        root, paths = self.stage(tmp_path, "mwoodard")
        repair.repair(root, use_network=False)
        stored = index.read_index(paths["index"])[0]["authors"]
        assert not stored.isupper()
