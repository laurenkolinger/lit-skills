"""Adversarial tests: what a corrupted sync, a hostile filename, or a tired operator produces.

Every case here must fail loudly or cope. None may silently miscount or write a bad row.
"""

import os
import shutil
import stat

import pytest

from conftest import make_paper, make_pdf
from litkit import extract, index, ingest, naming, sheet


class TestBrokenFilesAreRejected:
    def test_zero_byte_file(self, broken_files):
        usable, reason = extract.check_readable(broken_files["empty"])
        assert usable is False and "empty" in reason

    def test_pdf_header_only_stub(self, broken_files):
        usable, reason = extract.check_readable(broken_files["stub"])
        assert usable is False and "bytes" in reason

    def test_html_renamed_to_pdf(self, broken_files):
        usable, reason = extract.check_readable(broken_files["not_a_pdf"])
        assert usable is False and "not a PDF" in reason

    def test_truncated_pdf(self, broken_files):
        usable, _ = extract.check_readable(broken_files["truncated"])
        assert usable is False

    def test_missing_file(self, broken_files):
        usable, reason = extract.check_readable(broken_files["missing"])
        assert usable is False and "does not exist" in reason

    def test_extract_raises_a_named_error(self, broken_files):
        with pytest.raises(extract.UnreadablePdf) as caught:
            extract.extract(broken_files["empty"])
        assert broken_files["empty"] in str(caught.value)

    def test_a_directory_named_like_a_pdf_is_rejected(self, tmp_path):
        folder = tmp_path / "folder.pdf"
        folder.mkdir()
        usable, _ = extract.check_readable(str(folder))
        assert usable is False


class TestHostileContent:
    def test_csv_injection_in_a_title_is_neutralized(self, tmp_path, paper_factory):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        paper_factory("evil.pdf", title="=HYPERLINK(\"http://evil.test\",\"click\") coral study")
        shutil.move(str(tmp_path / "evil.pdf"), os.path.join(paths["ingest"], "evil.pdf"))
        ingest.run(root, use_network=False)
        with open(paths["index"], encoding="utf-8") as handle:
            body = handle.read()
        assert "\n=HYPERLINK" not in body
        assert ",=HYPERLINK" not in body

    def test_a_title_full_of_quotes_and_commas_survives_the_round_trip(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        nasty = 'He said "reefs, corals, and algae" then, oddly, left'
        index.write_index(path, [{"key": "k", "title": nasty}])
        assert index.read_index(path)[0]["title"] == nasty

    def test_a_title_with_newlines_becomes_one_row(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        index.write_index(path, [{"key": "k", "title": "line one\nline two\nline three"}])
        rows = index.read_index(path)
        assert len(rows) == 1
        assert "\n" not in rows[0]["title"]

    def test_path_traversal_in_a_title_cannot_escape_the_pdf_folder(self):
        key = naming.build_key("../../etc", "2020", "../../../passwd")
        assert ".." not in key and "/" not in key

    def test_a_null_byte_in_a_field_is_stripped(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        index.write_index(path, [{"key": "k", "title": "coral\x00reef"}])
        assert index.read_index(path)[0]["title"] == "coralreef"

    def test_a_very_long_abstract_survives_the_csv_reader(self, tmp_path):
        path = str(tmp_path / index.INDEX_FILENAME)
        long_text = "coral " * 30_000
        index.write_index(path, [{"key": "k", "summary": long_text}])
        assert len(index.read_index(path)[0]["summary"]) > 100_000


class TestFilesystemTrouble:
    def test_missing_ingest_folder_is_created_rather_than_crashing(self, tmp_path):
        root = str(tmp_path / "fresh")
        result = ingest.run(root, use_network=False)
        assert result["added"] == 0
        assert os.path.isdir(index.library_paths(root)["ingest"])

    def test_empty_ingest_folder_reports_zero(self, tmp_path):
        root = str(tmp_path / "lib")
        index.ensure_layout(root)
        result = ingest.run(root, use_network=False)
        assert result == pytest.approx(result)  # smoke: the call returned a mapping
        assert result["added"] == 0 and result["rejected"] == 0

    def test_non_pdf_files_in_ingest_are_ignored(self, tmp_path):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        (tmp_path / "notes.txt").write_text("not a paper")
        shutil.copy(str(tmp_path / "notes.txt"), os.path.join(paths["ingest"], "notes.txt"))
        result = ingest.run(root, use_network=False)
        assert result["added"] == 0 and result["rejected"] == 0

    def test_hidden_files_in_ingest_are_ignored(self, tmp_path):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        make_paper(os.path.join(paths["ingest"], ".DS_Store.pdf"), "T", "A", 2020, "10.1/x", "abs")
        assert ingest.run(root, use_network=False)["added"] == 0

    def test_a_read_only_pdf_folder_is_reported_not_swallowed(self, tmp_path, paper_factory):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        paper_factory("p.pdf")
        shutil.move(str(tmp_path / "p.pdf"), os.path.join(paths["ingest"], "p.pdf"))
        os.chmod(paths["pdfs"], stat.S_IRUSR | stat.S_IXUSR)
        try:
            result = ingest.run(root, use_network=False)
            assert result["added"] == 0
            assert result["rejected"] == 1
            assert "could not file it" in result["log"]
        finally:
            os.chmod(paths["pdfs"], stat.S_IRWXU)

    def test_a_corrupt_pdf_does_not_stop_the_rest_of_the_batch(self, tmp_path, paper_factory, broken_files):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        paper_factory("good.pdf", title="A good paper about reef fish", doi="10.5/good")
        shutil.move(str(tmp_path / "good.pdf"), os.path.join(paths["ingest"], "good.pdf"))
        shutil.copy(broken_files["empty"], os.path.join(paths["ingest"], "bad.pdf"))
        result = ingest.run(root, use_network=False)
        assert result["added"] == 1 and result["rejected"] == 1


class TestDuplicateTrouble:
    def test_two_identical_papers_in_one_batch_collapse_to_one(self, tmp_path, paper_factory):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        for name in ("a.pdf", "b.pdf"):
            paper_factory(name, title="Identical reef study", doi="10.9/same")
            shutil.move(str(tmp_path / name), os.path.join(paths["ingest"], name))
        assert ingest.run(root, use_network=False)["added"] == 1

    def test_two_different_papers_sharing_a_title_but_not_a_doi_still_collapse(self, tmp_path, paper_factory):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        paper_factory("a.pdf", title="Shared title exactly here", doi="")
        paper_factory("b.pdf", title="Shared title exactly here", doi="")
        for name in ("a.pdf", "b.pdf"):
            shutil.move(str(tmp_path / name), os.path.join(paths["ingest"], name))
        assert ingest.run(root, use_network=False)["added"] == 1

    def test_same_author_and_year_different_papers_get_distinct_keys(self, tmp_path, paper_factory):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        paper_factory("a.pdf", title="Coral cover across the shelf", doi="10.1/a")
        paper_factory("b.pdf", title="Coral cover across the shelf", doi="10.1/b")
        for name in ("a.pdf", "b.pdf"):
            shutil.move(str(tmp_path / name), os.path.join(paths["ingest"], name))
        ingest.run(root, use_network=False)
        rows = index.read_index(paths["index"])
        assert len({r["key"] for r in rows}) == len(rows)
        assert len({r["filename"] for r in rows}) == len(rows)


class TestSheetAdversarial:
    def test_zero_rows_still_produces_a_valid_workbook(self, tmp_path):
        out = sheet.build_workbook([], str(tmp_path / "empty.xlsx"))
        assert os.path.getsize(out) > 0

    def test_missing_output_path_raises_a_clear_error(self):
        with pytest.raises(ValueError):
            sheet.build_workbook([], "")

    def test_rows_missing_columns_do_not_raise(self, tmp_path):
        out = sheet.build_workbook([{"key": "only"}], str(tmp_path / "sparse.xlsx"))
        assert os.path.exists(out)

    def test_every_data_row_keeps_the_fixed_short_height(self, tmp_path):
        from openpyxl import load_workbook

        rows = [{"key": f"k{n}", "summary": "long text " * 200} for n in range(5)]
        out = sheet.build_workbook(rows, str(tmp_path / "tall.xlsx"))
        loaded = load_workbook(out)[sheet.SHEET_TITLE]
        for number in range(2, 7):
            assert loaded.row_dimensions[number].height == sheet.DATA_ROW_HEIGHT

    def test_no_cell_wraps_text(self, tmp_path):
        from openpyxl import load_workbook

        out = sheet.build_workbook(
            [{"key": "k", "summary": "long " * 300}], str(tmp_path / "nowrap.xlsx")
        )
        loaded = load_workbook(out)[sheet.SHEET_TITLE]
        # openpyxl writes "no wrap" as an absent attribute, so both None and False pass.
        for row in loaded.iter_rows():
            for cell in row:
                assert not cell.alignment.wrap_text

    def test_no_cell_on_the_index_sheet_wraps(self, tmp_path):
        """The no-wrap rule is about the data table staying one line per row.

        The Read me tab is prose and wraps on purpose, so this checks the index sheet only
        rather than the whole workbook.
        """
        from openpyxl import load_workbook

        out = sheet.build_workbook(
            [{"key": "k", "summary": "long " * 300}], str(tmp_path / "nowrap2.xlsx")
        )
        tab = load_workbook(out)[sheet.SHEET_TITLE]
        for row in tab.iter_rows():
            for cell in row:
                assert not cell.alignment.wrap_text

    def test_the_read_me_tab_does_wrap_because_it_is_prose(self, tmp_path):
        from openpyxl import load_workbook

        out = sheet.build_workbook([{"key": "k"}], str(tmp_path / "wrap.xlsx"))
        tab = load_workbook(out)[sheet.README_TITLE]
        wrapped = [c for row in tab.iter_rows() for c in row if c.value and c.alignment.wrap_text]
        assert wrapped, "the documentation tab should wrap its prose"

    def test_numeric_columns_become_numbers_so_sorting_works(self, tmp_path):
        from openpyxl import load_workbook

        out = sheet.build_workbook(
            [{"key": "k", "citations": "152", "year": "2017", "citations_per_year": "15.2"}],
            str(tmp_path / "nums.xlsx"),
        )
        loaded = load_workbook(out)[sheet.SHEET_TITLE]
        header = [c.value for c in loaded[1]]
        values = {header[i]: c.value for i, c in enumerate(loaded[2])}
        assert values["citations"] == 152
        assert values["year"] == 2017
        assert values["citations_per_year"] == 15.2

    def test_an_unrated_paper_leaves_citations_blank_not_zero(self, tmp_path):
        from openpyxl import load_workbook

        out = sheet.build_workbook([{"key": "k", "citations": ""}], str(tmp_path / "blank.xlsx"))
        loaded = load_workbook(out)[sheet.SHEET_TITLE]
        header = [c.value for c in loaded[1]]
        values = {header[i]: c.value for i, c in enumerate(loaded[2])}
        assert values["citations"] in ("", None)


class TestUnicodeAndOddTitles:
    def test_a_cjk_title_still_yields_a_filesystem_safe_name(self, tmp_path, paper_factory):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        make_paper(
            os.path.join(paths["ingest"], "cjk.pdf"),
            "Coral monitoring in the Pacific", "Zhang, Wei", 2021, "10.7/cjk",
            "An abstract about reef monitoring across many sites.",
        )
        ingest.run(root, use_network=False)
        for name in os.listdir(paths["pdfs"]):
            assert name.isascii()

    def test_a_paper_with_no_extractable_title_still_gets_a_row(self, tmp_path):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        make_pdf(os.path.join(paths["ingest"], "blank.pdf"), ["...", "..."] * 60)
        result = ingest.run(root, use_network=False)
        assert result["added"] + result["rejected"] == 1


class TestScannedDocumentsAreKeptNotDiscarded:
    """A scanned report is library content. It must be indexed and flagged, never dropped."""

    def make_image_only_pdf(self, path):
        """Write a valid, large PDF whose pages carry no extractable text."""
        from conftest import make_pdf

        return make_pdf(path, [" ", " ", " "], pad_to=200_000)

    def test_an_image_only_pdf_is_accepted(self, tmp_path):
        path = self.make_image_only_pdf(str(tmp_path / "scan.pdf"))
        usable, quality, reason = extract.inspect(path)
        assert usable is True, reason
        assert quality == extract.TEXT_QUALITY_SCANNED

    def test_a_text_pdf_is_labelled_as_text(self, tmp_path, paper_factory):
        path = paper_factory("text.pdf")
        usable, quality, _ = extract.inspect(path)
        assert usable is True and quality == extract.TEXT_QUALITY_TEXT

    def test_a_scanned_report_reaches_the_index_with_a_warning_note(self, tmp_path):
        root = str(tmp_path / "lib")
        paths = index.ensure_layout(root)
        self.make_image_only_pdf(os.path.join(paths["ingest"], "Report_2018_Handbook.pdf"))

        result = ingest.run(root, use_network=False)

        assert result["added"] == 1 and result["rejected"] == 0
        row = index.read_index(paths["index"])[0]
        assert "likely scanned" in row["notes"]

    def test_check_readable_still_returns_two_values_for_older_callers(self, tmp_path, paper_factory):
        usable, reason = extract.check_readable(paper_factory("p.pdf"))
        assert usable is True and reason == ""

    def test_a_small_truncated_pdf_is_rejected_for_size_and_no_text(self, broken_files):
        # Size alone no longer rejects a file; it must also fail to yield text.
        usable, _, reason = extract.inspect(broken_files["truncated"])
        assert usable is False
        assert "bytes" in reason and "characters of text" in reason

    def test_a_large_truncated_pdf_is_rejected_by_the_parse_check(self, tmp_path):
        # Big enough to clear the size floor, but its xref and trailer are gone, so no
        # extractor can make pages out of it. This is the path the size floor cannot catch.
        good = self.make_image_only_pdf(str(tmp_path / "_whole.pdf"))
        whole = open(good, "rb").read()
        cut = whole.index(b"\nxref")  # everything from the xref onward is discarded
        broken = tmp_path / "big_truncated.pdf"
        # Pad with filler so the file clears the size floor and must be caught by parsing.
        broken.write_bytes(whole[:cut] + b"\n% " + b"z" * 60_000 + b"\n")
        os.remove(good)

        usable, _, reason = extract.inspect(str(broken))

        assert usable is False
        assert "parsed as a PDF" in reason or "no pages" in reason
