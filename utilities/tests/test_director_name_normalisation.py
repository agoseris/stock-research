"""
test_director_name_normalisation.py

Tests for the director name normalisation utility.

Run with:
    cd /home/agoseris/projects/stock-research
    scripts/venv/bin/python -m pytest utilities/tests/test_director_name_normalisation.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from director_name_normalisation import (
    normalise_director_name,
    names_match,
    normalise_for_storage,
)


# ---------------------------------------------------------------------------
# normalise_director_name — individual transformation tests
# ---------------------------------------------------------------------------

class TestNormaliseDirectorName:

    def test_standard_format(self):
        r = normalise_director_name("Stephanie Coxon")
        assert r["normalised"] == "stephanie coxon"
        assert r["abbreviated"] is False
        assert r["confidence"] == "high"
        assert r["format_detected"] == "standard"

    def test_middle_initial_stripped(self):
        r = normalise_director_name("James S. Metcalf")
        assert r["normalised"] == "james metcalf"
        assert r["abbreviated"] is False
        assert r["confidence"] == "high"
        assert "middle initial" in r["notes"]

    def test_surname_first_format(self):
        r = normalise_director_name("SMITH, John")
        assert r["normalised"] == "john smith"
        assert r["abbreviated"] is False
        assert r["confidence"] == "high"
        assert r["format_detected"] == "surname_first"
        assert "surname-first" in r["notes"]

    def test_surname_first_mixed_case(self):
        r = normalise_director_name("COXON, Stephanie")
        assert r["normalised"] == "stephanie coxon"
        assert r["abbreviated"] is False
        assert r["confidence"] == "high"

    def test_abbreviated_first_name(self):
        r = normalise_director_name("J. Low")
        assert r["normalised"] == "j low"
        assert r["abbreviated"] is True
        assert r["confidence"] == "low"
        assert "abbreviated" in r["notes"]

    def test_abbreviated_no_dot(self):
        r = normalise_director_name("J Low")
        assert r["normalised"] == "j low"
        assert r["abbreviated"] is True

    def test_honorific_dr(self):
        r = normalise_director_name("Dr. Jane Smith")
        assert r["normalised"] == "jane smith"
        assert r["abbreviated"] is False
        assert r["confidence"] == "high"
        assert "honorific" in r["notes"]

    def test_honorific_sir(self):
        r = normalise_director_name("Sir Martin Sorrell")
        assert r["normalised"] == "martin sorrell"
        assert "honorific" in r["notes"]

    def test_honorific_prof(self):
        r = normalise_director_name("Prof. Angela Davis")
        assert r["normalised"] == "angela davis"

    def test_double_barrelled_surname_preserved(self):
        r = normalise_director_name("Sarah Jones-Williams")
        assert r["normalised"] == "sarah jones-williams"
        assert r["abbreviated"] is False

    def test_inconsistent_spacing(self):
        r = normalise_director_name("James  Low")
        assert r["normalised"] == "james low"

    def test_all_caps_name(self):
        r = normalise_director_name("JANE SMITH")
        assert r["normalised"] == "jane smith"
        assert r["format_detected"] == "all_caps"

    def test_punctuation_stripped(self):
        # Dot after surname etc
        r = normalise_director_name("John Smith.")
        assert r["normalised"] == "john smith"

    def test_empty_string(self):
        r = normalise_director_name("")
        assert r["normalised"] == ""
        assert r["confidence"] == "low"

    def test_whitespace_only(self):
        r = normalise_director_name("   ")
        assert r["normalised"] == ""

    def test_middle_initial_without_dot(self):
        # "James B Smith" — B is middle initial between two words
        r = normalise_director_name("James B Smith")
        assert r["normalised"] == "james smith"
        assert "middle initial" in r["notes"]

    def test_multiple_honorifics(self):
        # Edge case: "Prof Dr Smith" — strip both
        r = normalise_director_name("Prof Dr Jane Smith")
        assert r["normalised"] == "jane smith"

    def test_kelly_baker(self):
        # From spec test cases
        r = normalise_director_name("Kelly Baker")
        assert r["normalised"] == "kelly baker"
        assert r["confidence"] == "high"


# ---------------------------------------------------------------------------
# names_match — spec test cases (all must pass)
# ---------------------------------------------------------------------------

class TestNamesMatchSpec:
    """
    Exact test cases from the design spec.
    All must pass before the utility is used in production.
    """

    @pytest.mark.parametrize("a,b,expected_match,expected_confidence", [
        ("Stephanie Coxon",       "Stephanie Coxon",          True,  "high"),
        ("Stephanie Coxon",       "COXON, Stephanie",         True,  "high"),
        ("James S. Metcalf",      "James Metcalf",            True,  "high"),
        ("J. Low",                "Jim Low",                  True,  "medium"),
        ("J. Low",                "James Low",                True,  "medium"),
        ("James Low",             "Jim Low",                  False, "low"),
        ("Kelly Baker",           "Kelly Baker",              True,  "high"),
        ("Dr. Jane Smith",        "Jane Smith",               True,  "high"),
        ("Sarah Jones-Williams",  "Sarah Jones Williams",     True,  "medium"),
        ("John Smith",            "Jane Smith",               False, "high"),
    ])
    def test_spec_cases(self, a, b, expected_match, expected_confidence):
        result = names_match(a, b)
        assert result["match"] == expected_match, (
            f"names_match({a!r}, {b!r}): expected match={expected_match}, "
            f"got match={result['match']} (method={result['method']}, "
            f"score={result['similarity_score']}, notes={result['notes']!r})"
        )
        assert result["confidence"] == expected_confidence, (
            f"names_match({a!r}, {b!r}): expected confidence={expected_confidence!r}, "
            f"got confidence={result['confidence']!r}"
        )


# ---------------------------------------------------------------------------
# names_match — method field checks
# ---------------------------------------------------------------------------

class TestNamesMatchMethod:

    def test_exact_method_same_name(self):
        r = names_match("Stephanie Coxon", "Stephanie Coxon")
        assert r["method"] == "exact"
        assert r["similarity_score"] == 1.0

    def test_exact_method_after_normalisation(self):
        # After normalisation both become "stephanie coxon"
        r = names_match("Stephanie Coxon", "COXON, Stephanie")
        assert r["method"] == "exact"

    def test_exact_middle_initial(self):
        # Both normalise to "james metcalf"
        r = names_match("James S. Metcalf", "James Metcalf")
        assert r["method"] == "exact"

    def test_abbreviated_method(self):
        r = names_match("J. Low", "Jim Low")
        assert r["method"] == "abbreviated"

    def test_no_match_method_different_people(self):
        r = names_match("John Smith", "Jane Smith")
        assert r["method"] == "no_match"
        assert r["match"] is False
        assert r["confidence"] == "high"

    def test_no_match_james_jim(self):
        r = names_match("James Low", "Jim Low")
        assert r["method"] == "no_match"
        assert r["match"] is False

    def test_similarity_score_range(self):
        r = names_match("Alice Brown", "Bob Green")
        assert 0.0 <= r["similarity_score"] <= 1.0


# ---------------------------------------------------------------------------
# names_match — edge cases
# ---------------------------------------------------------------------------

class TestNamesMatchEdgeCases:

    def test_reversed_abbreviated(self):
        # Name_b is abbreviated, name_a is full
        r = names_match("Jim Low", "J. Low")
        assert r["match"] is True
        assert r["method"] == "abbreviated"
        assert r["confidence"] == "medium"

    def test_abbreviated_wrong_initial(self):
        # "B. Low" should NOT match "Jim Low"
        r = names_match("B. Low", "Jim Low")
        assert r["match"] is False

    def test_honorific_both_sides(self):
        r = names_match("Dr. Jane Smith", "Prof. Jane Smith")
        assert r["match"] is True
        assert r["method"] == "exact"

    def test_hyphenated_vs_spaced(self):
        # "Sarah Jones-Williams" vs "Sarah Jones Williams"
        r = names_match("Sarah Jones-Williams", "Sarah Jones Williams")
        assert r["match"] is True

    def test_custom_threshold_strict(self):
        # With threshold=1.0, only exact/perfect matches pass
        # These have completely different surnames so should never match
        r = names_match("Sarah Jones-Williams", "Sarah Jones-Henderson", threshold=1.0)
        assert r["match"] is False

    def test_completely_different_names(self):
        r = names_match("Alice Brown", "Bob Green")
        assert r["match"] is False

    def test_same_surname_different_first(self):
        r = names_match("John Smith", "Jane Smith")
        assert r["match"] is False


# ---------------------------------------------------------------------------
# normalise_for_storage
# ---------------------------------------------------------------------------

class TestNormaliseForStorage:

    def test_returns_string(self):
        result = normalise_for_storage("Stephanie Coxon")
        assert isinstance(result, str)
        assert result == "stephanie coxon"

    def test_low_confidence_logs(self, capsys):
        normalise_for_storage("J. Low")
        captured = capsys.readouterr()
        assert "low-confidence" in captured.out
        assert "j low" in captured.out

    def test_high_confidence_no_log(self, capsys):
        normalise_for_storage("Stephanie Coxon")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_surname_first_no_log(self, capsys):
        # Format corrected but confidence is still "high"
        result = normalise_for_storage("SMITH, John")
        assert result == "john smith"
        captured = capsys.readouterr()
        assert captured.out == ""
