"""Unit tests for litkit.enrich. No test here touches the network."""

import datetime

import pytest

from litkit import enrich

TODAY = datetime.date(2026, 9, 15)


class TestCitationsPerYear:
    def test_published_this_year_uses_a_one_year_floor(self):
        assert enrich.citations_per_year(10, 2026, today=TODAY) == 10.0

    def test_older_paper_divides_by_inclusive_age(self):
        # 2017 to 2026 inclusive is 10 years.
        assert enrich.citations_per_year(152, 2017, today=TODAY) == 15.2

    def test_future_year_never_divides_by_zero_or_negative(self):
        assert enrich.citations_per_year(4, 2030, today=TODAY) == 4.0

    @pytest.mark.parametrize("citations,year", [(None, 2020), ("", 2020), (5, None), (5, "")])
    def test_missing_input_returns_none(self, citations, year):
        assert enrich.citations_per_year(citations, year, today=TODAY) is None

    @pytest.mark.parametrize("citations,year", [("many", 2020), (5, "n.d."), ("", "")])
    def test_unparseable_input_returns_none(self, citations, year):
        assert enrich.citations_per_year(citations, year, today=TODAY) is None


class TestImpactLabel:
    def test_high_impact_by_total(self):
        assert enrich.impact_label(500, 1994, today=TODAY) == enrich.IMPACT_HIGH

    def test_just_below_high_total_is_well_cited(self):
        assert enrich.impact_label(499, 1994, today=TODAY) == enrich.IMPACT_WELL_CITED

    def test_high_impact_by_rate_on_a_recent_paper(self):
        # 2025, 80 citations over an age of 2 is 40 per year exactly.
        assert enrich.impact_label(80, 2025, today=TODAY) == enrich.IMPACT_HIGH

    def test_well_cited_by_total(self):
        assert enrich.impact_label(100, 2005, today=TODAY) == enrich.IMPACT_WELL_CITED

    def test_well_cited_by_rate(self):
        # 2025, 30 citations over an age of 2 is 15 per year exactly.
        assert enrich.impact_label(30, 2025, today=TODAY) == enrich.IMPACT_WELL_CITED

    def test_standard_at_the_boundary(self):
        assert enrich.impact_label(10, 2010, today=TODAY) == enrich.IMPACT_STANDARD

    def test_emerging_is_recent_and_lightly_cited(self):
        assert enrich.impact_label(3, 2025, today=TODAY) == enrich.IMPACT_EMERGING

    def test_emerging_window_closes_after_three_years(self):
        assert enrich.impact_label(3, 2023, today=TODAY) == enrich.IMPACT_EMERGING
        assert enrich.impact_label(3, 2022, today=TODAY) == enrich.IMPACT_LOW

    def test_old_and_uncited_is_low(self):
        assert enrich.impact_label(1, 1998, today=TODAY) == enrich.IMPACT_LOW

    def test_zero_citations_on_an_old_paper_is_low(self):
        assert enrich.impact_label(0, 1998, today=TODAY) == enrich.IMPACT_LOW

    @pytest.mark.parametrize("citations", [None, "", "lots", object()])
    def test_missing_or_bad_count_is_unrated(self, citations):
        assert enrich.impact_label(citations, 2020, today=TODAY) == enrich.IMPACT_UNRATED

    def test_negative_count_is_unrated_rather_than_low(self):
        assert enrich.impact_label(-5, 2020, today=TODAY) == enrich.IMPACT_UNRATED

    def test_unknown_year_with_a_big_count_still_reads_as_high(self):
        assert enrich.impact_label(900, None, today=TODAY) == enrich.IMPACT_HIGH

    def test_unknown_year_with_a_small_count_is_low_not_emerging(self):
        assert enrich.impact_label(2, None, today=TODAY) == enrich.IMPACT_LOW


class TestTitleSimilarity:
    def test_identical_titles_score_one(self):
        assert enrich.title_similarity("Coral reef resilience", "coral REEF resilience") == 1.0

    def test_unrelated_titles_score_zero(self):
        assert enrich.title_similarity("Coral reefs", "Fiscal policy") == 0.0

    @pytest.mark.parametrize("left,right", [(None, "x"), ("x", None), ("", ""), ("!!!", "???")])
    def test_missing_input_scores_zero(self, left, right):
        assert enrich.title_similarity(left, right) == 0.0

    def test_partial_overlap_lands_between(self):
        score = enrich.title_similarity(
            "Growth estimates of Caribbean reef sponges",
            "Growth estimates of Caribbean reef sponges on a shipwreck",
        )
        assert 0.0 < score < 1.0


class TestParseWork:
    def test_none_yields_blank_fields_not_an_error(self):
        parsed = enrich.parse_work(None)
        assert parsed["title"] == ""
        assert parsed["citations"] == ""

    def test_flattens_a_realistic_record(self):
        work = {
            "display_name": "Growth estimates of Caribbean reef sponges",
            "publication_year": 2019,
            "cited_by_count": 41,
            "doi": "https://doi.org/10.1038/s41598-019-54681-2",
            "authorships": [
                {"author": {"display_name": "Lauren K. Olinger"}},
                {"author": {"display_name": "Joseph R. Pawlik"}},
            ],
            "primary_location": {
                "source": {"display_name": "Scientific Reports"},
                "landing_page_url": "https://example.org/paper",
            },
            "topics": [{"display_name": "Coral Reef Ecology"}],
        }
        parsed = enrich.parse_work(work)
        assert parsed["first_author"] == "Lauren K. Olinger"
        assert parsed["authors"] == "Lauren K. Olinger; Joseph R. Pawlik"
        assert parsed["journal"] == "Scientific Reports"
        assert parsed["doi"] == "10.1038/s41598-019-54681-2"
        assert parsed["citations"] == 41
        assert parsed["openalex_topics"] == "Coral Reef Ecology"

    def test_missing_authorships_and_location_do_not_raise(self):
        parsed = enrich.parse_work({"display_name": "A paper", "publication_year": 2020})
        assert parsed["authors"] == ""
        assert parsed["journal"] == ""

    def test_falls_back_to_doi_url_when_no_landing_page(self):
        parsed = enrich.parse_work({"doi": "https://doi.org/10.1/x", "primary_location": {}})
        assert parsed["url"] == "https://doi.org/10.1/x"


class TestFetchGuards:
    """These prove the fetchers refuse bad input before any request is made."""

    @pytest.mark.parametrize("doi", [None, "", "not-a-doi", "12.345/x", "   "])
    def test_bad_doi_returns_none_without_a_request(self, doi):
        assert enrich.fetch_by_doi(doi) is None

    @pytest.mark.parametrize("title", [None, "", "short", "tiny title"])
    def test_short_title_returns_none_without_a_request(self, title):
        assert enrich.fetch_by_title(title) is None


class TestEnrichMerge:
    def test_openalex_values_win_and_extracted_values_fill_gaps(self, monkeypatch):
        monkeypatch.setattr(
            enrich,
            "fetch_by_doi",
            lambda doi: {
                "display_name": "Canonical title",
                "publication_year": 2019,
                "cited_by_count": 41,
                "doi": "https://doi.org/10.1/x",
                "authorships": [{"author": {"display_name": "A Author"}}],
                "primary_location": {"source": {"display_name": "A Journal"}},
            },
        )
        record = {"title": "messy ocr title", "doi": "10.1/x", "year": "", "abstract": "kept"}
        merged = enrich.enrich(record, today=TODAY)
        assert merged["title"] == "Canonical title"
        assert merged["year"] == "2019"
        assert merged["citations"] == 41
        # 41 citations spread over 2019 to 2026 is 5.1 per year, which is standard, not well cited.
        assert merged["citations_per_year"] == 5.1
        assert merged["impact"] == enrich.IMPACT_STANDARD
        assert merged["citations_retrieved"] == "2026-09-15"
        assert merged["abstract"] == "kept"
        assert merged["matched_openalex"] is True

    def test_no_match_keeps_extracted_values_and_leaves_citations_empty(self, monkeypatch):
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: None)
        monkeypatch.setattr(enrich, "fetch_by_title", lambda title, year=None: None)
        record = {"title": "An unindexed report", "doi": "", "year": "2021"}
        merged = enrich.enrich(record, today=TODAY)
        assert merged["title"] == "An unindexed report"
        assert merged["year"] == "2021"
        assert merged["citations"] == ""
        assert merged["citations_retrieved"] == ""
        assert merged["impact"] == enrich.IMPACT_UNRATED
        assert merged["matched_openalex"] is False
