"""Table-driven edge cases for star/candidate matching (decide + playlist)."""

from __future__ import annotations

import pytest

from starkeep import decide, playlist
from starkeep.subsonic import _norm, _strip_feat

# (candidate artist, candidate title, starred artist, starred title, match?)
CASES = [
    ("Artist", "Title", "Artist", "Title", True),
    ("ARTIST", "  TITLE ", "artist", "title", True),          # case + whitespace
    ("AC/DC", "Back: In Black", "AC/DC", "Back: In Black", True),
    ("Ｂｅｙｏｎｃｅ", "Ｔｉｔｌｅ", "Beyonce", "Title", True),  # NFKC fullwidth
    ("Artist", "Title (feat. Guest)", "Artist", "Title", True),  # guest credit
    ("Artist feat. Guest", "Title", "Artist", "Title", True),
    ("Artist", "Title (feat. Guest)", "Artist", "Title (Live)", False),  # still different
    ("Artist", "Title", "Artist", "Another Title", False),
    ("Someone Else", "Title", "Artist", "Title", False),
    ("Artist", "Title (Live)", "Artist", "Title", False),     # version words must NOT fold
]


@pytest.mark.parametrize("ca,ct,sa,st_,expected", CASES)
def test_starred_match(ca, ct, sa, st_, expected):
    assert decide.starred_match(
        {"artist": ca, "title": ct},
        [{"artist": sa, "title": st_, "path": ""}],
    ) is expected


def test_playlist_song_lookup_feat_variant():
    class Sub:
        def search_songs(self, artist, title, limit=10):
            return [{"id": "s1", "title": "Title", "artist": "Artist",
                     "path": "/lib/Artist/Title.m4a"}]
    cand = {"artist": "Artist", "title": "Title (feat. Guest)",
            "file_path": "/incoming/Artist - Title (feat. Guest).m4a"}
    assert playlist._song_id_for(Sub(), cand) == "s1"


def test_strip_feat():
    assert _strip_feat("Song (feat. B)") == "song"
    assert _strip_feat("Artist feat. B") == "artist"
    assert _strip_feat("Song ft. B") == "song"
    assert _strip_feat("Song (Live)") == "song (live)"  # only feat/ft is stripped


def test_norm_folds_unicode_compatibility():
    assert _norm("Ｂｅｙｏｎｃｅ") == "beyonce"
    assert _norm("ﬁle") == "file"
    assert _norm("  A  B ") == "a b"
