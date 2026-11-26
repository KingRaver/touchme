"""Guards the prior-art bug this project avoids: a built .tox embedding a script older than
`bridge/src/callbacks.py` (research/PRIOR_ART_TOUCHDESIGNER_MCP.md, finding 3).

This project never commits the built `.tox` (it embeds a per-build token, bridge/build/ is
gitignored), so there is no checked-in binary to diff against. What remains to guard is the
one thing that could still make an embedded script stale even though nothing is committed:
`build_spike.prepare_script` must read `bridge/src/callbacks.py` fresh for every build,
never a cached or hand-copied string. Needs no TouchDesigner, so it runs in CI.
"""

import ast
import importlib.util
import json
from pathlib import Path

BRIDGE_DIR = Path(__file__).parents[2] / "bridge"
SOURCE = (BRIDGE_DIR / "src" / "callbacks.py").read_text(encoding="utf-8")


def _load_build_spike():
    spec = importlib.util.spec_from_file_location("td_mcp_build_spike", BRIDGE_DIR / "build_spike.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepared_script_is_the_current_source_with_only_the_placeholders_filled():
    build_spike = _load_build_spike()
    script = build_spike.prepare_script("tok-123", test_fixtures=False)
    expected = (
        SOURCE.replace("__TOKEN__", "tok-123")
        .replace("__TEST_FIXTURES__", "0")
        .replace('"__AUDIT_LOG__"', json.dumps(build_spike.DEFAULT_AUDIT_LOG))
    )
    assert script == expected
    assert "__TOKEN__" not in script and "__TEST_FIXTURES__" not in script


def test_prepared_script_reads_the_source_file_fresh_every_call(tmp_path, monkeypatch):
    """If this starts reading from anywhere but the live source file, this must fail."""
    build_spike = _load_build_spike()
    decoy_src = tmp_path / "src"
    decoy_src.mkdir()
    (decoy_src / "callbacks.py").write_text(SOURCE + "\n# decoy marker: must appear below\n", encoding="utf-8")
    monkeypatch.setattr(build_spike, "HERE", str(tmp_path))

    script = build_spike.prepare_script("tok", test_fixtures=True)

    assert "# decoy marker: must appear below" in script


def test_audit_log_path_is_embedded_as_a_python_string_that_cannot_break_out(tmp_path):
    build_spike = _load_build_spike()
    awkward = str(tmp_path / 'a "quoted"\\ name.jsonl')

    script = build_spike.prepare_script("tok", test_fixtures=False, audit_log=awkward)

    line = next(row for row in script.splitlines() if row.startswith("_AUDIT_SETTING = "))
    assert ast.literal_eval(line.split(" = ", 1)[1].split("  #")[0]) == awkward
    namespace: dict[str, object] = {}
    exec(compile(script, "callbacks.py", "exec"), namespace)  # noqa: S102
    assert namespace["AUDIT_LOG"] == awkward
