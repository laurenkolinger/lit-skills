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

    def test_the_tags_tab_is_a_plain_vocabulary_reference(self, tmp_path, library):
        tab = self.build(tmp_path, library)[sheet.TAGS_TITLE]
        assert [tab.cell(row=4, column=i).value for i in range(1, 4)] == ["facet", "tag", "papers"]
        assert tab.cell(row=4, column=4).value is None

    def test_the_link_cell_is_a_real_hyperlink(self, tmp_path, library):
        cell = self.build(tmp_path, library)[sheet.SHEET_TITLE].cell(row=2, column=1)
        assert cell.value == sheet.LINK_TEXT and cell.hyperlink is not None

    def test_the_index_rows_are_still_short(self, tmp_path, library):
        tab = self.build(tmp_path, library)[sheet.SHEET_TITLE]
        assert {tab.row_dimensions[r].height for r in range(2, tab.max_row + 1)} == {sheet.DATA_ROW_HEIGHT}


class TestReadMeTab:
    """The tab explains itself to whoever opens the file cold."""

    def text(self, tmp_path, library):
        root, _ = library
        links.apply_links(root)
        tab = load_workbook(os.path.join(root, "lit_index.xlsx"))[sheet.README_TITLE]
        return " ".join(str(c.value) for row in tab.iter_rows() for c in row if c.value)

    def test_every_index_column_is_defined(self):
        from litkit import index as index_module

        defined = dict(sheet.COLUMN_DEFINITIONS)
        assert [c for c in index_module.COLUMNS if c not in defined] == []

    def test_no_definition_describes_a_column_that_does_not_exist(self):
        from litkit import index as index_module

        assert [c for c, _ in sheet.COLUMN_DEFINITIONS if c not in index_module.COLUMNS] == []

    def test_every_definition_is_one_short_line(self):
        long_ones = [(n, d) for n, d in sheet.COLUMN_DEFINITIONS if len(d.split()) > 12]
        assert long_ones == [], f"these definitions are too long: {long_ones}"

    def test_every_column_name_appears_on_the_tab(self, tmp_path, library):
        body = self.text(tmp_path, library)
        for name, _ in sheet.COLUMN_DEFINITIONS:
            assert name in body, f"{name} is not documented"

    def test_the_impact_labels_match_the_ones_the_code_assigns(self):
        from litkit import enrich

        documented = {label for label, _ in sheet.IMPACT_RULES}
        actual = {enrich.IMPACT_HIGH, enrich.IMPACT_WELL_CITED, enrich.IMPACT_STANDARD,
                  enrich.IMPACT_EMERGING, enrich.IMPACT_LOW, enrich.IMPACT_UNRATED}
        assert documented == actual

    def test_the_impact_thresholds_match_the_code(self, tmp_path, library):
        from litkit import enrich

        body = self.text(tmp_path, library)
        for value in (enrich.HIGH_IMPACT_TOTAL, enrich.HIGH_IMPACT_PER_YEAR,
                      enrich.WELL_CITED_TOTAL, enrich.WELL_CITED_PER_YEAR,
                      enrich.STANDARD_TOTAL):
            assert str(value) in body

    def test_it_says_unrated_is_not_a_low_score(self, tmp_path, library):
        body = self.text(tmp_path, library).lower()
        assert "unrated means the count is unknown" in body

    def test_it_explains_the_key_and_warns_against_renaming(self, tmp_path, library):
        body = self.text(tmp_path, library).lower()
        assert "first author surname" in body and "rename" in body

    def test_it_names_all_three_setup_requirements(self, tmp_path, library):
        body = self.text(tmp_path, library)
        assert "Claude Code subscription" in body
        assert "Google Drive for Desktop" in body
        assert "Two skills installed" in body

    def test_it_gives_both_install_routes(self, tmp_path, library):
        body = self.text(tmp_path, library)
        assert "github.com/laurenkolinger/lit-skills" in body
        assert "install.sh" in body
        assert "git clone" in body

    def test_it_names_both_skills(self, tmp_path, library):
        body = self.text(tmp_path, library)
        assert "lit-search" in body and "lit-ingest" in body

    def test_it_points_at_the_agent_with_real_example_questions(self, tmp_path, library):
        body = self.text(tmp_path, library)
        assert "Ask Claude" in body
        assert body.count("?") >= 4, "there should be several example questions"

    def test_it_does_not_teach_hand_filtering(self, tmp_path, library):
        body = self.text(tmp_path, library)
        for banned in ("Text contains", "Custom formula", "filter arrow",
                       "Filter by condition", "ISNUMBER", "|AUV|"):
            assert banned not in body, f"still teaches hand filtering: {banned}"

    def test_it_warns_that_edits_here_are_overwritten(self, tmp_path, library):
        assert "overwrites" in self.text(tmp_path, library)

    def test_the_copy_stays_short_enough_to_read(self):
        blocks = sheet.readme_blocks(243)
        words = sum(len(str(b["left"]).split()) + len(str(b["text"]).split()) for b in blocks)
        assert words < 750, f"the Read me is {words} words, too long to scan"

    def test_house_style_no_em_dashes_and_american_spelling(self):
        body = " ".join(f"{b['left']} {b['text']}" for b in sheet.readme_blocks(243))
        assert "\u2014" not in body and chr(8212) not in body
        for british in ("favour", "colour", "behaviour", "analyse", "organise", "recognise",
                        "judgement", "centre", "labour", "modelling", "prioritise"):
            assert british not in body.lower(), f"British spelling: {british}"

    def test_no_promotional_or_filler_language(self):
        body = " ".join(f"{b['left']} {b['text']}" for b in sheet.readme_blocks(243)).lower()
        for word in ("seamless", "powerful", "robust", "crucial", "vital", "groundbreaking",
                     "leverage", "utilize", "reach out", "circle back", "it's worth noting"):
            assert word not in body, f"promotional or filler language: {word}"


class TestReadMeLayout:
    """The layout bug on 2026-09-16: prose sat in a 30 character column and was cut off."""

    def tab(self, tmp_path, library):
        root, _ = library
        links.apply_links(root)
        return load_workbook(os.path.join(root, "lit_index.xlsx"))[sheet.README_TITLE]

    def test_prose_spans_both_columns_so_it_is_never_clipped(self, tmp_path, library):
        tab = self.tab(tmp_path, library)
        merged = {str(r) for r in tab.merged_cells.ranges}
        assert merged, "prose rows must be merged across A and B"

    def test_a_long_prose_line_is_in_a_merged_row(self, tmp_path, library):
        tab = self.tab(tmp_path, library)
        merged_rows = {r.min_row for r in tab.merged_cells.ranges}
        long_rows = [
            c.row for row in tab.iter_rows() for c in row
            if c.column == 1 and isinstance(c.value, str) and len(c.value) > 90
        ]
        unmerged = [r for r in long_rows if r not in merged_rows]
        assert unmerged == [], f"long text left in the narrow column at rows {unmerged}"

    def test_every_prose_row_is_tall_enough_for_its_text(self, tmp_path, library):
        tab = self.tab(tmp_path, library)
        for row in tab.iter_rows():
            cell = row[0]
            if isinstance(cell.value, str) and len(cell.value) > 120:
                needed = sheet.README_LINE_HEIGHT * (1 + len(cell.value) // sheet.README_CHARS_PER_LINE)
                assert tab.row_dimensions[cell.row].height >= needed * 0.9

    def test_everything_wraps_rather_than_overflowing(self, tmp_path, library):
        tab = self.tab(tmp_path, library)
        for row in tab.iter_rows():
            for cell in row:
                if cell.value:
                    assert cell.alignment.wrap_text, f"{cell.coordinate} does not wrap"

    def test_the_text_column_is_wide(self, tmp_path, library):
        tab = self.tab(tmp_path, library)
        assert tab.column_dimensions["B"].width >= 90

    def test_gridlines_are_off_so_it_reads_as_a_document(self, tmp_path, library):
        assert self.tab(tmp_path, library).sheet_view.showGridLines is False


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
