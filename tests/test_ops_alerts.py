from pathlib import Path

from platform_app.ops_alerts import ALERTS


def test_every_paging_alert_has_owner_impact_and_real_runbook_anchor():
    root = Path(__file__).resolve().parents[1]
    pages = [item for item in ALERTS.values() if item.severity == "page"]
    assert len(pages) == 5
    for item in ALERTS.values():
        assert item.owner and item.impact and item.condition
        path, anchor = item.runbook.split("#", 1)
        document = (root / path).read_text(encoding="utf-8")
        assert f"## {anchor.replace('-', ' ')}" in document.casefold()
