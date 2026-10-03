"""Publication / thesis sources."""

from .base import SourceAdapter
from .dblp import DblpAdapter
from .doi import DoiAdapter
from .hal import HalAdapter
from .openalex import OpenAlexAdapter
from .orcid import OrcidAdapter
from .scholar import ScholarAdapter
from .semanticscholar import SemanticScholarAdapter
from .thesesfr import ThesesFrAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    a.name: a
    for a in (
        DblpAdapter(),
        HalAdapter(),
        OpenAlexAdapter(),
        SemanticScholarAdapter(),
        OrcidAdapter(),
        ScholarAdapter(),
        ThesesFrAdapter(),
        DoiAdapter(),
    )
}

# Preference order when picking canonical fields from merged records: the DOI record
# (registered by the publisher) first.
PRIORITY = ("doi", "dblp", "hal", "openalex", "semanticscholar", "orcid", "scholar")


def adapter(name: str) -> SourceAdapter:
    return ADAPTERS[name]
