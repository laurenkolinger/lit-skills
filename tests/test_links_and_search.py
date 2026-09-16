"""Tests for the link column, the derived tags cell, and the Search and Tags tabs."""

import json
import os

import pytest
from openpyxl import load_workbook

from conftest import make_paper
from litkit import index, links, sheet


class TestLinkBuilding:
    def test_a_known_id_becomes_a_direct_file_link(self):
        assert links.file_link("1AbC") == "https://drive.google.com/file/d/1AbC/view"

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_no_id_yields_no_direct_link(self, empty):
        assert links.file_link(empty) == ""

    def test_the_fallback_searches_drive_for_the_filename(self):
        url = links.search_link("Smith_2016_CoralCover.pdf")
        assert url.startswith("https://drive.google.com/drive/search?q=")
        assert "Smith_2016_CoralCover" in url
        assert ".pdf" not in url

    def test_a_filename_with_spaces_is_url_encoded(self):
        assert " " not in links.search_link("A paper with spaces.pdf")

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_no_filename_yields_no_fallback(self, empty):
        assert links.search_link(empty) == ""

    def test_a_missing_ids_file_is_not_fatal(self, tmp_path):
        assert links.load_ids(str(tmp_path / "nope.json")) == {}

    def test_a_corrupt_ids_file_is_not_fatal(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert links.load_ids(str(bad)) == {}

    def test_an_ids_file_holding_a_list_is_rejected(self, tmp_path):
        bad = tmp_path / "list.json"
        bad.write_text('["a", "b"]')
        assert links.load_ids(str(bad)) == {}


@pytest.fixture
def library(tmp_path):
    root = str(tmp_path / "Lit")
    paths = index.ensure_layout(root)
    for name in ("A_2016_X.pdf", "B_2017_Y.pdf"):
        make_paper(os.path.join(paths["pdfs"], name), "T", "A", 2016, "10.1/x", "abs")
    index.write_index(paths["index"], [
        {"key": "A_2016_X", "filename": "A_2016_X.pdf", "title": "Reef cover over time",
         "topic_tags": "coral reef, monitoring", "method_tags": "AUV", "region_tags": "USVI",
         "vicar_relevance": "reef research", "citations": "40", "year": "2016"},
        {"key": "B_2017_Y", "filename": "B_2017_Y.pdf", "title": "Grouper spawning",
         "topic_tags": "fish spawning aggregation", "region_tags": "USVI",
         "vicar_relevance": "reef research", "citations": "9", "year": "2017"},
    ])
    return root, paths


class TestApplyLinks:
    def test_known_ids_become_direct_links(self, tmp_path, library):
        root, paths = library
        ids = tmp_path / "ids.json"
        ids.write_text(json.dumps({"A_2016_X.pdf": "ID1", "B_2017_Y.pdf": "ID2"}))
        result = links.apply_links(root, ids_path=str(ids))
        assert result["direct"] == 2 and result["fallback"] == 0
        rows = index.read_index(paths["index"])
        assert all(r["link"].startswith("https://drive.google.com/file/d/") for r in rows)

    def test_an_unknown_id_still_gets_a_working_link(self, tmp_path, library):
        root, paths = library
        ids = tmp_path / "ids.json"
        ids.write_text(json.dumps({"A_2016_X.pdf": "ID1"}))
        result = links.apply_links(root, ids_path=str(ids))
        assert result["direct"] == 1 and result["fallback"] == 1
        rows = {r["key"]: r for r in index.read_index(paths["index"])}
        assert rows["B_2017_Y"]["link"]
        assert "search" in rows["B_2017_Y"]["link"]

    def test_every_row_ends_up_with_a_link(self, library):
        root, paths = library
        links.apply_links(root)
        assert all(r["link"] for r in index.read_index(paths["index"]))

    def test_dry_run_changes_nothing(self, library):
        root, paths = library
        before = open(paths["index"], "rb").read()
        links.apply_links(root, dry_run=True)
        assert open(paths["index"], "rb").read() == before


class TestTagsAllCell:
    def test_it_is_derived_on_write_and_wrapped_in_pipes(self, library):
        root, paths = library
        row = {r["key"]: r for r in index.read_index(paths["index"])}["A_2016_X"]
        assert row["tags_all"] == "|coral reef|monitoring|AUV|USVI|reef research|"

    def test_a_whole_tag_matches_but_a_fragment_does_not(self, library):
        root, paths = library
        cell = {r["key"]: r for r in index.read_index(paths["index"])}["A_2016_X"]["tags_all"]
        assert "|AUV|" in cell          # the tag itself
        assert "|AU|" not in cell       # a fragment of it must not match

    def test_it_cannot_drift_from_the_facets(self, library):
        root, paths = library
        rows = index.read_index(paths["index"])
        for r in rows:
            if r["key"] == "A_2016_X":
                r["topic_tags"] = "bleaching"
                r["tags_all"] = "|stale|nonsense|"
        index.write_index(paths["index"], rows)
        refreshed = {r["key"]: r for r in index.read_index(paths["index"])}["A_2016_X"]
        assert "stale" not in refreshed["tags_all"]
        assert "|bleaching|" in refreshed["tags_all"]

    def test_a_row_with_no_tags_has_an_empty_cell(self):
        assert index.build_tags_all({"topic_tags": "", "method_tags": None}) == ""

    def test_a_tag_repeated_across_facets_appears_once(self):
        cell = index.build_tags_all({"topic_tags": "reef fish", "taxa_tags": "reef fish"})
        assert cell == "|reef fish|"


class TestWorkbookTabs:
    def build(self, tmp_path, library):
        root, _ = library
        links.apply_links(root)
        return load_workbook(os.path.join(root, "lit_index.xlsx"))

    def test_the_three_tabs_are_readme_index_tags_in_that_order(self, tmp_path, library):
        assert self.build(tmp_path, library).sheetnames == [
            sheet.README_TITLE, sheet.SHEET_TITLE, sheet.TAGS_TITLE
        ]

    def test_the_index_header_is_exactly_the_csv_columns(self, tmp_path, library):
        from litkit import index as index_module

        tab = self.build(tmp_path, library)[sheet.SHEET_TITLE]
        assert [c.value for c in tab[1]] == index_module.COLUMNS

    def test_the_tags_tab_counts_each_tag(self, tmp_path, library):
        tab = self.build(tmp_path, library)[sheet.TAGS_TITLE]
        found = {r[1]: r[2] for r in tab.iter_rows(min_row=5, values_only=True) if r[1]}
        assert found["USVI"] == 2 and found["AUV"] == 1 and found["reef research"] == 2

    def test_the_tags_tab_gives_a_paste_ready_filter_string(self, tmp_path, library):
        tab = self.build(tmp_path, library)[sheet.TAGS_TITLE]
        strings = {r[1]: r[3] for r in tab.iter_rows(min_row=5, values_only=True) if r[1]}
        assert strings["AUV"] == "|AUV|"

    def test_the_link_cell_is_a_real_hyperlink(self, tmp_path, library):
        cell = self.build(tmp_path, library)[sheet.SHEET_TITLE].cell(row=2, column=1)
        assert cell.value == sheet.LINK_TEXT and cell.hyperlink is not None

    def test_the_index_rows_are_still_short(self, tmp_path, library):
        tab = self.build(tmp_path, library)[sheet.SHEET_TITLE]
        assert {tab.row_dimensions[r].height for r in range(2, tab.max_row + 1)} == {sheet.DATA_ROW_HEIGHT}


class TestReadMeTab:
    """The spreadsheet has to explain itself to someone who was not here when it was built."""

    def text(self, tmp_path, library):
        root, _ = library
        links.apply_links(root)
        tab = load_workbook(os.path.join(root, "lit_index.xlsx"))[sheet.README_TITLE]
        return " ".join(str(c.value) for row in tab.iter_rows() for c in row if c.value)

    def test_every_index_column_is_defined(self, tmp_path, library):
        from litkit import index as index_module

        defined = dict(sheet.COLUMN_DEFINITIONS)
        assert [c for c in index_module.COLUMNS if c not in defined] == []

    def test_no_definition_describes_a_column_that_does_not_exist(self):
        from litkit import index as index_module

        assert [c for c, _ in sheet.COLUMN_DEFINITIONS if c not in index_module.COLUMNS] == []

    def test_every_definition_appears_on_the_tab(self, tmp_path, library):
        body = self.text(tmp_path, library)
        for name, _ in sheet.COLUMN_DEFINITIONS:
            assert name in body, f"{name} is not documented on the Read me tab"

    def test_every_impact_label_and_its_rule_are_stated(self, tmp_path, library):
        body = self.text(tmp_path, library)
        for label, rule in sheet.IMPACT_RULES:
            assert label in body
            assert rule.split(",")[0][:24] in body

    def test_the_impact_labels_match_the_ones_the_code_assigns(self):
        from litkit import enrich

        documented = {label for label, _ in sheet.IMPACT_RULES}
        actual = {enrich.IMPACT_HIGH, enrich.IMPACT_WELL_CITED, enrich.IMPACT_STANDARD,
                  enrich.IMPACT_EMERGING, enrich.IMPACT_LOW, enrich.IMPACT_UNRATED}
        assert documented == actual

    def test_the_impact_thresholds_match_the_code(self, tmp_path, library):
        from litkit import enrich

        body = self.text(tmp_path, library)
        assert str(enrich.HIGH_IMPACT_TOTAL) in body
        assert str(enrich.HIGH_IMPACT_PER_YEAR) in body
        assert str(enrich.WELL_CITED_TOTAL) in body
        assert str(enrich.WELL_CITED_PER_YEAR) in body
        assert str(enrich.STANDARD_TOTAL) in body

    def test_it_explains_what_a_key_is_and_not_to_rename_files(self, tmp_path, library):
        body = self.text(tmp_path, library)
        assert "FirstAuthor_Year_ShortTitle" in body
        assert "rename" in body.lower()

    def test_it_states_both_setup_requirements(self, tmp_path, library):
        body = self.text(tmp_path, library)
        assert "Google Drive for Desktop" in body
        assert "Claude Code account" in body

    def test_it_gives_the_skill_install_route(self, tmp_path, library):
        body = self.text(tmp_path, library)
        assert "github.com/laurenkolinger/lit-skills" in body
        assert "install.sh" in body
        assert "lit-ingest" in body and "lit-search" in body

    def test_it_explains_how_to_filter_by_one_tag_and_by_two(self, tmp_path, library):
        body = self.text(tmp_path, library)
        assert "Text contains" in body
        assert "Custom formula" in body
        assert "|AUV|" in body

    def test_it_warns_that_edits_here_are_overwritten(self, tmp_path, library):
        body = self.text(tmp_path, library)
        assert "source of truth" in body and "overwritten" in body

    def test_it_says_unrated_is_not_a_low_score(self, tmp_path, library):
        assert "unrated is not a low score" in self.text(tmp_path, library)


class TestNoFormulasAtAll:
    """On 2026-09-16 a FILTER formula was gutted by Google Sheets into __xludf.DUMMYFUNCTION
    when it opened the xlsx. Filtering now happens in the spreadsheet's own filter UI, which
    cannot break on a file round trip, so the workbook carries no formulas at all."""

    def test_the_workbook_contains_no_formula_cells(self, tmp_path, library):
        root, _ = library
        links.apply_links(root)
        wb = load_workbook(os.path.join(root, "lit_index.xlsx"))
        offenders = [
            (name, cell.coordinate, cell.value)
            for name in wb.sheetnames
            for row in wb[name].iter_rows()
            for cell in row
            if isinstance(cell.value, str) and cell.value.startswith("=")
        ]
        assert offenders == [], f"formulas found: {offenders[:3]}"
