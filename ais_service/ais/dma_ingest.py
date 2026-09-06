"""Danish Maritime Authority bulk-CSV ingest (web.ais.dk archives).

Raw columns are renamed straight to contract names at this boundary, then the
frame runs clean -> interpolate -> to_contract so the returned DataFrame meets
the frozen vessels.parquet contract exactly (14 columns, lowercase vessel
types, `timestamp_utc`, `draught_m`, `interpolated`).
"""

import pandas as pd

from .clean import clean_ais_data
from .contract import to_contract
from .interpolate import interpolate_trajectory


class DMAIngest:
    @staticmethod
    def parse_dma_csv(filepath, bbox, start_time, end_time):
        col_map = {
            'MMSI': 'mmsi',
            '# Timestamp': 'timestamp_utc',
            'Latitude': 'lat',
            'Longitude': 'lon',
            'SOG': 'sog_kn',
            'COG': 'cog_deg',
            'Heading': 'heading_deg',
            'Name': 'vessel_name',
            'IMO': 'imo',
            'Ship type': 'vessel_type',
            'Navigational status': 'status',
            'Length': 'length_m',
            'Width': 'width_m',
            'Draught': 'draught_m'
        }

        try:
            chunks = pd.read_csv(filepath, chunksize=100000)
            filtered_chunks = []

            start_dt = pd.to_datetime(start_time, utc=True)
            end_dt = pd.to_datetime(end_time, utc=True)
            lon_min, lat_min, lon_max, lat_max = bbox

            for chunk in chunks:
                # DMA specific parsing may be needed
                chunk = chunk.rename(columns=col_map)
                if 'timestamp_utc' in chunk.columns:
                    chunk['timestamp_utc'] = pd.to_datetime(
                        chunk['timestamp_utc'], utc=True, format='mixed',
                        dayfirst=True)
                elif 'Timestamp' in chunk.columns:
                    # fallback if the header is just 'Timestamp'
                    chunk['timestamp_utc'] = pd.to_datetime(
                        chunk['Timestamp'], utc=True, format='mixed',
                        dayfirst=True)
                else:
                    continue

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

            # Clean, interpolate, then project onto the frozen contract
            df = clean_ais_data(df, bbox)
            if df.empty:
                return pd.DataFrame()
            df = interpolate_trajectory(df)
            return to_contract(df, source='real')
        except Exception as e:
            # Handle specific archive missing / parse error
            raise RuntimeError(f"PARSE_ERROR: {str(e)}")
