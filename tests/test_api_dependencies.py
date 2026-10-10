"""The API imports only what its image installs.

Every package is installed in the development environment, so a module the
API image lacks passes every other test and then crash-loops production: a
`import numpy` in estimates.py took the API down for five minutes
(2026-10-10). This reads the API's imports and its declared dependencies.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"

# Import name -> the distribution that provides it, where they differ, and
# modules that arrive with a declared dependency.
PROVIDED_BY = {
    "aioboto3": "aioboto3",
    "botocore": "aioboto3",
    "argon2": "argon2-cffi",
    "jwt": "pyjwt",
    "ulid": "python-ulid",
    "pydantic_settings": "pydantic-settings",
    "sse_starlette": "sse-starlette",
    "starlette": "fastapi",
    "multipart": "python-multipart",
    "email_validator": "email-validator",
    "firebase_admin": "firebase-admin",
    "sqlalchemy": "sqlalchemy",
    "alembic": "alembic",
    "celery": "celery",
    "kombu": "celery",
    "uvicorn": "uvicorn",
}


def _declared() -> set[str]:
    deps = tomllib.loads((API / "pyproject.toml").read_text())["project"]["dependencies"]
    names = set()
    for spec in deps:
        name = spec.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip().lower()
        names.add(name)
    return names


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_every_third_party_import_is_a_declared_dependency() -> None:
    declared = _declared()
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    missing: dict[str, list[str]] = {}
    for path in sorted((API / "src" / "visiovox_api").rglob("*.py")):
        for module in _imports(path) - stdlib - {"visiovox_api"}:
            dist = PROVIDED_BY.get(module, module.replace("_", "-")).lower()
            if dist not in declared:
                missing.setdefault(module, []).append(path.name)
    assert not missing, f"imported by the API but not in apps/api/pyproject.toml: {missing}"
