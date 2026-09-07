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
            'CallSign': 'call_sign',
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

        # Identity is lifted off here, BEFORE to_contract() projects onto the
        # frozen 14 columns and drops it. MarineCadastre publishes name, IMO
        # and call sign; the contract deliberately does not carry them, and
        # widening it would change a file five modules validate against. This
        # is the side channel: the same bytes, kept beside the contract file
        # instead of smuggled into it.
        MarineCadastreIngest.last_identities = MarineCadastreIngest.vessel_identities(df)
        return to_contract(df, source='real')

    #: Identity table from the most recent parse, keyed by MMSI. Populated as
    #: a side effect because the contract-returning signature is frozen too.
    last_identities: dict = {}

    @staticmethod
    def vessel_identities(df) -> dict:
        """One identity row per MMSI, from whatever the source actually gave.

        Never invents: a field the archive left blank stays absent rather than
        becoming an empty string, so "we do not know this vessel's name" and
        "this vessel has no name" remain different statements. Synthetic AIS
        has none of these columns at all and yields an empty table.
        """
        out = {}
        if df is None or getattr(df, "empty", True) or "mmsi" not in df.columns:
            return out
        fields = [c for c in ("vessel_name", "imo", "call_sign") if c in df.columns]
        if not fields:
            return out

        for mmsi, group in df.groupby("mmsi"):
            identity = {}
            for field in fields:
                values = group[field].dropna()
                values = values[values.astype(str).str.strip() != ""]
                if values.empty:
                    continue
                # The archive repeats identity on every fix; take the most
                # common rather than the first, so one corrupt row cannot
                # rename a vessel.
                identity[field] = str(values.mode().iloc[0]).strip()
            if identity:
                out[int(mmsi)] = identity
        return out
