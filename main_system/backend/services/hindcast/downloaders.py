"""Forcing downloaders -- STUBS.

The pipeline reads ERA5 / CMEMS products from local NetCDF. Fetching them needs
accounts this repository cannot hold, so these functions document exactly what
has to be requested and then stop. Implementing them is a matter of filling in
the two TODO blocks; nothing else in the pipeline changes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import os

BBox = tuple[float, float, float, float]   # west, south, east, north


def download_era5_wind(t_start: datetime, t_end: datetime, bbox: BBox, out: Path) -> Path:
    """ERA5 hourly 10 m wind.

    TODO(credentials): set CDS_API_KEY (https://cds.climate.copernicus.eu/how-to-api)
    and accept the ERA5 licence on the dataset page, then:

        import cdsapi
        cdsapi.Client(url=os.environ["CDS_API_URL"], key=os.environ["CDS_API_KEY"]).retrieve(
            "reanalysis-era5-single-levels",
            {"product_type": "reanalysis", "format": "netcdf",
             "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
             "date": f"{t_start:%Y-%m-%d}/{t_end:%Y-%m-%d}", "time": [f"{h:02d}:00" for h in range(24)],
             "area": [bbox[3], bbox[0], bbox[1], bbox[2]]},   # N, W, S, E
            str(out))

    ERA5 lags real time by ~5 days; for a recent scene use ERA5T or a forecast product.
    """
    hint = "CDS_API_KEY is not set" if not os.getenv("CDS_API_KEY") else "downloader not implemented"
    raise NotImplementedError(f"ERA5 download: {hint}. Supply forcing.wind_path instead.")


def download_cmems(t_start: datetime, t_end: datetime, bbox: BBox, out_currents: Path,
                   out_stokes: Path) -> tuple[Path, Path]:
    """CMEMS surface currents and Stokes drift.

    TODO(credentials): set CMEMS_USERNAME / CMEMS_PASSWORD, then with `copernicusmarine`:

        copernicusmarine.subset(dataset_id="cmems_mod_glo_phy_anfc_0.083deg_PT1H-m",
            variables=["uo", "vo"], minimum_depth=0, maximum_depth=1, ...)
        copernicusmarine.subset(dataset_id="cmems_mod_glo_wav_anfc_0.083deg_PT3H-i",
            variables=["VSDX", "VSDY"], ...)   # 3-hourly: the loader interpolates to 1 h

    Analysis/forecast datasets keep ~2 years; older scenes need the multi-year
    reanalysis ids instead. Picking by scene date belongs here.
    """
    hint = "CMEMS credentials are not set" if not os.getenv("CMEMS_USERNAME") else "downloader not implemented"
    raise NotImplementedError(f"CMEMS download: {hint}. Supply forcing.current_path instead.")
