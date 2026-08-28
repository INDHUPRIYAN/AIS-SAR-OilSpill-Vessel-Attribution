import pandas as pd
import numpy as np

def haversine_distance(lat1, lon1, lat2, lon2):
    # Radius of earth in km
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c

def clean_ais_data(df, bbox=None):
    """
    Deduplicates and orders by MMSI and timestamp.
    Drops impossible positions and physically impossible jumps.
    """
    if df.empty:
        return df

    # Deduplicate by mmsi and timestamp
    df = df.drop_duplicates(subset=['mmsi', 'timestamp']).copy()
    
    # Sort
    df = df.sort_values(['mmsi', 'timestamp'])

    # Valid coordinates
    valid_coords = (df['lat'] >= -90) & (df['lat'] <= 90) & \
                   (df['lon'] >= -180) & (df['lon'] <= 180)
    df = df[valid_coords]

    if bbox:
        lon_min, lat_min, lon_max, lat_max = bbox
        in_bbox = (df['lon'] >= lon_min) & (df['lon'] <= lon_max) & \
                  (df['lat'] >= lat_min) & (df['lat'] <= lat_max)
        df = df[in_bbox]

    # Calculate implied speed (knots)
    # 1 km = 0.539957 nautical miles
    df['prev_lat'] = df.groupby('mmsi')['lat'].shift(1)
    df['prev_lon'] = df.groupby('mmsi')['lon'].shift(1)
    df['prev_time'] = df.groupby('mmsi')['timestamp'].shift(1)
    
    dist_km = haversine_distance(df['prev_lat'], df['prev_lon'], df['lat'], df['lon'])
    dist_nm = dist_km * 0.539957
    
    time_diff_hours = (df['timestamp'] - df['prev_time']).dt.total_seconds() / 3600.0
    
    implied_speed_knots = np.where(time_diff_hours > 0, dist_nm / time_diff_hours, 0)
    
    # Mask out impossible jumps (> 60 knots)
    # The first point of each vessel has NaN prev_lat, so implied speed is NaN, we keep it.
    valid_jumps = (np.isnan(implied_speed_knots)) | (implied_speed_knots <= 60)
    df = df[valid_jumps]
    
    df = df.drop(columns=['prev_lat', 'prev_lon', 'prev_time'])
    
    return df
