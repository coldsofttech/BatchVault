import pytest

from batchvault.errors import OptionsError
from batchvault.options import MIB, PackOptions, parse_size


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("500", 500),
        ("1k", 1024),
        ("10m", 10 * MIB),
        ("2G", 2 * 1024 * MIB),
        ("1mb", MIB),
        (7, 7),
    ],
)
def test_parse_size(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize("value", ["", "ten", "-1", "1.5m", "0", 0])
def test_parse_size_rejects_invalid(value):
    with pytest.raises(OptionsError):
        parse_size(value)


def test_pack_options_defaults():
    options = PackOptions()
    assert options.level == 5
    assert options.volume_size == 10 * MIB


@pytest.mark.parametrize("kwargs", [{"level": 10}, {"level": -1}, {"jobs": -1}])
def test_pack_options_validation(kwargs):
    with pytest.raises(OptionsError):
        PackOptions(**kwargs)
