import pandas as pd
import numpy as np

def interpolate_trajectory(df):
    """
    Interpolates latitudes/longitudes over temporal gaps to 5-min steps.
    Detects gaps >15 mins and sets gap_flag=True for interpolated points.
    """
    if df.empty:
        return df
        
    df = df.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    
    interpolated_dfs = []
    
    # Ensure imo, length, width, draft exist
    for col in ['imo', 'length_m', 'width_m', 'draft_m']:
        if col not in df.columns:
            df[col] = np.nan
            
    for mmsi, group in df.groupby('mmsi'):
        group = group.sort_values('timestamp')
        group = group.set_index('timestamp')
        
        # Determine raw intervals to find gaps > 15 mins (900 seconds)
        group['time_diff'] = group.index.to_series().diff().dt.total_seconds()
        
        # Resample to 5 minutes
        resampled = group.resample('5min').mean(numeric_only=True)
        
        # Interpolate coordinates
        resampled['lat'] = resampled['lat'].interpolate(method='linear')
        resampled['lon'] = resampled['lon'].interpolate(method='linear')
        
        # Forward fill other numeric columns
        cols_to_ffill = ['sog_kn', 'cog_deg', 'heading_deg', 'imo', 'length_m', 'width_m', 'draft_m']
        for col in cols_to_ffill:
            if col in resampled.columns:
                resampled[col] = resampled[col].ffill()
                
        # Forward fill categorical/string columns using the original group
        string_cols = ['vessel_name', 'vessel_type', 'status', 'source']
        for col in string_cols:
            if col in group.columns:
                res = group[col].reindex(resampled.index)
                res = res.ffill().bfill()
                resampled[col] = res
                
        # Keep culprit boolean
        if 'culprit' in group.columns:
            resampled['culprit'] = group['culprit'].iloc[0] if not group.empty else False
            
        resampled['mmsi'] = mmsi
        resampled['gap_flag'] = False
        
        # Identify gaps > 15 min
        gaps = group[group['time_diff'] > 900]
        for end_time, row in gaps.iterrows():
            start_time = end_time - pd.Timedelta(seconds=row['time_diff'])
            gap_mask = (resampled.index > start_time) & (resampled.index < end_time)
            resampled.loc[gap_mask, 'gap_flag'] = True
            
        # Any raw data points that fell exactly on the 5min mark won't be gaps, 
        # but anything interpolated will be caught by the >15min rule.
        # Ensure we drop any extra columns we created
        if 'time_diff' in resampled.columns:
            resampled = resampled.drop(columns=['time_diff'])
            
        resampled = resampled.reset_index()
        interpolated_dfs.append(resampled)
        
    if not interpolated_dfs:
        return df
        
    final_df = pd.concat(interpolated_dfs, ignore_index=True)
    
    # Cast dtypes
    final_df['mmsi'] = final_df['mmsi'].astype('int64')
    final_df['lat'] = final_df['lat'].astype('float64')
    final_df['lon'] = final_df['lon'].astype('float64')
    final_df['sog_kn'] = final_df['sog_kn'].astype('float32')
    final_df['cog_deg'] = final_df['cog_deg'].astype('float32')
    final_df['heading_deg'] = final_df['heading_deg'].astype('float32')
    
    # Nullable types
    final_df['imo'] = final_df['imo'].astype('Int64')
    final_df['length_m'] = final_df['length_m'].astype('float32')
    final_df['width_m'] = final_df['width_m'].astype('float32')
    final_df['draft_m'] = final_df['draft_m'].astype('float32')
    
    final_df['gap_flag'] = final_df['gap_flag'].astype(bool)
    final_df['culprit'] = final_df['culprit'].astype(bool)
    
    return final_df
