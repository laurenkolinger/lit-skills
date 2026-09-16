"""Tests pinned to the defects the independent review found on 2026-09-15.

Each test names the defect it covers so a regression is obvious.
"""

import os

import pytest

from conftest import make_paper
from litkit import corrections, enrich, extract, index, naming


class TestD5AuthorParsing:
    """The comma branch treated a western-order author list as a surname-first name."""

    @pytest.mark.parametrize(
        "authors,expected",
        [
            # Surname-first: the text before the comma is the surname.
            ("Smith, Tyler B.", "Smith"),
            ("Alvarez-Filip, Lorenzo", "AlvarezFilip"),
            ("Nemeth, Richard S.", "Nemeth"),
            # Western order with a comma separating AUTHORS, the reviewer's probes.
            ("John A. Smith, Mary B. Jones and Carl Doe", "Smith"),
            ("Tyler Smith, Bob Jones", "Smith"),
            ("SHINICHI KOBARA1, WILLIAM D. HEYMAN2", "KOBARA"),
            ("Lauren K. Olinger, Sarah L. Heidmann", "Olinger"),
            # Semicolons still win over commas.
            ("Smith, Tyler B.; Brandt, Marilyn E.", "Smith"),
        ],
    )
    def test_first_surname_handles_both_orders(self, authors, expected):
        assert naming.first_surname(authors) == expected

    def test_affiliation_digits_are_stripped_from_a_surname(self):
        assert naming.first_surname("KOBARA1, WILLIAM") == "KOBARA"

    @pytest.mark.parametrize(
        "raw,expected",
        [("Bjorn Ødegård", "Odegard"), ("Ødegård, Bjorn", "Odegard"), ("Æsop, A.", "AEsop")],
    )
    def test_nordic_letters_fold_rather_than_vanish(self, raw, expected):
        assert naming.first_surname(raw) == expected


class TestD2ClearingAField:
    """A correction must be able to say "this value is wrong and I have no replacement"."""

    def build(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        make_paper(os.path.join(paths["pdfs"], "K_2016_X.pdf"), "T", "A", 2016, "10.1/x", "abs")
        index.write_index(paths["index"], [{
            "key": "K_2016_X", "filename": "K_2016_X.pdf", "title": "A title",
            "authors": "Smith, T", "year": "2016", "doi": "10.1126/science.WRONG",
        }])
        return root, paths

    def write(self, path, rows):
        import csv
        fields = ["old_key", "title", "authors", "first_author", "year", "journal", "doi", "new_key"]
        with open(path, "w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({f: r.get(f, "") for f in fields})
        return path

    def test_the_clear_token_empties_a_field(self, tmp_path):
        root, paths = self.build(tmp_path)
        p = self.write(str(tmp_path / "c.csv"), [{"old_key": "K_2016_X", "doi": corrections.CLEAR_TOKEN}])
        result = corrections.apply_corrections(root, p)
        assert result["applied"] == 1
        assert index.read_index(paths["index"])[0]["doi"] == ""

    def test_a_blank_cell_still_means_no_change(self, tmp_path):
        root, paths = self.build(tmp_path)
        p = self.write(str(tmp_path / "c.csv"), [{"old_key": "K_2016_X", "title": "A better title here"}])
        corrections.apply_corrections(root, p)
        row = index.read_index(paths["index"])[0]
        assert row["doi"] == "10.1126/science.WRONG"
        assert row["title"] == "A better title here"

    def test_clearing_is_reported_as_a_change(self, tmp_path):
        root, _ = self.build(tmp_path)
        p = self.write(str(tmp_path / "c.csv"), [{"old_key": "K_2016_X", "doi": corrections.CLEAR_TOKEN}])
        assert "doi" in corrections.apply_corrections(root, p)["log"]


class TestCorrectionsPathTraversal:
    def test_a_new_key_cannot_escape_the_pdf_folder(self, tmp_path):
        import csv

        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        make_paper(os.path.join(paths["pdfs"], "K_2016_X.pdf"), "T", "A", 2016, "10.1/x", "abs")
        index.write_index(paths["index"], [
            {"key": "K_2016_X", "filename": "K_2016_X.pdf", "title": "A title"}
        ])
        p = str(tmp_path / "c.csv")
        with open(p, "w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=["old_key", "new_key"])
            w.writeheader()
            w.writerow({"old_key": "K_2016_X", "new_key": "../../escaped/evil"})
        corrections.apply_corrections(root, p)

        row = index.read_index(paths["index"])[0]
        assert ".." not in row["key"] and "/" not in row["key"]
        assert os.path.exists(os.path.join(paths["pdfs"], row["filename"]))
        assert not os.path.exists(str(tmp_path / "escaped"))


class TestD4VerifyBeforeCommit:
    """A DOI match that describes a different paper must be rejected, not merely noted."""

    def test_a_confidently_wrong_match_is_rejected_and_the_row_keeps_its_own_metadata(self, monkeypatch):
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: {
            "display_name": "Discovering important people and objects for egocentric video summarization",
            "publication_year": 2012, "cited_by_count": 900,
            "authorships": [{"author": {"display_name": "Yong Jae Lee"}}],
        })
        monkeypatch.setattr(enrich, "fetch_by_title", lambda t, year=None: None)
        record = {
            "title": "Automated annotation of coral reef survey images",
            "title_alt": "", "doi": "10.1109/CVPR.2012.6247820", "year": "2012",
        }
        merged = enrich.enrich(record)
        assert merged["title"] == "Automated annotation of coral reef survey images"
        assert merged["citations"] == ""
        assert merged["impact"] == enrich.IMPACT_UNRATED
        assert merged["metadata_mismatch"] is True
        assert merged["doi"] == ""

    def test_a_good_match_still_commits(self, monkeypatch):
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: {
            "display_name": "Automated annotation of coral reef survey images",
            "publication_year": 2012, "cited_by_count": 120,
            "authorships": [{"author": {"display_name": "Oscar Beijbom"}}],
        })
        record = {"title": "Automated annotation of coral reef survey images",
                  "title_alt": "", "doi": "10.1/x", "year": "2012"}
        merged = enrich.enrich(record)
        assert merged["citations"] == 120
        assert merged["metadata_mismatch"] is False

    def test_a_weak_own_title_does_not_reject_a_match(self, monkeypatch):
        """When the PDF gave us nothing to compare against, trust the DOI rather than discard it."""
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: {
            "display_name": "A perfectly reasonable canonical paper title about reefs",
            "publication_year": 2016, "cited_by_count": 50,
            "authorships": [{"author": {"display_name": "A Author"}}],
        })
        merged = enrich.enrich({"title": "Galaxea,", "title_alt": "", "doi": "10.1/x", "year": "2016"})
        assert merged["citations"] == 50
        assert merged["metadata_mismatch"] is False


class TestSmallButReadablePdfs:
    """Plan decision 4: reject a small file only when it also yields no text."""

    def test_a_small_text_bearing_pdf_is_accepted(self, tmp_path):
        path = make_paper(
            str(tmp_path / "small.pdf"),
            "A short but real paper about coral reef monitoring",
            "Smith, Tyler B.", 2016, "10.1/small",
            "This study surveyed reefs and reports the change in cover over the period. " * 3,
            pad_to=0,
        )
        assert os.path.getsize(path) < extract.MIN_USABLE_BYTES
        usable, quality, reason = extract.inspect(path)
        assert usable is True, reason

    def test_a_tiny_stub_with_no_text_is_still_rejected(self, tmp_path):
        stub = tmp_path / "stub.pdf"
        stub.write_bytes(b"%PDF-1.4\n" + b" " * 300)
        usable, _, _ = extract.inspect(str(stub))
        assert usable is False


class TestAuthorLinePrecedence:
    """extract.py used `A or (B and C)`, so the length guard never applied."""

    @pytest.mark.parametrize(
        "line",
        ["Tyler B. Smith, Marilyn E. Brandt", "Smith, T. B., and Brandt, M.",
         "Lauren K. Olinger, Sarah L. Heidmann, Allie N. Durdall"],
    )
    def test_real_author_lines_with_initials_are_not_discarded(self, line):
        text = "A study of coral reef resilience across the shelf\n" + line + "\nAbstract\nWe surveyed."
        assert extract.guess_authors_from_text(text, "A study of coral reef resilience across the shelf")


class TestAuditCatchesDuplicates:
    def test_two_rows_pointing_at_one_pdf_are_reported(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        make_paper(os.path.join(paths["pdfs"], "K_2016_X.pdf"), "T", "A", 2016, "10.1/x", "abs")
        index.write_index(paths["index"], [
            {"key": "a", "filename": "K_2016_X.pdf"},
            {"key": "b", "filename": "K_2016_X.pdf"},
        ])
        report = index.audit(root)
        assert report["duplicate_filenames"] == ["K_2016_X.pdf"]

    def test_duplicate_keys_are_reported(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        index.write_index(paths["index"], [{"key": "a", "filename": "x.pdf"}, {"key": "a", "filename": "y.pdf"}])
        assert index.audit(root)["duplicate_keys"] == ["a"]

    def test_a_clean_library_reports_none(self, tmp_path):
        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        make_paper(os.path.join(paths["pdfs"], "K_2016_X.pdf"), "T", "A", 2016, "10.1/x", "abs")
        index.write_index(paths["index"], [{"key": "K_2016_X", "filename": "K_2016_X.pdf"}])
        report = index.audit(root)
        assert report["duplicate_keys"] == [] and report["duplicate_filenames"] == []


class TestContactEmailIsConfigurable:
    def test_the_environment_overrides_the_default(self, monkeypatch):
        monkeypatch.setenv("VICAR_LIT_CONTACT_EMAIL", "lab@example.org")
        assert enrich.contact_email() == "lab@example.org"

    def test_there_is_a_usable_default(self, monkeypatch):
        monkeypatch.delenv("VICAR_LIT_CONTACT_EMAIL", raising=False)
        assert "@" in enrich.contact_email()
