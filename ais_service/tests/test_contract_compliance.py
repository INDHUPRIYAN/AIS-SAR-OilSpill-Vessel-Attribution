"""The service meets the frozen vessels.parquet contract AT SOURCE.

contracts/schemas/tabular.py::validate_vessels_df is the referee; if these
tests pass, the main system needs no renaming to ingest our output.
"""

import pandas as pd

from ais.dma_ingest import DMAIngest
from ais.generator import generate_synthetic_ais
from ais.mc_ingest import MarineCadastreIngest
from contracts.schemas.tabular import (REQUIRED_VESSEL_COLUMNS,
                                       validate_vessels_df)

from _testdata import (BBOX, CULPRIT_CONFIG, END, GEN_BBOX, GEN_END,
                       GEN_START, START)


def _generate(**kw):
    args = dict(bbox=GEN_BBOX, start_time=GEN_START, end_time=GEN_END,
                n_vessels=15, culprit_config=CULPRIT_CONFIG, seed=11)
    args.update(kw)
    return generate_synthetic_ais(**args)


def test_generator_output_passes_contract():
    df = _generate(n_hard_negatives=3)
    validate_vessels_df(df)          # raises on any violation


def test_generator_exact_column_set_and_order():
    df = _generate()
    assert list(df.columns) == REQUIRED_VESSEL_COLUMNS


def test_generator_dtypes_match_contract():
    df = _generate()
    assert str(df["mmsi"].dtype) == "int64"
    assert str(df["timestamp_utc"].dtype) == "datetime64[ns, UTC]"
    for c in ("lat", "lon", "sog_kn", "cog_deg", "heading_deg",
              "length_m", "width_m", "draught_m"):
        assert str(df[c].dtype) == "float64", c
    assert str(df["interpolated"].dtype) == "bool"
    assert str(df["culprit"].dtype) == "bool"


def test_generator_sorted_by_mmsi_then_time():
    df = _generate(n_hard_negatives=3)
    assert df.sort_values(["mmsi", "timestamp_utc"]).equals(df)


def test_generator_vessel_types_lowercase_contract_set():
    df = _generate(n_hard_negatives=3)
    allowed = {"tanker", "cargo", "bulk", "fishing", "passenger", "tug", "other"}
    assert set(df["vessel_type"].unique()) <= allowed


def test_mc_ingest_output_passes_contract(mc_csv):
    df = MarineCadastreIngest.parse_mc_csv(mc_csv, BBOX, START, END)
    assert not df.empty
    validate_vessels_df(df)
    assert (df["source"] == "real").all()
    assert not df["culprit"].any()          # never ground truth on real data


def test_mc_ingest_maps_numeric_type_codes(mc_csv):
    df = MarineCadastreIngest.parse_mc_csv(mc_csv, BBOX, START, END)
    types = set(df["vessel_type"].unique())
    assert "tanker" in types                # code 80
    assert "cargo" in types                 # code 70


def test_dma_ingest_output_passes_contract(dma_csv):
    df = DMAIngest.parse_dma_csv(dma_csv, BBOX, START, END)
    assert not df.empty
    validate_vessels_df(df)
    assert set(df["vessel_type"].unique()) == {"tanker"}
    assert (df["source"] == "real").all()


def test_roundtrip_through_parquet_still_validates(tmp_path):
    df = _generate(n_hard_negatives=2)
    p = tmp_path / "vessels.parquet"
    df.to_parquet(p, index=False)
    back = pd.read_parquet(p)
    # parquet may store ns as us; re-coerce like any consumer would
    back["timestamp_utc"] = pd.to_datetime(back["timestamp_utc"], utc=True)
    validate_vessels_df(back)
