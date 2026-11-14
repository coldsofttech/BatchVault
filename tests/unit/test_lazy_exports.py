import pytest

import batchvault


class TestLazyExports:
    def test_api_names_resolve_to_their_module(self):
        from batchvault.core import api

        assert batchvault.pack is api.pack
        assert batchvault.verify is api.verify

    def test_every_public_name_resolves(self):
        for name in batchvault.__all__:
            assert getattr(batchvault, name) is not None

    def test_public_names_are_listed(self):
        assert {"pack", "unpack", "Status"} <= set(dir(batchvault))

    def test_an_unknown_name_is_an_attribute_error(self):
        with pytest.raises(AttributeError, match="no attribute"):
            _ = batchvault.nope
