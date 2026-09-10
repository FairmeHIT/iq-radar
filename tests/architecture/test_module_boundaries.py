from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "iqradar"


def _imports(module_root: Path) -> set[str]:
    imports: set[str] = set()
    for source_path in module_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
    return imports


def test_deepswe_and_reporting_have_no_cross_dependencies() -> None:
    deepswe_root = PACKAGE_ROOT / "deepswe"
    reporting_root = PACKAGE_ROOT / "reporting"

    assert deepswe_root.is_dir(), "deepswe must be an independent application module"
    assert reporting_root.is_dir(), "reporting must be an independent application module"

    deepswe_imports = _imports(deepswe_root)
    reporting_imports = _imports(reporting_root)

    assert not any(name.startswith("iqradar.reporting") for name in deepswe_imports)
    assert not any(name.startswith("iqradar.publication") for name in deepswe_imports)
    assert not any(name.startswith("iqradar.deepswe") for name in reporting_imports)


def test_only_publication_module_may_bridge_deepswe_and_reporting() -> None:
    publication_root = PACKAGE_ROOT / "publication"

    assert publication_root.is_dir(), "publication must be the explicit integration boundary"
    imports = _imports(publication_root)

    assert any(name.startswith("iqradar.deepswe") for name in imports)
    assert any(name.startswith("iqradar.reporting") for name in imports)

    unexpected_bridges: list[str] = []
    for package_root in PACKAGE_ROOT.iterdir():
        if not package_root.is_dir() or package_root.name in {
            "__pycache__",
            "api",
            "publication",
            "benchmarks",
        }:
            continue
        package_imports = _imports(package_root)
        if any(name.startswith("iqradar.deepswe") for name in package_imports) and any(
            name.startswith("iqradar.reporting") for name in package_imports
        ):
            unexpected_bridges.append(package_root.name)

    assert unexpected_bridges == []


def test_benchmarks_package_must_not_import_reporting_or_publication() -> None:
    """Benchmark adapters are part of the run-execution side; they must not
    leak into the reporting/publication boundary."""
    benchmarks_root = PACKAGE_ROOT / "benchmarks"

    assert benchmarks_root.is_dir(), "benchmarks must be a standalone package"
    imports = _imports(benchmarks_root)

    assert not any(name.startswith("iqradar.reporting") for name in imports)
    assert not any(name.startswith("iqradar.publication") for name in imports)
