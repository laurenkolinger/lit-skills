"""Unit tests for litkit.naming."""

import pytest

from litkit import naming


class TestFirstSurname:
    def test_semicolon_separated_comma_form(self):
        authors = "Smith, Tyler B.; Brandt, Marilyn E.; Canals, Miguel"
        assert naming.first_surname(authors) == "Smith"

    def test_natural_order_with_initial(self):
        assert naming.first_surname("Tyler B. Smith and Marilyn Brandt") == "Smith"

    def test_hyphenated_surname_loses_the_hyphen(self):
        assert naming.first_surname("Alvarez-Filip, Lorenzo") == "AlvarezFilip"

    def test_accents_fold_to_ascii(self):
        assert naming.first_surname("Álvarez-Filip, Lorenzo") == "AlvarezFilip"
        assert naming.first_surname("Grémillet, David") == "Gremillet"

    def test_list_input(self):
        assert naming.first_surname(["Kobara, Shinichi", "Heyman, William"]) == "Kobara"

    def test_all_initials_falls_back_to_last_token(self):
        assert naming.first_surname("J. R. R.") == "R"

    @pytest.mark.parametrize("empty", [None, "", "   ", [], ";;;"])
    def test_missing_author_is_anon(self, empty):
        assert naming.first_surname(empty) == "Anon"

    def test_cjk_surname_has_no_ascii_and_becomes_anon(self):
        assert naming.first_surname("张伟") == "Anon"


class TestCleanYear:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (2019, "2019"),
            ("2019", "2019"),
            ("2019-04-01", "2019"),
            ("Published 1994 in Science", "1994"),
            ("1699", "nd"),
            ("2101", "nd"),
            (None, "nd"),
            ("", "nd"),
            ("no date", "nd"),
            ("volume 12", "nd"),
        ],
    )
    def test_year_normalization(self, raw, expected):
        assert naming.clean_year(raw) == expected


class TestShortTitle:
    def test_drops_stopwords_and_short_words(self):
        title = "Flattening of Caribbean coral reefs: region-wide declines in architectural complexity"
        assert naming.short_title(title) == "FlatteningCaribbeanCoralReefsRegion"

    def test_respects_max_words(self):
        title = "Growth estimates of Caribbean reef sponges on a shipwreck using 3D photogrammetry"
        assert naming.short_title(title, max_words=3) == "GrowthEstimatesCaribbean"

    def test_title_of_only_stopwords_still_yields_a_name(self):
        assert naming.short_title("Of the and to") == "OfTheAndTo"

    def test_punctuation_and_quotes_are_removed(self):
        assert naming.short_title('"Doing" science versus being a scientist!') == "DoingScienceVersusScientist"

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_missing_title_is_untitled(self, empty):
        assert naming.short_title(empty) == "Untitled"

    def test_cjk_title_has_no_ascii_and_becomes_untitled(self):
        assert naming.short_title("珊瑚礁监测") == "Untitled"

    def test_very_long_title_is_truncated_to_max_words(self):
        title = " ".join(f"word{n}" for n in range(300))
        result = naming.short_title(title)
        assert result.count("Word") == naming.MAX_TITLE_WORDS

    def test_numbers_survive(self):
        assert naming.short_title("3D photogrammetry of reefs") == "3dPhotogrammetryReefs"


class TestBuildKey:
    def test_assembles_three_parts(self):
        key = naming.build_key("Olinger, Lauren K.", 2019, "Growth estimates of Caribbean reef sponges")
        assert key == "Olinger_2019_GrowthEstimatesCaribbeanReefSponges"

    def test_all_missing_still_produces_a_key(self):
        assert naming.build_key(None, None, None) == "Anon_nd_Untitled"

    def test_key_contains_no_filesystem_hostile_characters(self):
        key = naming.build_key("O'Brien, Seán/Sean", "2020", "A/B testing: 50% better?")
        assert not set(key) & set('/\\:*?"<>|')


class TestDisambiguate:
    def test_free_key_is_returned_unchanged(self):
        assert naming.disambiguate("Smith_2016_Reefs", set()) == "Smith_2016_Reefs"

    def test_first_collision_gets_b(self):
        assert naming.disambiguate("Smith_2016_Reefs", {"Smith_2016_Reefs"}) == "Smith_2016_Reefs_b"

    def test_second_collision_gets_c(self):
        taken = {"Smith_2016_Reefs", "Smith_2016_Reefs_b"}
        assert naming.disambiguate("Smith_2016_Reefs", taken) == "Smith_2016_Reefs_c"

    def test_exhausted_letters_fall_back_to_numbers(self):
        base = "Smith_2016_Reefs"
        taken = {base} | {f"{base}_{chr(o)}" for o in range(ord("b"), ord("z") + 1)}
        assert naming.disambiguate(base, taken) == f"{base}_2"

    def test_never_returns_a_taken_key_over_many_collisions(self):
        base = "Smith_2016_Reefs"
        taken = set()
        for _ in range(200):
            key = naming.disambiguate(base, taken)
            assert key not in taken
            taken.add(key)


class TestNormalizeTitle:
    def test_case_and_punctuation_are_ignored(self):
        a = naming.normalize_title("Flattening of Caribbean Coral Reefs: Region-wide declines")
        b = naming.normalize_title("flattening of caribbean coral reefs   region wide declines")
        assert a == b

    def test_accents_fold(self):
        assert naming.normalize_title("Café reefs") == naming.normalize_title("Cafe reefs")

    @pytest.mark.parametrize("empty", [None, "", "   ", "!!!"])
    def test_unusable_titles_normalize_to_empty(self, empty):
        assert naming.normalize_title(empty) == ""


class TestBuildFilename:
    def test_appends_pdf(self):
        assert naming.build_filename("Smith_2016_Reefs") == "Smith_2016_Reefs.pdf"
