from station_appkit import changelog

TEXT = """# Changelog

Some words about this file.

## [Unreleased]

## [0.26.0] - 2026-11-27

A short intro,
over two lines.

### Added

- Help › **Report a problem**: a button that opens a new
  [GitHub issue](https://github.com/raby/station-studio/issues/new),
  with `this` filled in.
- A second thing.

### Fixed

- A fix.

## [0.25.0] - 2026-11-24

- No groups here.

```
- not a bullet: inside a code block
```

[0.25.0]: https://example.com/a-link-definition

## Earlier versions

- not part of any release
"""


def test_parse_reads_releases_intros_groups_and_wrapped_bullets():
    releases = changelog.parse(TEXT)
    assert [r.version for r in releases] == ["Unreleased", "0.26.0", "0.25.0"]
    new = releases[1]
    assert new.date == "2026-11-27"
    assert new.intro == ["A short intro, over two lines."]
    assert [h for h, _ in new.groups] == ["Added", "Fixed"]
    assert new.items[0] == ("Help › **Report a problem**: a button that opens a new "
                            "[GitHub issue](https://github.com/raby/station-studio/issues/new), with `this` filled in.")
    assert new.items[1:] == ["A second thing.", "A fix."]
    old = releases[2]
    assert old.groups == [(None, ["No groups here."])]  # code blocks, link definitions and "Earlier versions" left out


def test_as_dict_is_plain_text_for_a_page():
    got = changelog.parse(TEXT)[1].as_dict()
    assert got["groups"][0] == {"heading": "Added", "items": [
        "Help › Report a problem: a button that opens a new GitHub issue, with this filled in.", "A second thing."]}
    assert got["version"] == "0.26.0" and got["date"] == "2026-11-27"


def test_section_is_the_release_with_each_line_whole():
    # GitHub keeps every line break in a release's notes: wrapped lines are joined.
    got = changelog.section(TEXT, "0.26.0")
    assert got.startswith("A short intro, over two lines.\n\n### Added\n\n- Help › **Report a problem**: a button "
                          "that opens a new [GitHub issue](https://github.com/raby/station-studio/issues/new), "
                          "with `this` filled in.\n- A second thing.")
    assert got.endswith("- A fix.")
    # A code block is kept as it is.
    assert changelog.section(TEXT, "0.25.0") == "- No groups here.\n\n```\n- not a bullet: inside a code block\n```"
    assert changelog.section(TEXT, "9.9.9") is None
    assert changelog.section(TEXT, "Unreleased") is None  # nothing under it


def test_whats_new_reads_a_releases_notes_back():
    body = (f"{changelog.NOTES_HEADING} in Station Player 0.26.0\r\n\r\n"
            + changelog.section(TEXT, "0.26.0").replace("\n", "\r\n")
            + f"\r\n\r\n{changelog.NOTES_END}\r\nStation Player 0.26.0: plays…\r\n\r\n- **Mac:** not a change\r\n")
    got = changelog.whats_new(body)
    assert got.intro == ["A short intro, over two lines."]
    assert got.items == changelog.parse(TEXT)[1].items  # nothing from below the ---


def test_whats_new_of_an_older_release_is_none():
    assert changelog.whats_new("Station Player 0.24.0: plays…\n\n**Download** …") is None
    assert changelog.whats_new(None) is None
    assert changelog.whats_new(f"{changelog.NOTES_HEADING}\n\n---\n") is None  # nothing in it


def test_plain():
    assert changelog.plain("**Bold** `code` and [a link](https://x)") == "Bold code and a link"
