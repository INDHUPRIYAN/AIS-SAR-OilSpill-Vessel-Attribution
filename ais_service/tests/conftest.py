"""Shared fixtures: tiny synthetic raw-CSV archives (nothing is downloaded)."""

import sys
from pathlib import Path

import pandas as pd
import pytest

# Repo root, module root and this tests dir; pytest.ini's pythonpath covers
# the first two in a normal run, this also covers direct IDE runs and makes
# `import _testdata` work under --import-mode=importlib.
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
for p in (str(_REPO), str(_REPO / "ais_service"), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import _testdata  # noqa: E402


@pytest.fixture()
def mc_csv(tmp_path):
    path = tmp_path / "mc_raw.csv"
    pd.DataFrame(_testdata.mc_rows()).to_csv(path, index=False)
    return path


@pytest.fixture()
def dma_csv(tmp_path):
    path = tmp_path / "dma_raw.csv"
    pd.DataFrame(_testdata.dma_rows()).to_csv(path, index=False)
    return path
