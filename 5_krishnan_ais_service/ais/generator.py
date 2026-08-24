import pandas as pd
import numpy as np

def generate_synthetic_ais(bbox, start_time, end_time, n_vessels, culprit_config=None, seed=42):
    """
    Generates synthetic lane traffic matching schema, with source='synthetic'.
    Plants one culprit vessel whose track passes through culprit_config origin/time.
    """
    if seed is not None:
        np.random.seed(seed)
        
    start_dt = pd.to_datetime(start_time, utc=True)
    end_dt = pd.to_datetime(end_time, utc=True)
    
    lon_min, lat_min, lon_max, lat_max = bbox
    
    vessels = []
    times = pd.date_range(start=start_dt, end=end_dt, freq='5min')
    
    for i in range(n_vessels):
        mmsi = 900000000 + i
        is_culprit = (i == 0) and (culprit_config is not None)
        
        if is_culprit:
            origin = culprit_config['origin']
            o_lat, o_lon = origin['lat'], origin['lon']
            w_start = pd.to_datetime(origin['window_start_utc'], utc=True)
            w_end = pd.to_datetime(origin['window_end_utc'], utc=True)
            
            pass_time = w_start + (w_end - w_start) / 2
            
            lat_arr = np.linspace(o_lat - 0.2, o_lat + 0.2, len(times))
            lon_arr = np.linspace(o_lon - 0.2, o_lon + 0.2, len(times))
            
            gap_flag_arr = np.zeros(len(times), dtype=bool)
            sog_arr = np.full(len(times), 14.0)
            
            if 'behaviour' in culprit_config:
                beh = culprit_config['behaviour']
                if beh.get('ais_gap_minutes'):
                    gap_mins = beh['ais_gap_minutes']
                    gap_start = pass_time - pd.Timedelta(minutes=gap_mins/2)
                    gap_end = pass_time + pd.Timedelta(minutes=gap_mins/2)
                    gap_mask = (times >= gap_start) & (times <= gap_end)
                    gap_flag_arr[gap_mask] = True
                    
                if beh.get('slowdown'):
                    slow_mask = (times >= pass_time - pd.Timedelta(minutes=30)) & (times <= pass_time + pd.Timedelta(minutes=30))
                    sog_arr[slow_mask] = 6.0
                    
        else:
            lat_arr = np.linspace(np.random.uniform(lat_min, lat_max), np.random.uniform(lat_min, lat_max), len(times))
            lon_arr = np.linspace(np.random.uniform(lon_min, lon_max), np.random.uniform(lon_min, lon_max), len(times))
            sog_arr = np.full(len(times), np.random.uniform(10, 20))
            gap_flag_arr = np.zeros(len(times), dtype=bool)
            
        df = pd.DataFrame({
            'mmsi': mmsi,
            'timestamp': times,
            'lat': lat_arr,
            'lon': lon_arr,
            'sog_kn': sog_arr,
            'cog_deg': np.random.uniform(0, 360, len(times)),
            'heading_deg': np.random.uniform(0, 360, len(times)),
            'vessel_name': f"SynthVessel_{mmsi}",
            'imo': mmsi + 1000,
            'vessel_type': np.random.choice(["Tanker", "Cargo", "Fishing", "Passenger"]),
            'length_m': np.random.uniform(50, 300),
            'width_m': np.random.uniform(10, 50),
            'draft_m': np.random.uniform(5, 15),
            'status': "Under way using engine",
            'gap_flag': gap_flag_arr,
            'source': 'synthetic',
            'culprit': is_culprit
        })
        vessels.append(df)
        
    if not vessels:
        return pd.DataFrame()
        
    final_df = pd.concat(vessels, ignore_index=True)
    
    final_df['mmsi'] = final_df['mmsi'].astype('int64')
    final_df['lat'] = final_df['lat'].astype('float64')
    final_df['lon'] = final_df['lon'].astype('float64')
    final_df['sog_kn'] = final_df['sog_kn'].astype('float32')
    final_df['cog_deg'] = final_df['cog_deg'].astype('float32')
    final_df['heading_deg'] = final_df['heading_deg'].astype('float32')
    final_df['imo'] = final_df['imo'].astype('Int64')
    final_df['length_m'] = final_df['length_m'].astype('float32')
    final_df['width_m'] = final_df['width_m'].astype('float32')
    final_df['draft_m'] = final_df['draft_m'].astype('float32')
    final_df['gap_flag'] = final_df['gap_flag'].astype(bool)
    final_df['culprit'] = final_df['culprit'].astype(bool)
    
    return final_df
