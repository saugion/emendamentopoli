"""Pure Akoma Ntoso parser: XML text -> ParsedAkn (no fetching, no IO).

The implementation lives in amendments_worker.sources.senato (source parsers
live in the sources/ package); this module re-exports the names so existing
imports keep working. Shared contract with src/akn.ts: tests/fixtures/akn/*.xml
+ *.expected.json are read by both the Python and the TypeScript test suites,
so the two adapters are pinned to the same behaviour.
"""

from ..sources.senato import (  # re-exported: tests import these names from this module
    ParsedAkn,
    decode_entities,
    extract_bill,
    extract_date,
    extract_parent_number,
    extract_signatories,
    extract_text,
    parent_number_from_numero,
    parse,
    senator_profile,
)

__all__ = [
    "ParsedAkn",
    "decode_entities",
    "extract_bill",
    "extract_date",
    "extract_parent_number",
    "extract_signatories",
    "extract_text",
    "parent_number_from_numero",
    "parse",
    "senator_profile",
]
