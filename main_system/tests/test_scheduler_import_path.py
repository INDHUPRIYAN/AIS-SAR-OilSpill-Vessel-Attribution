"""The scheduler must import in the environment uvicorn actually runs in.

Audit P2/C7. `watcher.py` imports `satellite.chain` lazily, inside
`search_scenes`. The sibling services are separate import roots rather than
installed packages -- `pyproject.toml` is dependencies-only by design
(`packages = []`), so nothing puts them on `sys.path` at runtime. `pytest.ini`
declares all five roots for the test suite, so the scheduler's own tests passed
while every deployed poll raised:

    ModuleNotFoundError: No module named 'satellite'

That is the failure mode this file exists to prevent: a green suite that proves
nothing about the deployed path. So these tests deliberately run in a
subprocess with `-P` (ignore CWD) and a scrubbed PYTHONPATH, reproducing what
uvicorn sees rather than what pytest arranges.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# Python 3.10 has no `-P` / PYTHONSAFEPATH, so isolation is done explicitly:
# run from a directory that contributes nothing, clear PYTHONPATH, and drop
# the implicit cwd entry that `-c` puts at sys.path[0].
_PRELUDE = (
    "import sys, os\n"
    "sys.path[:] = [p for p in sys.path if p not in ('', os.getcwd())]\n"
    f"sys.path.insert(0, r'{REPO_ROOT / 'main_system'}')\n"
    f"sys.path.insert(0, r'{REPO_ROOT}')\n"
)


def _run_isolated(code: str) -> subprocess.CompletedProcess:
    """Execute `code` with only REPO_ROOT and main_system importable.

    That is what uvicorn gives the app (see `backend/main.py`), and notably
    NOT what `pytest.ini` gives the suite -- which is the whole reason this
    defect survived a green test run.
    """
    env = {k: v for k, v in __import__("os").environ.items() if k != "PYTHONPATH"}
    return subprocess.run(
        [sys.executable, "-c", _PRELUDE + code],
        cwd=str(REPO_ROOT.parent),
        capture_output=True, text=True, env=env, timeout=180,
    )


def test_isolation_actually_isolates():
    """Guard for the guards: if the harness leaked pytest's pythonpath, every
    test below would pass without proving anything."""
    proc = _run_isolated(
        "try:\n"
        "    import satellite  # noqa: F401\n"
        "    print('LEAKED')\n"
        "except ModuleNotFoundError:\n"
        "    print('ISOLATED')\n"
    )
    assert "ISOLATED" in proc.stdout, (
        "the subprocess could import `satellite` before the watcher bootstrapped "
        f"it -- isolation is broken, so these tests prove nothing.\n{proc.stdout}{proc.stderr}")


def test_watcher_imports_without_pytest_pythonpath():
    """The regression itself: importing the watcher module must succeed."""
    proc = _run_isolated(
        "import sys, pathlib\n"
        f"sys.path.insert(0, r'{REPO_ROOT}')\n"
        f"sys.path.insert(0, r'{REPO_ROOT / 'main_system'}')\n"
        "import backend.services.scheduler.watcher as w\n"
        "print('IMPORT_OK')\n"
    )
    assert "IMPORT_OK" in proc.stdout, (
        f"watcher failed to import in a uvicorn-like environment\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}")


def test_scene_chain_resolves_without_pytest_pythonpath():
    """The actual dependency that was missing. Importing the watcher must make
    `satellite.chain` reachable -- that is what a poll needs."""
    proc = _run_isolated(
        "import sys, pathlib\n"
        f"sys.path.insert(0, r'{REPO_ROOT}')\n"
        f"sys.path.insert(0, r'{REPO_ROOT / 'main_system'}')\n"
        "import backend.services.scheduler.watcher  # noqa: F401\n"
        "from satellite.chain import SceneRetrievalChain\n"
        "print('CHAIN_OK', SceneRetrievalChain.__name__)\n"
    )
    assert "CHAIN_OK" in proc.stdout, (
        f"satellite.chain unreachable after importing the watcher\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
    assert "No module named 'satellite'" not in proc.stderr


def test_sibling_service_roots_are_all_bootstrapped():
    """scene_service is the one that broke, but the same class of bug is one
    bare import away in the AIS and metocean roots."""
    proc = _run_isolated(
        "import sys\n"
        f"sys.path.insert(0, r'{REPO_ROOT}')\n"
        f"sys.path.insert(0, r'{REPO_ROOT / 'main_system'}')\n"
        "import backend.services.scheduler.watcher  # noqa: F401\n"
        "import sys as s\n"
        "roots = [p for p in s.path if p.endswith(('scene_service','ais_service','metocean_service'))]\n"
        "print('ROOTS', len(roots))\n"
    )
    assert "ROOTS 3" in proc.stdout, (
        f"expected all three sibling roots on sys.path\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}")


def test_bootstrap_is_idempotent():
    """Importing twice must not stack duplicate entries onto sys.path."""
    proc = _run_isolated(
        "import sys\n"
        f"sys.path.insert(0, r'{REPO_ROOT}')\n"
        f"sys.path.insert(0, r'{REPO_ROOT / 'main_system'}')\n"
        "import backend.services.scheduler.watcher\n"
        "import importlib\n"
        "importlib.reload(backend.services.scheduler.watcher)\n"
        "import sys as s\n"
        "n = sum(1 for p in s.path if p.endswith('scene_service'))\n"
        "print('COUNT', n)\n"
    )
    assert "COUNT 1" in proc.stdout, f"duplicate sys.path entries: {proc.stdout} {proc.stderr}"


def test_pyproject_stays_dependencies_only():
    """Frozen surface. Installing the roots as packages would 'fix' the import
    another way and silently change the multi-root layout every module and
    pytest.ini depend on."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "packages = []" in text, \
        "pyproject must stay dependencies-only; the import fix belongs in sys.path"
