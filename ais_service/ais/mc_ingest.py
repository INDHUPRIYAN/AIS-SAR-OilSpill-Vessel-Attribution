"""MarineCadastre bulk-CSV ingest (US waters, NOAA AISDataHandler archives).

Raw columns are renamed straight to contract names at this boundary, then the
frame runs clean -> interpolate -> to_contract so the returned DataFrame meets
the frozen vessels.parquet contract exactly (14 columns, lowercase vessel
types, `timestamp_utc`, `draught_m`, `interpolated`).
"""

import pandas as pd

from .clean import clean_ais_data
from .contract import to_contract
from .interpolate import interpolate_trajectory


class MarineCadastreIngest:
    @staticmethod
    def parse_mc_csv(filepath, bbox, start_time, end_time):
        col_map = {
            'MMSI': 'mmsi',
            'BaseDateTime': 'timestamp_utc',
            'LAT': 'lat',
            'LON': 'lon',
            'SOG': 'sog_kn',
            'COG': 'cog_deg',
            'Heading': 'heading_deg',
            'VesselName': 'vessel_name',
            'IMO': 'imo',
            'VesselType': 'vessel_type',
            'Status': 'status',
            'Length': 'length_m',
            'Width': 'width_m',
            'Draft': 'draught_m'
        }

        chunks = pd.read_csv(filepath, chunksize=100000)
        filtered_chunks = []

        start_dt = pd.to_datetime(start_time, utc=True)
        end_dt = pd.to_datetime(end_time, utc=True)
        lon_min, lat_min, lon_max, lat_max = bbox

        for chunk in chunks:
            chunk = chunk.rename(columns=col_map)
            chunk['timestamp_utc'] = pd.to_datetime(chunk['timestamp_utc'], utc=True)

            mask = (chunk['lat'] >= lat_min) & (chunk['lat'] <= lat_max) & \
                   (chunk['lon'] >= lon_min) & (chunk['lon'] <= lon_max) & \
                   (chunk['timestamp_utc'] >= start_dt) & (chunk['timestamp_utc'] <= end_dt)

            chunk = chunk[mask]

            if not chunk.empty:
                filtered_chunks.append(chunk)

        if not filtered_chunks:
            return pd.DataFrame()

        df = pd.concat(filtered_chunks, ignore_index=True)
        df['source'] = 'real'
        df['culprit'] = False

        for col in col_map.values():
            if col not in df.columns:
                df[col] = None

        # Clean, interpolate, then project onto the frozen contract
        df = clean_ais_data(df, bbox)
        if df.empty:
            return pd.DataFrame()
        df = interpolate_trajectory(df)
        return to_contract(df, source='real')
