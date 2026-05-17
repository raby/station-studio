"""Tests for artist collaboration matching and alias resolution.

Ported from weekly/tests/test_artist_match.py — the artist-match
behaviour must remain byte-equivalent so the eventual port harness
(§17 Phase B sub-slice #17) passes. New for station-studio: the
load_alias_map persistence integration round-trip.
"""
import pytest

from engine.artist_match import (
    extract_artist_keys,
    resolve_artist_keys,
    artists_conflict,
    build_alias_map,
)


# ============================================================================
# extract_artist_keys
# ============================================================================


class TestExtractArtistKeys:
    """Splitting raw artist strings into individual artist keys."""

    def test_single_artist(self):
        assert extract_artist_keys("Sabrina Carpenter") == frozenset({"sabrina carpenter"})

    def test_none(self):
        assert extract_artist_keys(None) == frozenset()

    def test_empty_string(self):
        assert extract_artist_keys("") == frozenset()

    def test_blank_string(self):
        assert extract_artist_keys("   ") == frozenset()

    # --- Collaboration separators ---

    def test_feat_dot(self):
        result = extract_artist_keys("Alex Warren feat. Ella Henderson")
        assert result == frozenset({"alex warren", "ella henderson"})

    def test_feat_no_dot(self):
        result = extract_artist_keys("Alex Warren feat Ella Henderson")
        assert result == frozenset({"alex warren", "ella henderson"})

    def test_ft_dot(self):
        result = extract_artist_keys("BL3SS x CamrinWatsin ft. bbyclose")
        assert result == frozenset({"bl3ss", "camrinwatsin", "bbyclose"})

    def test_featuring(self):
        result = extract_artist_keys("KSI featuring Trippie Redd")
        assert result == frozenset({"ksi", "trippie redd"})

    def test_with(self):
        result = extract_artist_keys("Sabrina Carpenter with Jimin")
        assert result == frozenset({"sabrina carpenter", "jimin"})

    def test_vs(self):
        result = extract_artist_keys("DJ A vs DJ B")
        assert result == frozenset({"dj a", "dj b"})

    def test_vs_dot(self):
        result = extract_artist_keys("DJ A vs. DJ B")
        assert result == frozenset({"dj a", "dj b"})

    def test_ampersand_with_spaces(self):
        """Ampersand only splits when surrounded by spaces."""
        result = extract_artist_keys("Andy C & Becky Hill")
        assert result == frozenset({"andy c", "becky hill"})

    def test_comma(self):
        result = extract_artist_keys("Calvin Harris, Ellie Goulding")
        assert result == frozenset({"calvin harris", "ellie goulding"})

    def test_semicolon(self):
        result = extract_artist_keys("Lady Gaga;Bruno Mars")
        assert result == frozenset({"lady gaga", "bruno mars"})

    def test_x_as_separator(self):
        """Standalone 'x' with spaces is a separator."""
        result = extract_artist_keys("Ella Henderson x Switch Disco")
        assert result == frozenset({"ella henderson", "switch disco"})

    def test_multiple_separators(self):
        result = extract_artist_keys("David Guetta & Alesso feat. Madison Love")
        assert result == frozenset({"david guetta", "alesso", "madison love"})

    def test_three_artists_comma_ampersand(self):
        result = extract_artist_keys("Sonny Fodera, Jazzy & D.O.D")
        assert result == frozenset({"sonny fodera", "jazzy", "d.o.d"})

    def test_three_commas(self):
        result = extract_artist_keys("Clean Bandit, Anne-Marie, David Guetta")
        assert result == frozenset({"clean bandit", "anne-marie", "david guetta"})

    # --- Names that must NOT be split ---

    def test_charli_xcx_not_split(self):
        """'xcx' contains 'x' but is not a standalone word — must not split."""
        result = extract_artist_keys("Charli xcx")
        assert result == frozenset({"charli xcx"})

    def test_alex_warren_not_split(self):
        """'Alex' contains 'x' at the end — must not split on it."""
        result = extract_artist_keys("Alex Warren")
        assert result == frozenset({"alex warren"})

    def test_fred_again_dot_dot(self):
        """'Fred again..' — dots in name, not a separator."""
        result = extract_artist_keys("Fred again..")
        assert result == frozenset({"fred again.."})

    def test_florence_plus_the_machine(self):
        """'+' is NOT a separator (not in the regex)."""
        result = extract_artist_keys("Florence + The Machine")
        assert result == frozenset({"florence + the machine"})

    def test_case_insensitive(self):
        result = extract_artist_keys("RAYE")
        assert result == frozenset({"raye"})

    def test_olly_alexander_years_and_years(self):
        """Parenthetical act name is extracted as a single key."""
        result = extract_artist_keys("Olly Alexander (Years & Years)")
        assert result == frozenset({"olly alexander", "years & years"})

    def test_and_as_separator(self):
        """'and' (space-padded) splits collaborations."""
        result = extract_artist_keys("David Guetta and Sia")
        assert result == frozenset({"david guetta", "sia"})

    def test_parenthetical_simple(self):
        """Parenthetical content extracted as an extra key."""
        result = extract_artist_keys("Some Artist (The Band)")
        assert result == frozenset({"some artist", "the band"})

    def test_parenthetical_not_split_internally(self):
        """Content inside parentheses is NOT split on separators."""
        result = extract_artist_keys("DJ Name (Foo & Bar)")
        assert result == frozenset({"dj name", "foo & bar"})

    def test_no_parentheses_unchanged(self):
        """No parentheses — original splitting is unaffected."""
        result = extract_artist_keys("Calvin Harris, Ellie Goulding")
        assert result == frozenset({"calvin harris", "ellie goulding"})

    def test_and_not_split_inside_word(self):
        """'and' only splits as a standalone word, not inside 'Andrew'."""
        result = extract_artist_keys("Andrew Smith")
        assert result == frozenset({"andrew smith"})

    # --- Parenthetical feat. credits ---

    def test_paren_feat_splits(self):
        """(feat. A & B) should strip prefix and split on &."""
        result = extract_artist_keys(
            "I Smoked Away My Brain (I'm God x Demons Mashup) "
            "(feat. Imogen Heap & Clams Casino)"
        )
        assert result == frozenset({
            "i smoked away my brain",
            "i'm god x demons mashup",
            "imogen heap",
            "clams casino",
        })

    def test_paren_ft_comma(self):
        """(ft. A, B) should strip prefix and split on comma."""
        result = extract_artist_keys("Track Title (ft. Artist1, Artist2)")
        assert result == frozenset({"track title", "artist1", "artist2"})

    def test_paren_featuring_single(self):
        """(featuring Someone) → single artist extracted."""
        result = extract_artist_keys("Track (featuring Someone)")
        assert result == frozenset({"track", "someone"})

    def test_paren_with_single(self):
        """(with Artist) → single artist extracted."""
        result = extract_artist_keys("Song Name (with Artist)")
        assert result == frozenset({"song name", "artist"})

    def test_paren_no_feat_still_intact(self):
        """Non-feat parenthetical like (Years & Years) stays intact."""
        result = extract_artist_keys("Olly Alexander (Years & Years)")
        assert result == frozenset({"olly alexander", "years & years"})


class TestExtractRealData:
    """Test against actual artist names from weekly's COMMERCIAL pool —
    proves byte-equivalent splitting with the port source."""

    @pytest.mark.parametrize("artist,expected_keys", [
        ("Sabrina Carpenter", {"sabrina carpenter"}),
        ("Alex Warren feat. Ella Henderson", {"alex warren", "ella henderson"}),
        ("Alex Warren, Jelly Roll", {"alex warren", "jelly roll"}),
        ("Calvin Harris, Clementine Douglas", {"calvin harris", "clementine douglas"}),
        ("David Guetta & Alesso feat. Madison Love", {"david guetta", "alesso", "madison love"}),
        ("Lady Gaga;Bruno Mars", {"lady gaga", "bruno mars"}),
        ("ROSÉ & Bruno Mars", {"rosé", "bruno mars"}),
        ("Sam Fender & Olivia Dean", {"sam fender", "olivia dean"}),
        ("Ella Henderson x Switch Disco", {"ella henderson", "switch disco"}),
        ("Nathan Dawe X Abi Flynn", {"nathan dawe", "abi flynn"}),
        ("The Weeknd & Playboi Carti", {"the weeknd", "playboi carti"}),
        ("Robbie Williams & Tony Iommi", {"robbie williams", "tony iommi"}),
        ("KSI feat. Trippie Redd", {"ksi", "trippie redd"}),
        ("Sonny Fodera, Jazzy & D.O.D", {"sonny fodera", "jazzy", "d.o.d"}),
        ("Sonny Fodera, Jazzy", {"sonny fodera", "jazzy"}),
        ("Rudimental x Karen Harding", {"rudimental", "karen harding"}),
        ("BL3SS x CamrinWatsin ft. bbyclose", {"bl3ss", "camrinwatsin", "bbyclose"}),
        ("David Guetta and Sia", {"david guetta", "sia"}),
        ("Olly Alexander (Years & Years)", {"olly alexander", "years & years"}),
        # Parenthetical feat. credits
        ("I Smoked Away My Brain (I'm God x Demons Mashup) (feat. Imogen Heap & Clams Casino)",
         {"i smoked away my brain", "i'm god x demons mashup", "imogen heap", "clams casino"}),
        # Single artists that must not split
        ("Charli xcx", {"charli xcx"}),
        ("Alex Warren", {"alex warren"}),
        ("Streetlight Walkers", {"streetlight walkers"}),
        ("Fred again..", {"fred again.."}),
    ])
    def test_real_artist(self, artist, expected_keys):
        assert extract_artist_keys(artist) == frozenset(expected_keys)


# ============================================================================
# resolve_artist_keys
# ============================================================================


class TestResolveArtistKeys:
    """Resolve aliases after splitting collaborations."""

    def test_no_alias_map(self):
        result = resolve_artist_keys("Sabrina Carpenter", None)
        assert result == frozenset({"sabrina carpenter"})

    def test_empty_alias_map(self):
        result = resolve_artist_keys("Sabrina Carpenter", {})
        assert result == frozenset({"sabrina carpenter"})

    def test_alias_resolved(self):
        alias_map = {"plastikman": "richie hawtin", "richie hawtin": "richie hawtin"}
        result = resolve_artist_keys("Plastikman", alias_map)
        assert result == frozenset({"richie hawtin"})

    def test_canonical_also_resolves(self):
        alias_map = {"plastikman": "richie hawtin", "richie hawtin": "richie hawtin"}
        result = resolve_artist_keys("Richie Hawtin", alias_map)
        assert result == frozenset({"richie hawtin"})

    def test_collab_with_alias(self):
        alias_map = {"plastikman": "richie hawtin", "richie hawtin": "richie hawtin"}
        result = resolve_artist_keys("Plastikman & Someone", alias_map)
        assert result == frozenset({"richie hawtin", "someone"})

    def test_none_input(self):
        alias_map = {"plastikman": "richie hawtin"}
        assert resolve_artist_keys(None, alias_map) == frozenset()

    def test_unmatched_name_passes_through(self):
        alias_map = {"plastikman": "richie hawtin"}
        result = resolve_artist_keys("Sabrina Carpenter", alias_map)
        assert result == frozenset({"sabrina carpenter"})


# ============================================================================
# artists_conflict
# ============================================================================


class TestArtistsConflict:
    """Check if two key-sets overlap (i.e. same person appears in both)."""

    def test_same_artist(self):
        a = frozenset({"sabrina carpenter"})
        b = frozenset({"sabrina carpenter"})
        assert artists_conflict(a, b) is True

    def test_different_artists(self):
        a = frozenset({"sabrina carpenter"})
        b = frozenset({"alex warren"})
        assert artists_conflict(a, b) is False

    def test_collab_overlap(self):
        """'Alex Warren feat. Ella Henderson' conflicts with 'Alex Warren'."""
        a = frozenset({"alex warren", "ella henderson"})
        b = frozenset({"alex warren"})
        assert artists_conflict(a, b) is True

    def test_collab_overlap_other_member(self):
        """'Alex Warren feat. Ella Henderson' conflicts with 'Ella Henderson'."""
        a = frozenset({"alex warren", "ella henderson"})
        b = frozenset({"ella henderson"})
        assert artists_conflict(a, b) is True

    def test_collab_no_overlap(self):
        a = frozenset({"alex warren", "ella henderson"})
        b = frozenset({"sabrina carpenter"})
        assert artists_conflict(a, b) is False

    def test_two_collabs_shared_member(self):
        """Two collabs sharing one member should conflict."""
        a = frozenset({"calvin harris", "ellie goulding"})
        b = frozenset({"calvin harris", "clementine douglas"})
        assert artists_conflict(a, b) is True

    def test_empty_sets(self):
        assert artists_conflict(frozenset(), frozenset()) is False

    def test_one_empty(self):
        assert artists_conflict(frozenset({"a"}), frozenset()) is False

    def test_alias_resolved_conflict(self):
        """After alias resolution, Plastikman → richie hawtin matches."""
        alias_map = build_alias_map([("Plastikman", "Richie Hawtin")])
        a = resolve_artist_keys("Plastikman", alias_map)
        b = resolve_artist_keys("Richie Hawtin", alias_map)
        assert artists_conflict(a, b) is True


# ============================================================================
# build_alias_map
# ============================================================================


class TestBuildAliasMap:
    def test_basic(self):
        result = build_alias_map([("Plastikman", "Richie Hawtin")])
        assert result == {
            "plastikman": "richie hawtin",
            "richie hawtin": "richie hawtin",
        }

    def test_multiple_aliases_same_canonical(self):
        result = build_alias_map([
            ("Plastikman", "Richie Hawtin"),
            ("F.U.S.E.", "Richie Hawtin"),
        ])
        assert result["plastikman"] == "richie hawtin"
        assert result["f.u.s.e."] == "richie hawtin"
        assert result["richie hawtin"] == "richie hawtin"

    def test_strips_whitespace(self):
        result = build_alias_map([("  Plastikman  ", "  Richie Hawtin  ")])
        assert "plastikman" in result
        assert result["plastikman"] == "richie hawtin"

    def test_empty_list(self):
        assert build_alias_map([]) == {}


# ============================================================================
# Integration: Collaboration conflicts with real pool data
# ============================================================================


class TestCollaborationConflicts:
    """Verify that real-world collaboration strings produce correct conflicts."""

    def test_alex_warren_solo_vs_feat(self):
        """'Alex Warren' should conflict with 'Alex Warren feat. Ella Henderson'."""
        a = extract_artist_keys("Alex Warren")
        b = extract_artist_keys("Alex Warren feat. Ella Henderson")
        assert artists_conflict(a, b) is True

    def test_alex_warren_solo_vs_comma(self):
        """'Alex Warren' should conflict with 'Alex Warren, Jelly Roll'."""
        a = extract_artist_keys("Alex Warren")
        b = extract_artist_keys("Alex Warren, Jelly Roll")
        assert artists_conflict(a, b) is True

    def test_ella_henderson_solo_vs_alex_warren_feat(self):
        """'Ella Henderson' should conflict with 'Alex Warren feat. Ella Henderson'."""
        a = extract_artist_keys("Ella Henderson")
        b = extract_artist_keys("Alex Warren feat. Ella Henderson")
        assert artists_conflict(a, b) is True

    def test_ella_henderson_solo_vs_switch_disco(self):
        """'Ella Henderson' should conflict with 'Ella Henderson x Switch Disco'."""
        a = extract_artist_keys("Ella Henderson")
        b = extract_artist_keys("Ella Henderson x Switch Disco")
        assert artists_conflict(a, b) is True

    def test_lady_gaga_solo_vs_semicolon(self):
        """'Lady Gaga' should conflict with 'Lady Gaga;Bruno Mars'."""
        a = extract_artist_keys("Lady Gaga")
        b = extract_artist_keys("Lady Gaga;Bruno Mars")
        assert artists_conflict(a, b) is True

    def test_bruno_mars_conflicts_with_both(self):
        """'Bruno Mars' appears in both 'Lady Gaga;Bruno Mars' and 'ROSÉ & Bruno Mars'."""
        lady = extract_artist_keys("Lady Gaga;Bruno Mars")
        rose = extract_artist_keys("ROSÉ & Bruno Mars")
        assert artists_conflict(lady, rose) is True  # via bruno mars

    def test_sam_fender_conflicts(self):
        a = extract_artist_keys("Sam Fender")
        b = extract_artist_keys("Sam Fender & Olivia Dean")
        assert artists_conflict(a, b) is True

    def test_olivia_dean_conflicts(self):
        a = extract_artist_keys("Olivia Dean")
        b = extract_artist_keys("Sam Fender & Olivia Dean")
        assert artists_conflict(a, b) is True

    def test_the_weeknd_conflicts(self):
        a = extract_artist_keys("The Weeknd")
        b = extract_artist_keys("The Weeknd & Playboi Carti")
        assert artists_conflict(a, b) is True

    def test_raye_conflicts(self):
        a = extract_artist_keys("RAYE")
        b = extract_artist_keys("Mark Ronson & RAYE")
        assert artists_conflict(a, b) is True

    def test_anne_marie_conflicts(self):
        a = extract_artist_keys("Anne-Marie")
        b = extract_artist_keys("Clean Bandit, Anne-Marie, David Guetta")
        assert artists_conflict(a, b) is True

    def test_kylie_minogue_conflicts(self):
        a = extract_artist_keys("Kylie Minogue")
        b = extract_artist_keys("The Blessed Madonna & Kylie Minogue")
        assert artists_conflict(a, b) is True

    def test_robbie_williams_conflicts(self):
        a = extract_artist_keys("Robbie Williams")
        b = extract_artist_keys("Robbie Williams & Tony Iommi")
        assert artists_conflict(a, b) is True

    def test_jazzy_conflicts_with_sonny_fodera_jazzy(self):
        a = extract_artist_keys("Jazzy & KILIMANJARO")
        b = extract_artist_keys("Sonny Fodera, Jazzy")
        assert artists_conflict(a, b) is True  # via "jazzy"

    def test_calvin_harris_two_collabs_conflict(self):
        a = extract_artist_keys("Calvin Harris, Clementine Douglas")
        b = extract_artist_keys("Calvin Harris, Ellie Goulding")
        assert artists_conflict(a, b) is True  # via "calvin harris"

    def test_paren_feat_conflict(self):
        """Artist in (feat. ...) parenthetical conflicts with standalone."""
        a = extract_artist_keys("Imogen Heap")
        b = extract_artist_keys(
            "I Smoked Away My Brain (I'm God x Demons Mashup) "
            "(feat. Imogen Heap & Clams Casino)"
        )
        assert artists_conflict(a, b) is True

    def test_unrelated_artists_no_conflict(self):
        a = extract_artist_keys("Sabrina Carpenter")
        b = extract_artist_keys("Billie Eilish")
        assert artists_conflict(a, b) is False
