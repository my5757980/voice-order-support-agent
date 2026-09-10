"""T013 — the import guard that makes constitution principle IV enforceable.

Principle IV says vendor SDK types must not cross an adapter boundary into the core.
That is a good intention until something checks it. This walks the AST of every module
under `src/core/` and fails if a forbidden import appears.

It is deliberately strict about `time` and `asyncio` too: those are allowed ONLY in
`clock.py`, which is the designated seam. Principle VII requires injected time, so a
direct `time.monotonic()` anywhere else in core is the exact violation that makes tests
depend on a wall clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "core"

# Vendor SDKs, network, and storage. None of these belong in the conversation engine.
FORBIDDEN_ROOTS = frozenset(
    {
        "anthropic", "elevenlabs", "assemblyai",
        "websockets", "fastapi", "uvicorn", "starlette",
        "httpx", "requests", "aiohttp", "urllib", "socket",
        "aiosqlite", "sqlite3", "sqlalchemy",
        "structlog", "logging",
    }
)

# Allowed only in the module that exists to abstract them.
TIME_ROOTS = frozenset({"time", "asyncio"})
TIME_SEAM = "clock.py"


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import — inside core, which is fine.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _core_modules() -> list[Path]:
    return sorted(p for p in CORE.rglob("*.py") if p.name != "__init__.py")


def test_core_package_exists() -> None:
    assert CORE.is_dir(), f"core package not found at {CORE}"
    assert _core_modules(), "no core modules found — the guard would vacuously pass"


def test_core_imports_no_vendor_or_io_library() -> None:
    violations: list[str] = []
    for module in _core_modules():
        roots = _imported_roots(ast.parse(module.read_text(encoding="utf-8")))
        for bad in sorted(roots & FORBIDDEN_ROOTS):
            violations.append(f"{module.relative_to(CORE)} imports {bad!r}")
    assert not violations, "core must stay vendor-free and I/O-free:\n  " + "\n  ".join(violations)


def test_time_is_injected_outside_the_clock_seam() -> None:
    violations: list[str] = []
    for module in _core_modules():
        if module.name == TIME_SEAM:
            continue
        roots = _imported_roots(ast.parse(module.read_text(encoding="utf-8")))
        for bad in sorted(roots & TIME_ROOTS):
            violations.append(f"{module.relative_to(CORE)} imports {bad!r}")
    assert not violations, (
        "time must be injected via core.clock, not read directly:\n  " + "\n  ".join(violations)
    )


def test_guard_actually_catches_a_violation(tmp_path: Path) -> None:
    """The guard is worthless if it cannot fail. Prove it detects a planted import."""
    planted = tmp_path / "bad_module.py"
    planted.write_text("import anthropic\nimport time\n", encoding="utf-8")
    roots = _imported_roots(ast.parse(planted.read_text(encoding="utf-8")))
    assert "anthropic" in roots & FORBIDDEN_ROOTS
    assert "time" in roots & TIME_ROOTS
