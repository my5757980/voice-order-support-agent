"""Tool contracts, loaded from the specification rather than restated in code.

`contracts/tools.schema.json` is the single source of truth. Duplicating the schemas
here would let the contract and the implementation drift apart silently, which is
exactly the failure the contract exists to prevent.

Loading happens at import, and a malformed contract raises immediately (constitution:
configuration and schema errors crash at startup).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from src.core.ports import LatencyClass, ToolSpec

from .schema_validate import assert_strict_compatible

_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "specs/001-order-support-agent/contracts/tools.schema.json",
    Path(__file__).resolve().parents[2] / "contracts/tools.schema.json",
)


def _contract_path() -> Path:
    for candidate in _CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "tools.schema.json not found; looked in: " + ", ".join(str(c) for c in _CANDIDATES)
    )


@lru_cache(maxsize=1)
def load_specs() -> tuple[ToolSpec, ...]:
    raw = json.loads(_contract_path().read_text(encoding="utf-8"))
    specs: list[ToolSpec] = []

    for entry in raw["tools"]:
        name = entry["name"]

        # TC-001: an unclassified tool must not be registrable. Missing keys raise here,
        # at import, rather than surfacing as an untimed call on the critical path.
        latency = LatencyClass(entry["latency_class"])
        schema = entry["input_schema"]
        assert_strict_compatible(schema, name)

        # FR-003 / TC-003 as a structural check: customer scoping comes from the
        # session, so no schema may accept a customer identifier at all.
        if "customer_id" in json.dumps(schema):
            raise ValueError(f"{name}: schemas must not expose customer_id")

        specs.append(
            ToolSpec(
                name=name,
                description=entry["description"],
                input_schema=schema,
                latency_class=latency,
                state_changing=bool(entry["state_changing"]),
                speculative_safe=bool(entry["speculative_safe"]),
                requires_confirmation=bool(entry.get("requires_confirmation", False)),
            )
        )

    _assert_coherent(specs)
    return tuple(specs)


def _assert_coherent(specs: list[ToolSpec]) -> None:
    """Invariants that hold across the whole tool set, not just within one entry."""
    for spec in specs:
        if spec.state_changing and spec.speculative_safe:
            # Speculation is read-only by construction. A state-changing tool marked
            # speculative-safe would let a guess create a real return.
            raise ValueError(f"{spec.name}: state-changing tools can never be speculative_safe")
    names = [s.name for s in specs]
    if len(names) != len(set(names)):
        raise ValueError("duplicate tool names in contract")


def openai_tools() -> list[dict[str, object]]:
    """Tool definitions in the wire format the LLM adapter actually sends.

    Built from `contracts/tools.schema.json`, so there is no second copy to drift. The
    schemas carry `additionalProperties: false` and an explicit `required` list, which is
    what lets a provider validate arguments before they ever reach us — our own
    validation still runs on top, because provider-side strictness is a first line, not
    a guarantee we control.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.input_schema,
            },
        }
        for s in load_specs()
    ]


def spec_for(name: str) -> ToolSpec | None:
    return next((s for s in load_specs() if s.name == name), None)
