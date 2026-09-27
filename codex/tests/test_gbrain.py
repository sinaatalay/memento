"""Protocol tests for ownership, pagination, stdin privacy, and safe failures."""

import asyncio
from pathlib import Path
import sys

import pytest

from memento.gbrain import GBrain, GBrainError


def fake_cli(tmp_path: Path, program: str) -> list[str]:
    script = tmp_path / "fake_gbrain.py"
    script.write_text(program)
    return [sys.executable, str(script)]


async def test_paginated_equal_timestamps_and_tombstones(tmp_path):
    rows = [
        {"slug": f"memories/{i}", "source_id": "default",
         "updated_at": "2026-09-27T12:00:00Z"}
        for i in range(5)
    ]
    rows[-1]["deleted_at"] = "2026-09-27T12:00:00Z"
    command = fake_cli(tmp_path, f"""
import json, sys
assert sys.argv[1:3] == ['call', 'list_pages']
p = json.loads(sys.argv[3])
assert p['updated_after'] == '2026-09-27T11:59:59Z'
assert p['source_id'] == '__all__'
assert p['include_deleted'] is True
rows = {rows!r}
print(json.dumps(rows[p['offset']:p['offset'] + p['limit']]))
""")
    brain = GBrain(command, tmp_path / "home", page_size=2)
    assert await brain.list_pages("2026-09-27T11:59:59Z") == rows


async def test_put_uses_stdin_and_explicit_revision(tmp_path):
    command = fake_cli(tmp_path, """
import json, sys
assert 'PRIVATE BODY' not in ' '.join(sys.argv)
assert '--force' not in sys.argv
assert sys.argv[sys.argv.index('--expected-revision') + 1] == 'previous'
assert sys.argv[sys.argv.index('--source-id') + 1] == 'mem'
assert sys.stdin.read() == 'PRIVATE BODY'
print(json.dumps({'state': 'committed', 'revision': 'next'}))
""")
    brain = GBrain(command, tmp_path / "home")
    result = await brain.put_page(
        "memories/test", "PRIVATE BODY", source="mem",
        expected_revision="previous", request_id="fixed-id",
    )
    assert result == {"state": "committed", "revision": "next"}


async def test_separate_instances_serialize_pglite_ownership(tmp_path):
    command = fake_cli(tmp_path, """
import json, os, pathlib, time
marker = pathlib.Path(os.environ['GBRAIN_HOME']) / 'owned'
fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
time.sleep(0.12)
os.close(fd)
marker.unlink()
print(json.dumps({'revision': 'ok'}))
""")
    brains = [GBrain(command, tmp_path / "home") for _ in range(3)]
    results = await asyncio.gather(*[
        brain.get_page("memories/test") for brain in brains
    ])
    assert results == [{"revision": "ok"}] * 3


async def test_inherited_database_and_provider_settings_cannot_retarget(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://unrelated/production")
    monkeypatch.setenv("GBRAIN_DATABASE_URL", "postgres://other/brain")
    monkeypatch.setenv("GBRAIN_HOME", "/unrelated")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-forwarded")
    command = fake_cli(tmp_path, """
import json, os
assert 'DATABASE_URL' not in os.environ
assert 'GBRAIN_DATABASE_URL' not in os.environ
assert 'OPENROUTER_API_KEY' not in os.environ
assert os.environ['GBRAIN_HOME'].endswith('/isolated')
print(json.dumps({'revision': 'ok'}))
""")
    brain = GBrain(command, tmp_path / "isolated")
    assert await brain.get_page("memories/test") == {"revision": "ok"}


async def test_revision_conflict_keeps_receipt_without_leaking_body(tmp_path):
    command = fake_cli(tmp_path, """
import json, sys
print('PRIVATE BODY', file=sys.stderr)
print(json.dumps({'error': 'revision_conflict', 'message': 'PRIVATE BODY',
                  'write_request': {'state': 'conflict', 'request_id': 'fixed'}}))
sys.exit(1)
""")
    brain = GBrain(command, tmp_path / "home")
    with pytest.raises(GBrainError) as caught:
        await brain.put_page("memories/test", "PRIVATE BODY", request_id="fixed")
    error = caught.value
    assert error.code == "revision_conflict"
    assert error.receipt["state"] == "conflict"
    assert error.request_id == "fixed"
    assert "PRIVATE BODY" not in str(error)


async def test_timeout_stops_child_before_releasing_lock(tmp_path):
    command = fake_cli(tmp_path, """
import json, pathlib, sys, time
if sys.argv[2] == 'slow':
    time.sleep(1)
    pathlib.Path('should-not-exist').touch()
print(json.dumps({'revision': 'ok'}))
""")
    brain = GBrain(command, tmp_path / "home", timeout=0.05)
    with pytest.raises(GBrainError, match="timeout_outcome_unknown"):
        await brain.get_page("slow")
    brain.timeout = 2
    assert await brain.get_page("fast") == {"revision": "ok"}
    assert not (tmp_path / "home" / "should-not-exist").exists()
