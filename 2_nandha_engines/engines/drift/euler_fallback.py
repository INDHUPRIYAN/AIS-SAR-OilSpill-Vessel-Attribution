import numpy as np

def run_euler_drift(start_lat, start_lon, wind_u, wind_v, current_u, current_v, duration_hours, dt_seconds=600):
    """
    Simple numerical Euler integration drift fallback.
    Computes trajectory of floating oil elements under metocean forces.
    """
    steps = int((duration_hours * 3600) / dt_seconds)
    lat, lon = start_lat, start_lon
    trajectory = [(lat, lon)]

    # Earth radius in meters
    R = 6378137.0

    # Empirical factors: 3% wind shear, 100% current transport
    wind_factor = 0.03
    current_factor = 1.0

    for _ in range(steps):
        # Calculate velocity in m/s
        u = current_factor * current_u + wind_factor * wind_u
        v = current_factor * current_v + wind_factor * wind_v

        # Displacements in meters
        dx = u * dt_seconds
        dy = v * dt_seconds

        # Change in lat/lon (approximated)
        dlat = dy / R
        dlon = dx / (R * np.cos(np.pi * lat / 180.0))

        lat += dlat * (180.0 / np.pi)
        lon += dlon * (180.0 / np.pi)

        trajectory.append((lat, lon))

    return trajectory
