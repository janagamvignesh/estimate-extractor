import json

from extractor.cli import main
from tests.conftest import SAMPLE


def test_end_to_end_replay_run(tmp_path, capsys):
    rc = main([str(SAMPLE), "--provider", "replay", "--out", str(tmp_path)])
    assert rc == 0
    run = next(tmp_path.iterdir())
    ex = {p.name.split("_")[1]: json.loads(p.read_text()) for p in run.glob("*.extraction.json")}
    assert ex["B"]["status"] == "ok" and ex["C"]["status"] == "ok"
    # no replay file for A -> recorded as failed, run continues, system-only checks still reported
    assert ex["A"]["status"] == "failed" and ex["A"]["errors"][0]["kind"] == "provider_error"
    rec_a = json.loads(next(run.glob("CLAIM_A*.reconciliation.json")).read_text())
    assert any(f["check"] == "system_lines_vs_system_total" for f in rec_a["findings"])
    assert len((run / "calls.jsonl").read_text().splitlines()) == 3
    assert "CLAIM_C" in (run / "report.md").read_text()
