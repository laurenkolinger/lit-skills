"""Tests for rejecting production junk in PDF metadata.

Every string in TestRealJunkFromTheLibrary came out of a real file in the VICAR library on
2026-09-15, where it had been written into /Title or /Author by publishing software and had
corrupted the row key.
"""

import os

import pytest

from litkit import extract


class TestRealJunkFromTheLibrary:
    @pytest.mark.parametrize(
        "junk",
        [
            "ecol-90-02-19 506..516",
            "ecap-16-02-22 747..769",
            "ecol_86_125.174_184.tp",
            "se230101893p",
            "Olinger_thesis_0105",
            "Olinger_dissertation_16nov",
            "decadalChangesProposal",
            "Overleaf Example",
            "millilLym.",
            "Galaxea,",
            "untitled",
            "Microsoft Word - draft3.doc",
            "manuscript.docx",
            "",
            "   ",
        ],
    )
    def test_junk_titles_are_rejected(self, junk):
        assert extract.looks_like_filename(junk) is True

    @pytest.mark.parametrize(
        "real",
        [
            "Flattening of Caribbean coral reefs: region-wide declines in architectural complexity",
            "Growth estimates of Caribbean reef sponges on a shipwreck using 3D photogrammetry",
            "A mathematical review of resilience in ecology",
            "Coral reef ecosystem functioning: eight core processes and the role of biodiversity",
            "Robot Goes Fishing: Rapid, High-Resolution Biological Hotspot Mapping in Coral Reefs",
            "3D photogrammetry reveals dynamics of stony coral tissue loss disease",
        ],
    )
    def test_real_titles_are_kept(self, real):
        assert extract.looks_like_filename(real) is False

    @pytest.mark.parametrize(
        "junk",
        [
            "mwoodard",
            "Client",
            "SPONGEGROWTH",
            "ArizonaPress",
            "HenleyBeach",
            "MatteoContini",
            "Microsoft Office User",
            "Adobe Acrobat",
            "PDF Writer",
            "",
        ],
    )
    def test_producer_strings_are_rejected_as_authors(self, junk):
        assert extract.looks_like_producer(junk) is True

    @pytest.mark.parametrize(
        "real",
        [
            "Smith, Tyler B.; Brandt, Marilyn E.",
            "Lauren K. Olinger",
            "Kobara, Shinichi",
            "Alvarez-Filip, Lorenzo and Dulvy, Nicholas K.",
        ],
    )
    def test_real_author_strings_are_kept(self, real):
        assert extract.looks_like_producer(real) is False


class TestTitleFallback:
    def test_extract_offers_a_second_title_candidate(self, paper_factory):
        record = extract.extract(paper_factory("p.pdf", title="A study of reef fish behavior"))
        assert "title_alt" in record

    def test_a_junk_metadata_title_does_not_reach_the_record(self, tmp_path, monkeypatch):
        from conftest import make_paper

        path = make_paper(
            str(tmp_path / "p.pdf"),
            "Coral cover decline across the Caribbean shelf",
            "Smith, Tyler B.",
            2016,
            "10.1/x",
            "An abstract about reef surveys and the change observed over time.",
        )
        monkeypatch.setattr(
            extract, "read_pdf_info", lambda _: {"title": "ecol-90-02-19 506..516", "author": "mwoodard", "year": ""}
        )
        record = extract.extract(path)
        assert "ecol-90" not in record["title"]
        assert record["authors"] != "mwoodard"

    def test_the_fallback_title_is_tried_when_the_first_fails(self, monkeypatch):
        from litkit import enrich

        tried = []

        def fake_title_fetch(title, year=None):
            tried.append(title)
            return {"display_name": "Found it", "cited_by_count": 5} if title == "second" else None

        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: None)
        monkeypatch.setattr(enrich, "fetch_by_title", fake_title_fetch)

        merged = enrich.enrich({"title": "first", "title_alt": "second", "doi": "", "year": "2020"})

        assert tried == ["first", "second"]
        assert merged["title"] == "Found it"

    def test_the_fallback_is_skipped_when_it_repeats_the_first(self, monkeypatch):
        from litkit import enrich

        tried = []
        monkeypatch.setattr(enrich, "fetch_by_doi", lambda doi: None)
        monkeypatch.setattr(
            enrich, "fetch_by_title", lambda title, year=None: tried.append(title) or None
        )

        enrich.enrich({"title": "same", "title_alt": "same", "doi": "", "year": "2020"})

        assert tried == ["same"]


class TestRunningHeads:
    @pytest.mark.parametrize(
        "head",
        [
            "Ecology, 90(2), 2009, pp. 506-516 by the Ecological Society of America",
            "Ecological Applications, 16(2), 2006, pp. 747-769",
            "Ecology, 86(1), 2005, pp. 174-184",
            "AN ABSTRACT OF THE THESIS OF Lauren Olinger for the degree of Master of Science",
            "PROJECT DESCRIPTION -- Decadal changes of Caribbean fore-reef benthos",
            "arXiv:2412.08228v1 11 Dec 2024",
            "Downloaded from https://example.org on 12 March",
            "All rights reserved",
        ],
    )
    def test_running_heads_are_rejected(self, head):
        assert extract.looks_like_running_head(head) is True

    @pytest.mark.parametrize(
        "real",
        [
            "Status of Multi-Species Spawning Aggregations in Belize",
            "Sponge Growth, Feeding Ecology, and Carbon Cycling on Caribbean Coral Reefs",
            "Flattening of Caribbean coral reefs: region-wide declines in complexity",
            "Coral reef ecosystem functioning: eight core processes",
            "Robot Goes Fishing: Rapid, High-Resolution Biological Hotspot Mapping",
        ],
    )
    def test_real_titles_pass(self, real):
        assert extract.looks_like_running_head(real) is False

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_empty_input_is_not_a_running_head(self, empty):
        assert extract.looks_like_running_head(empty) is False

    def test_repair_refuses_to_install_a_running_head_as_a_title(self):
        from litkit import repair

        assert repair.better_title(
            "ecol-90-02-19 506..516",
            "Ecology, 90(2), 2009, pp. 506-516 by the Ecological Society of America",
        ) is False

    def test_a_producer_author_makes_a_row_suspect(self):
        from litkit import repair

        suspect, reason = repair.is_suspect(
            {"title": "Coral reef ecosystem functioning and biodiversity here", "authors": "mwoodard"}
        )
        assert suspect is True and "software" in reason

    def test_repair_leaves_a_producer_author_alone_when_offline(self, tmp_path):
        import os
        from conftest import make_paper
        from litkit import index, repair

        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        make_paper(
            os.path.join(paths["pdfs"], "mwoodard_2016_X.pdf"),
            "Coral cover decline across the Caribbean shelf",
            "Smith, Tyler B.; Brandt, Marilyn E.", 2016, "10.1/x",
            "An abstract about reef surveys and observed change.",
        )
        index.write_index(paths["index"], [{
            "key": "mwoodard_2016_X", "filename": "mwoodard_2016_X.pdf",
            "title": "Coral cover decline across the Caribbean shelf",
            "authors": "mwoodard", "first_author": "mwoodard", "year": "2016",
        }])

        repair.repair(root, use_network=False)

        # Offline, the row keeps its junk author rather than gaining a guessed one. Authors
        # change only on an OpenAlex match; see TestAuthorsOnlyChangeOnAuthority.
        row = index.read_index(paths["index"])[0]
        assert row["authors"] == "mwoodard"
        assert repair.is_suspect(row)[0] is True


class TestProducerRegexHasWordBoundaries:
    """Real surnames contain the substrings the producer filter looks for."""

    @pytest.mark.parametrize(
        "authors",
        [
            "Aaron O'Dea; Mauro Lepore; Marguerite A. Toscano; Erin Dillon",   # Toscano holds "scan"
            "Oriol Prat-Bayarri; Marco Francescangeli; Joana Prat Farran",     # holds "scan"
            "Peter J. Edmunds; Howard R. Lasker",                              # Howard holds "howard"
            "Kevin Gross; Peter J. Edmunds",
            "Nystrom, Magnus; Folke, Carl; Moberg, Fredrik",
            "Wordsworth, Anna; Pressley, Ben",                                 # holds "word" and "press"
        ],
    )
    def test_real_author_lists_survive(self, authors):
        assert extract.looks_like_producer(authors) is False

    @pytest.mark.parametrize(
        "junk",
        [
            "Microsoft Office User",
            "Adobe Acrobat 9",
            "PDF Writer",
            "Univ. of Arizona Press",
            "Scanned Document",
        ],
    )
    def test_actual_producers_are_still_caught(self, junk):
        assert extract.looks_like_producer(junk) is True


class TestAffiliationBlocks:
    """An affiliation carries commas and spaces, so the shape test alone lets it through."""

    @pytest.mark.parametrize(
        "junk",
        [
            "{ Department of Computer Science and Engineering, Scripps Institution of Oceanography}",
            "Department of Ecology, Evolution and Marine Biology, University of California",
            "Marine Spatial Ecology Lab, School of BioSciences, University of Exeter",
            "Biomathematics Program, North Carolina State University, Raleigh",
            "obeijbom@ucsd.edu, kriegman@ucsd.edu",
            "(Institute of Marine Sciences, Santa Cruz)",
            "US Geological Survey Caribbean Field Station",
        ],
    )
    def test_affiliations_are_rejected_as_authors(self, junk):
        assert extract.looks_like_producer(junk) is True

    @pytest.mark.parametrize(
        "real",
        [
            "Oscar Beijbom; Peter J. Edmunds; David I. Kline; David Kriegman",
            "Smith, Tyler B.; Brandt, Marilyn E.",
            "Gross, Kevin; Edmunds, Peter J.",
            "Nemeth, Richard S.",
        ],
    )
    def test_real_author_lists_survive(self, real):
        assert extract.looks_like_producer(real) is False


class TestPmcCoverPages:
    """PubMed Central prepends a cover page whose text reads like a long title."""

    @pytest.mark.parametrize(
        "cover",
        [
            "NIH Public Access Author Manuscript Mar Ecol Prog Ser. Author manuscript",
            "Author Manuscript; available in PMC 2006 April 12",
            "Published in final edited form as: Mar Ecol Prog Ser. 2005 February ; 286: 81-97",
        ],
    )
    def test_pmc_cover_text_is_rejected(self, cover):
        assert extract.looks_like_running_head(cover) is True

    def test_the_real_title_under_the_cover_still_passes(self):
        real = "Population characteristics of a recovering US Virgin Islands red hind spawning aggregation following protection"
        assert extract.looks_like_running_head(real) is False


class TestDoiPlausibility:
    @pytest.mark.parametrize(
        "stub",
        ["10.1371/journal", "10.1038/s", "10.1234/", "10.1234/abc", "", None, "not-a-doi"],
    )
    def test_truncated_or_empty_dois_are_rejected(self, stub):
        assert extract.plausible_doi(stub) is False

    @pytest.mark.parametrize(
        "real",
        [
            "10.1371/journal.pone.0181396",
            "10.1038/s41598-019-54681-2",
            "10.3354/meps286081",
            "10.1890/14-0941.1",
            "10.1126/sciadv.abj2271",
        ],
    )
    def test_real_dois_are_accepted(self, real):
        assert extract.plausible_doi(real) is True

    def test_a_line_broken_doi_stub_is_not_returned(self):
        text = "Available at doi:10.1371/journal.\npone.0181396 and more text"
        # The stub before the break must not be handed to a lookup.
        assert extract.find_doi(text) != "10.1371/journal"

    def test_a_dryad_dataset_doi_is_skipped_in_favour_of_the_article_doi(self):
        text = "Data are archived at doi:10.5061/dryad.12j173m. The article is doi:10.1111/mec.15201."
        assert extract.find_doi(text) == "10.1111/mec.15201"

    def test_a_dryad_doi_alone_yields_nothing_by_default(self):
        assert extract.find_doi("Data: doi:10.5061/dryad.12j173m") == ""

    def test_a_dryad_doi_alone_is_returned_when_explicitly_allowed(self):
        text = "Data: doi:10.5061/dryad.12j173m"
        assert extract.find_doi(text, allow_data_repositories=True) == "10.5061/dryad.12j173m"

    def test_the_first_real_doi_still_wins(self):
        text = "doi:10.1038/s41598-019-54681-2 and later doi:10.3354/meps286081"
        assert extract.find_doi(text) == "10.1038/s41598-019-54681-2"


class TestMetadataMismatchGuard:
    def test_a_wrong_record_is_flagged(self, monkeypatch):
        from litkit import enrich

        monkeypatch.setattr(
            enrich, "fetch_by_doi",
            lambda doi: {"display_name": "Single-strand annealing between inverted DNA repeats",
                         "cited_by_count": 3, "publication_year": 2018},
        )
        merged = enrich.enrich({
            "title": "Altered juvenile fish communities associated with invasive Halophila stipulacea seagrass",
            "doi": "10.1371/journal", "year": "2017",
        })
        assert merged["metadata_mismatch"] is True
        assert "mismatch_detail" in " ".join(merged.keys())

    def test_a_matching_record_is_not_flagged(self, monkeypatch):
        from litkit import enrich

        monkeypatch.setattr(
            enrich, "fetch_by_doi",
            lambda doi: {"display_name": "Coral cover decline across the Caribbean shelf",
                         "cited_by_count": 3, "publication_year": 2016},
        )
        merged = enrich.enrich({
            "title": "Coral cover decline across the Caribbean shelf", "doi": "10.1/x", "year": "2016",
        })
        assert merged["metadata_mismatch"] is False

    def test_a_thin_own_title_does_not_trigger_a_false_flag(self, monkeypatch):
        from litkit import enrich

        monkeypatch.setattr(
            enrich, "fetch_by_doi",
            lambda doi: {"display_name": "A perfectly good canonical title", "cited_by_count": 1},
        )
        merged = enrich.enrich({"title": "Galaxea,", "doi": "10.1/x", "year": "2000"})
        assert merged["metadata_mismatch"] is False

    def test_the_flag_reaches_the_row_notes(self, tmp_path, monkeypatch):
        from conftest import make_paper
        from litkit import enrich, index, ingest

        root = str(tmp_path / "Lit")
        paths = index.ensure_layout(root)
        make_paper(
            os.path.join(paths["ingest"], "p.pdf"),
            "Altered juvenile fish communities in invasive seagrass habitats",
            "Olinger, Lauren K.", 2017, "10.1371/journal.pone.0181396",
            "We trapped juvenile fish across seagrass habitats and compared assemblages.",
        )
        monkeypatch.setattr(
            enrich, "fetch_by_doi",
            lambda doi: {"display_name": "Single-strand annealing between inverted DNA repeats",
                         "cited_by_count": 3, "publication_year": 2018},
        )
        ingest.run(root, use_network=True)
        row = index.read_index(paths["index"])[0]
        assert "metadata mismatch" in row["notes"]
