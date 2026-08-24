"""
Pure-Python NetCDF-3 Classic Binary Generator.
Creates standards-compliant CF-1.8 NetCDF files (currents.nc, wind.nc)
with strict dimension ordering (time, lat, lon), variables (uo, vo, u10, v10),
WGS84 coordinate reference systems, and IEEE-754 binary floating point data.
"""

from datetime import datetime, timezone
import struct
from typing import Any, Dict, List, Tuple


# NetCDF-3 Header Constants
NC_DIMENSION = 0x0000000A
NC_VARIABLE = 0x0000000B
NC_ATTRIBUTE = 0x0000000C

NC_BYTE = 1
NC_CHAR = 2
NC_SHORT = 3
NC_INT = 4
NC_FLOAT = 5
NC_DOUBLE = 6


def _pad4(data: bytes) -> bytes:
    """Pad bytes to 4-byte boundary."""
    rem = len(data) % 4
    if rem != 0:
        return data + (b"\x00" * (4 - rem))
    return data


def _write_nc_string(s: str) -> bytes:
    """Write length-prefixed 4-byte-padded string."""
    encoded = s.encode("utf-8")
    length = len(encoded)
    return struct.pack(">I", length) + _pad4(encoded)


def _write_nc_attr(name: str, value: str) -> bytes:
    """Write NC_CHAR attribute."""
    encoded = value.encode("utf-8")
    length = len(encoded)
    return (
        _write_nc_string(name)
        + struct.pack(">II", NC_CHAR, length)
        + _pad4(encoded)
    )


def write_currents_netcdf(
    filepath: str,
    lats: List[float],
    lons: List[float],
    time_strs: List[str],
    uo_data: List[List[List[float]]],  # [time][lat][lon]
    vo_data: List[List[List[float]]],  # [time][lat][lon]
    title: str = "OceanTrace Normalized Surface Currents",
    provider: str = "Copernicus Marine Service (CMEMS)",
) -> None:
    """Write standardized currents.nc with uo and vo variables."""
    n_time = len(time_strs)
    n_lat = len(lats)
    n_lon = len(lons)

    # Convert times to hours since first timestamp
    t0 = datetime.fromisoformat(time_strs[0].replace("Z", "+00:00"))
    time_vals = [
        (datetime.fromisoformat(ts.replace("Z", "+00:00")) - t0).total_seconds() / 3600.0
        for ts in time_strs
    ]

    header_parts = []
    # Magic bytes for NetCDF-3 64-bit offset format (CDF\x02)
    header_parts.append(b"CDF\x02")
    header_parts.append(struct.pack(">I", n_time))  # numrecs

    # 1. Dimensions list
    header_parts.append(struct.pack(">II", NC_DIMENSION, 3))
    header_parts.append(_write_nc_string("time") + struct.pack(">I", 0))  # unlimited record dim
    header_parts.append(_write_nc_string("lat") + struct.pack(">I", n_lat))
    header_parts.append(_write_nc_string("lon") + struct.pack(">I", n_lon))

    # 2. Global Attributes list
    g_attrs = [
        ("title", title),
        ("provider", provider),
        ("crs", "EPSG:4326 / WGS84"),
        ("conventions", "CF-1.8"),
        ("history", f"Created by OceanTrace Metocean Service at {datetime.now(timezone.utc).isoformat()}"),
    ]
    header_parts.append(struct.pack(">II", NC_ATTRIBUTE, len(g_attrs)))
    for k, v in g_attrs:
        header_parts.append(_write_nc_attr(k, v))

    # 3. Variables list (lat, lon, time, uo, vo)
    # Variable 0: lat (non-record: 1D [lat])
    # Variable 1: lon (non-record: 1D [lon])
    # Variable 2: time (record: 1D [time])
    # Variable 3: uo (record: 3D [time, lat, lon])
    # Variable 4: vo (record: 3D [time, lat, lon])
    header_parts.append(struct.pack(">II", NC_VARIABLE, 5))

    # lat var
    lat_attrs = [("long_name", "latitude"), ("standard_name", "latitude"), ("units", "degrees_north"), ("axis", "Y")]
    lat_hdr = _write_nc_string("lat") + struct.pack(">II", 1, 1)  # 1 dim (index 1 = lat)
    lat_hdr += struct.pack(">II", NC_ATTRIBUTE, len(lat_attrs))
    for k, v in lat_attrs:
        lat_hdr += _write_nc_attr(k, v)
    lat_hdr += struct.pack(">II", NC_FLOAT, n_lat * 4)

    # lon var
    lon_attrs = [("long_name", "longitude"), ("standard_name", "longitude"), ("units", "degrees_east"), ("axis", "X")]
    lon_hdr = _write_nc_string("lon") + struct.pack(">II", 1, 2)  # 1 dim (index 2 = lon)
    lon_hdr += struct.pack(">II", NC_ATTRIBUTE, len(lon_attrs))
    for k, v in lon_attrs:
        lon_hdr += _write_nc_attr(k, v)
    lon_hdr += struct.pack(">II", NC_FLOAT, n_lon * 4)

    # time var
    time_attrs = [
        ("long_name", "time"),
        ("standard_name", "time"),
        ("units", f"hours since {time_strs[0]}"),
        ("calendar", "standard"),
        ("axis", "T"),
    ]
    time_hdr = _write_nc_string("time") + struct.pack(">II", 1, 0)  # 1 dim (index 0 = time)
    time_hdr += struct.pack(">II", NC_ATTRIBUTE, len(time_attrs))
    for k, v in time_attrs:
        time_hdr += _write_nc_attr(k, v)
    time_hdr += struct.pack(">II", NC_FLOAT, 4)  # size per record

    # uo var
    uo_attrs = [
        ("long_name", "Eastward surface current velocity"),
        ("standard_name", "eastward_sea_water_velocity"),
        ("units", "m/s"),
        ("valid_min", "-10.0"),
        ("valid_max", "10.0"),
    ]
    uo_hdr = _write_nc_string("uo") + struct.pack(">IIII", 3, 0, 1, 2)  # 3 dims [0=time, 1=lat, 2=lon]
    uo_hdr += struct.pack(">II", NC_ATTRIBUTE, len(uo_attrs))
    for k, v in uo_attrs:
        uo_hdr += _write_nc_attr(k, v)
    uo_hdr += struct.pack(">II", NC_FLOAT, n_lat * n_lon * 4)  # size per record

    # vo var
    vo_attrs = [
        ("long_name", "Northward surface current velocity"),
        ("standard_name", "northward_sea_water_velocity"),
        ("units", "m/s"),
        ("valid_min", "-10.0"),
        ("valid_max", "10.0"),
    ]
    vo_hdr = _write_nc_string("vo") + struct.pack(">IIII", 3, 0, 1, 2)  # 3 dims [0=time, 1=lat, 2=lon]
    vo_hdr += struct.pack(">II", NC_ATTRIBUTE, len(vo_attrs))
    for k, v in vo_attrs:
        vo_hdr += _write_nc_attr(k, v)
    vo_hdr += struct.pack(">II", NC_FLOAT, n_lat * n_lon * 4)  # size per record

    # Placeholder offsets (8 bytes each for CDF-2)
    # Calculate exact offsets
    hdr_bytes_pre = b"".join(header_parts)
    var_hdrs = [lat_hdr, lon_hdr, time_hdr, uo_hdr, vo_hdr]
    # Each var header has offset appended (8 bytes for CDF2)
    hdr_total_len = len(hdr_bytes_pre) + sum(len(h) + 8 for h in var_hdrs)
    # Align data start to 4-byte boundary
    data_start = (hdr_total_len + 3) & ~3

    lat_offset = data_start
    lon_offset = lat_offset + (_pad4(struct.pack(f">{n_lat}f", *lats)).__len__())

    record_start = lon_offset + (_pad4(struct.pack(f">{n_lon}f", *lons)).__len__())
    time_offset = record_start
    uo_offset = time_offset + 4
    vo_offset = uo_offset + (_pad4(struct.pack(f">{n_lat * n_lon}f", *([0.0]*(n_lat*n_lon)))).__len__())
    rec_size = (vo_offset + (_pad4(struct.pack(f">{n_lat * n_lon}f", *([0.0]*(n_lat*n_lon)))).__len__())) - record_start

    # Assemble header with exact 64-bit offsets
    final_var_hdrs = []
    offsets = [lat_offset, lon_offset, time_offset, uo_offset, vo_offset]
    for h, off in zip(var_hdrs, offsets):
        final_var_hdrs.append(h + struct.pack(">Q", off))

    full_hdr = hdr_bytes_pre + b"".join(final_var_hdrs)
    full_hdr = _pad4(full_hdr)

    # Binary data payload
    data_bytes = bytearray(full_hdr)
    # Fill up to data_start
    if len(data_bytes) < data_start:
        data_bytes.extend(b"\x00" * (data_start - len(data_bytes)))

    # Write non-record variables
    data_bytes.extend(_pad4(struct.pack(f">{n_lat}f", *lats)))
    data_bytes.extend(_pad4(struct.pack(f">{n_lon}f", *lons)))

    # Write record variables for each timestep
    for t_idx in range(n_time):
        # time
        data_bytes.extend(struct.pack(">f", time_vals[t_idx]))
        # uo
        flat_uo = [uo_data[t_idx][i][j] for i in range(n_lat) for j in range(n_lon)]
        data_bytes.extend(_pad4(struct.pack(f">{n_lat * n_lon}f", *flat_uo)))
        # vo
        flat_vo = [vo_data[t_idx][i][j] for i in range(n_lat) for j in range(n_lon)]
        data_bytes.extend(_pad4(struct.pack(f">{n_lat * n_lon}f", *flat_vo)))

    with open(filepath, "wb") as f:
        f.write(data_bytes)


def write_wind_netcdf(
    filepath: str,
    lats: List[float],
    lons: List[float],
    time_strs: List[str],
    u10_data: List[List[List[float]]],  # [time][lat][lon]
    v10_data: List[List[List[float]]],  # [time][lat][lon]
    title: str = "OceanTrace Normalized 10m Atmospheric Winds",
    provider: str = "ECMWF ERA5 Reanalysis (CDS API)",
) -> None:
    """Write standardized wind.nc with u10 and v10 variables."""
    n_time = len(time_strs)
    n_lat = len(lats)
    n_lon = len(lons)

    t0 = datetime.fromisoformat(time_strs[0].replace("Z", "+00:00"))
    time_vals = [
        (datetime.fromisoformat(ts.replace("Z", "+00:00")) - t0).total_seconds() / 3600.0
        for ts in time_strs
    ]

    header_parts = []
    header_parts.append(b"CDF\x02")
    header_parts.append(struct.pack(">I", n_time))

    # 1. Dimensions list
    header_parts.append(struct.pack(">II", NC_DIMENSION, 3))
    header_parts.append(_write_nc_string("time") + struct.pack(">I", 0))
    header_parts.append(_write_nc_string("lat") + struct.pack(">I", n_lat))
    header_parts.append(_write_nc_string("lon") + struct.pack(">I", n_lon))

    # 2. Global Attributes list
    g_attrs = [
        ("title", title),
        ("provider", provider),
        ("crs", "EPSG:4326 / WGS84"),
        ("conventions", "CF-1.8"),
        ("history", f"Created by OceanTrace Metocean Service at {datetime.now(timezone.utc).isoformat()}"),
    ]
    header_parts.append(struct.pack(">II", NC_ATTRIBUTE, len(g_attrs)))
    for k, v in g_attrs:
        header_parts.append(_write_nc_attr(k, v))

    # 3. Variables list (lat, lon, time, u10, v10)
    header_parts.append(struct.pack(">II", NC_VARIABLE, 5))

    lat_attrs = [("long_name", "latitude"), ("standard_name", "latitude"), ("units", "degrees_north"), ("axis", "Y")]
    lat_hdr = _write_nc_string("lat") + struct.pack(">II", 1, 1)
    lat_hdr += struct.pack(">II", NC_ATTRIBUTE, len(lat_attrs))
    for k, v in lat_attrs:
        lat_hdr += _write_nc_attr(k, v)
    lat_hdr += struct.pack(">II", NC_FLOAT, n_lat * 4)

    lon_attrs = [("long_name", "longitude"), ("standard_name", "longitude"), ("units", "degrees_east"), ("axis", "X")]
    lon_hdr = _write_nc_string("lon") + struct.pack(">II", 1, 2)
    lon_hdr += struct.pack(">II", NC_ATTRIBUTE, len(lon_attrs))
    for k, v in lon_attrs:
        lon_hdr += _write_nc_attr(k, v)
    lon_hdr += struct.pack(">II", NC_FLOAT, n_lon * 4)

    time_attrs = [
        ("long_name", "time"),
        ("standard_name", "time"),
        ("units", f"hours since {time_strs[0]}"),
        ("calendar", "standard"),
        ("axis", "T"),
    ]
    time_hdr = _write_nc_string("time") + struct.pack(">II", 1, 0)
    time_hdr += struct.pack(">II", NC_ATTRIBUTE, len(time_attrs))
    for k, v in time_attrs:
        time_hdr += _write_nc_attr(k, v)
    time_hdr += struct.pack(">II", NC_FLOAT, 4)

    u10_attrs = [
        ("long_name", "10 metre U wind component"),
        ("standard_name", "eastward_wind"),
        ("units", "m/s"),
        ("valid_min", "-100.0"),
        ("valid_max", "100.0"),
    ]
    u10_hdr = _write_nc_string("u10") + struct.pack(">IIII", 3, 0, 1, 2)
    u10_hdr += struct.pack(">II", NC_ATTRIBUTE, len(u10_attrs))
    for k, v in u10_attrs:
        u10_hdr += _write_nc_attr(k, v)
    u10_hdr += struct.pack(">II", NC_FLOAT, n_lat * n_lon * 4)

    v10_attrs = [
        ("long_name", "10 metre V wind component"),
        ("standard_name", "northward_wind"),
        ("units", "m/s"),
        ("valid_min", "-100.0"),
        ("valid_max", "100.0"),
    ]
    v10_hdr = _write_nc_string("v10") + struct.pack(">IIII", 3, 0, 1, 2)
    v10_hdr += struct.pack(">II", NC_ATTRIBUTE, len(v10_attrs))
    for k, v in v10_attrs:
        v10_hdr += _write_nc_attr(k, v)
    v10_hdr += struct.pack(">II", NC_FLOAT, n_lat * n_lon * 4)

    hdr_bytes_pre = b"".join(header_parts)
    var_hdrs = [lat_hdr, lon_hdr, time_hdr, u10_hdr, v10_hdr]
    hdr_total_len = len(hdr_bytes_pre) + sum(len(h) + 8 for h in var_hdrs)
    data_start = (hdr_total_len + 3) & ~3

    lat_offset = data_start
    lon_offset = lat_offset + (_pad4(struct.pack(f">{n_lat}f", *lats)).__len__())

    record_start = lon_offset + (_pad4(struct.pack(f">{n_lon}f", *lons)).__len__())
    time_offset = record_start
    u10_offset = time_offset + 4
    v10_offset = u10_offset + (_pad4(struct.pack(f">{n_lat * n_lon}f", *([0.0]*(n_lat*n_lon)))).__len__())

    final_var_hdrs = []
    offsets = [lat_offset, lon_offset, time_offset, u10_offset, v10_offset]
    for h, off in zip(var_hdrs, offsets):
        final_var_hdrs.append(h + struct.pack(">Q", off))

    full_hdr = hdr_bytes_pre + b"".join(final_var_hdrs)
    full_hdr = _pad4(full_hdr)

    data_bytes = bytearray(full_hdr)
    if len(data_bytes) < data_start:
        data_bytes.extend(b"\x00" * (data_start - len(data_bytes)))

    data_bytes.extend(_pad4(struct.pack(f">{n_lat}f", *lats)))
    data_bytes.extend(_pad4(struct.pack(f">{n_lon}f", *lons)))

    for t_idx in range(n_time):
        data_bytes.extend(struct.pack(">f", time_vals[t_idx]))
        flat_u = [u10_data[t_idx][i][j] for i in range(n_lat) for j in range(n_lon)]
        data_bytes.extend(_pad4(struct.pack(f">{n_lat * n_lon}f", *flat_u)))
        flat_v = [v10_data[t_idx][i][j] for i in range(n_lat) for j in range(n_lon)]
        data_bytes.extend(_pad4(struct.pack(f">{n_lat * n_lon}f", *flat_v)))

    with open(filepath, "wb") as f:
        f.write(data_bytes)
