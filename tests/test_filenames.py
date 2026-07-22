import re
import unicodedata

import pytest

from oaw.errors import OawError
from oaw.filenames import portable_filename_component, portable_relative_path


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", "must not be empty"),
        (" padded", "surrounding whitespace"),
        ("padded ", "trailing space"),
        (".", "must not be '.' or '..'"),
        ("..", "must not be '.' or '..'"),
        (".hidden", "must not start with a dot"),
        ("trailing.", "must not end with a dot"),
        ("bad\x00name", "control character U+0000"),
        ("bad\ud800name", "surrogate code point U+D800"),
        ("CON", "Windows reserved device name"),
        ("lpt9.txt", "Windows reserved device name"),
        ("COM¹", "Windows reserved device name"),
        ("CONIN$", "Windows reserved device name"),
        *[(f"bad{character}name", "reserved filename character") for character in '\\/:*?"<>|'],
    ],
)
def test_portable_filename_component_rejects_each_rule_family(value, expected):
    with pytest.raises(OawError, match=re.escape(expected)):
        portable_filename_component(value, "test name")


def test_portable_filename_component_preserves_supported_unicode_and_punctuation():
    decomposed = unicodedata.normalize("NFD", "Café — owner's (draft) - v2")

    result = portable_filename_component(decomposed, "test name")

    assert result == "Café — owner's (draft) - v2"
    assert unicodedata.is_normalized("NFC", result)


@pytest.mark.parametrize(
    "value",
    ["/absolute", "topic//detail", "topic/../detail", "topic/.hidden", "topic/NUL"],
)
def test_portable_relative_path_validates_every_component(value):
    with pytest.raises(OawError):
        portable_relative_path(value, "test path")


def test_portable_relative_path_preserves_nested_components():
    assert portable_relative_path("architecture/provider choice", "test path").as_posix() == (
        "architecture/provider choice"
    )
