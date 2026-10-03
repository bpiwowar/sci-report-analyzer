"""Smoke tests against the real APIs (``pytest -m live``)."""

import pytest

from sci_report_analyzer.sources import ADAPTERS

pytestmark = pytest.mark.live
NAME = "Benjamin Piwowarski"


@pytest.mark.parametrize("source", ["dblp", "hal", "semanticscholar", "orcid", "thesesfr"])
async def test_search_and_fetch(source):
    adapter = ADAPTERS[source]
    cands = await adapter.search(NAME)
    assert cands, f"no candidate from {source}"
    result = await adapter.fetch(cands[0].external_id, [NAME])
    assert result.publications or result.theses
