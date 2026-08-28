import pandas as pd
from .clean import clean_ais_data
from .interpolate import interpolate_trajectory

class MarineCadastreIngest:
    @staticmethod
    def parse_mc_csv(filepath, bbox, start_time, end_time):
        col_map = {
            'MMSI': 'mmsi',
            'BaseDateTime': 'timestamp',
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
            'Draft': 'draft_m'
        }
        
        chunks = pd.read_csv(filepath, chunksize=100000)
        filtered_chunks = []
        
        start_dt = pd.to_datetime(start_time, utc=True)
        end_dt = pd.to_datetime(end_time, utc=True)
        lon_min, lat_min, lon_max, lat_max = bbox
        
        for chunk in chunks:
            chunk = chunk.rename(columns=col_map)
            chunk['timestamp'] = pd.to_datetime(chunk['timestamp'], utc=True)
            
            mask = (chunk['lat'] >= lat_min) & (chunk['lat'] <= lat_max) & \
                   (chunk['lon'] >= lon_min) & (chunk['lon'] <= lon_max) & \
                   (chunk['timestamp'] >= start_dt) & (chunk['timestamp'] <= end_dt)
                   
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
                
        # Clean and interpolate
        df = clean_ais_data(df, bbox)
        df = interpolate_trajectory(df)
        
        return df
