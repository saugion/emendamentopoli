import json
from pathlib import Path

from amendments_worker.workflows import akn

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "akn"
LEGISLATURE = 19


def test_akn_fixtures_match_shared_contract():
    xmls = sorted(FIXTURES.glob("*.xml"))
    assert xmls, "no AKN fixtures found"
    for xml_path in xmls:
        expected = json.loads(
            xml_path.with_suffix(".expected.json").read_text(encoding="utf-8")
        )
        parsed = akn.parse(xml_path.read_text(encoding="utf-8"), LEGISLATURE)
        assert parsed.model_dump() == expected, xml_path.name
