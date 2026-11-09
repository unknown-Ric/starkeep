"""Blacklist parsing and matching, including multi-artist candidates."""

from __future__ import annotations

from starkeep import blacklist


def _entries(tmp_path, text):
    p = tmp_path / "blacklist.txt"
    p.write_text(text, encoding="utf-8")
    return blacklist.load(str(p))


def test_load_skips_comments_and_blanks(tmp_path):
    entries = _entries(tmp_path, "\n# comment\n\nA - B\n\n  Solo  \n")
    assert entries == [("a", "b"), ("solo", "")]


def test_missing_file_is_not_an_error(tmp_path):
    assert blacklist.load(str(tmp_path / "nope.txt")) == []
    assert blacklist.load(None) == []
    assert blacklist.load("") == []


def test_exact_artist_title_entry(tmp_path):
    e = _entries(tmp_path, "Cage The Elephant - Come a Little Closer\n")
    assert blacklist.blacklisted(e, "Cage The Elephant", "Come a Little Closer")
    assert blacklist.blacklisted(e, "cage  the elephant", "come a little closer")
    assert not blacklist.blacklisted(e, "Cage The Elephant", "Skin around blanket")
    assert not blacklist.blacklisted(e, "Other", "Come a Little Closer")


def test_artist_entry_matches_any_named_artist(tmp_path):
    e = _entries(tmp_path, "Blood Red Shoes\n")
    # multi-artist spellings seen in the wild: comma, ampersand, slash, 'feat'
    assert blacklist.blacklisted(e, "Blood Red Shoes", "Any")
    assert blacklist.blacklisted(e, "Blood Red Shoes, Someone", "Any")
    assert blacklist.blacklisted(e, "Someone & Blood Red Shoes", "Any")
    assert blacklist.blacklisted(e, "Some/other feat. blood red shoes", "Any")
    assert blacklist.blacklisted(e, "Some ft. Blood Red Shoes", "Any")
    assert blacklist.blacklisted(e, "Some featuring Blood Red Shoes", "Any")
    # a similar- but different-artist is not blacklisted
    assert not blacklist.blacklisted(e, "Red Shoes Band", "Any")
    assert not blacklist.blacklisted(e, "Blood", "Any")


def test_and_is_never_a_separator(tmp_path):
    e = _entries(tmp_path, "Mumford and Sons\n")
    assert blacklist.blacklisted(e, "Mumford and Sons", "X")
    assert not blacklist.blacklisted(e, "Sons, Mumford", "X")  # 'and' never split


def test_track_beats_artist_token_order(tmp_path):
    e = _entries(tmp_path, "Blocked - Song\nOther\n")
    assert blacklist.blacklisted(e, "Blocked", "Song")          # track rule
    assert not blacklist.blacklisted(e, "Blocked", "Other Song")  # title mismatch
    assert not blacklist.blacklisted(e, "Someone", "Song")      # no artist rule hit
    assert not blacklist.blacklisted(e, "Someone", "Other")     # no artist entry match