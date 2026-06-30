"""Parity check: committed generated files must match the live contract.

Mirrors the ``install-script-parity`` CI job, but for the hardware contract:
regenerate each target in memory from ``meta/shared/hardware.toml`` and assert
byte-equality with the committed copy. A contract bump that wasn't re-rendered
(or a hand-edit of a generated file) fails here and in the ``contract-parity`` job.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
GEN_PATH = RUNTIME_ROOT / "scripts" / "gen_from_contract.py"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_from_contract", GEN_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()

# Skip cleanly when the sibling ``meta`` repo isn't checked out (e.g. a
# runtime-only CI job). The dedicated ``contract-parity`` job checks out ``meta``
# alongside ``runtime`` and runs ``gen_from_contract.py --check``.
pytestmark = pytest.mark.skipif(
    not gen.DEFAULT_CONTRACT.exists(),
    reason=f"contract not found at {gen.DEFAULT_CONTRACT}",
)


@pytest.mark.parametrize("rel_path", list(gen.TARGETS))
def test_committed_file_matches_contract(rel_path: str) -> None:
    contract = gen.load_contract(gen.DEFAULT_CONTRACT)
    expected = gen.TARGETS[rel_path](contract)
    committed = (RUNTIME_ROOT / rel_path).read_text()
    assert (
        committed == expected
    ), f"{rel_path} is out of sync with the contract; run `make gen` and commit the result."
