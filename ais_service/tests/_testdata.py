"""Constants and raw-CSV row builders shared by the ais_service tests."""

BBOX = [-91.0, 28.0, -89.0, 30.0]        # Gulf of Mexico-ish, offshore
START = "2023-01-01T00:00:00Z"
END = "2023-01-01T02:00:00Z"

CULPRIT_CONFIG = {
    "origin": {
        "lat": 13.0, "lon": 80.3,
        "window_start_utc": "2026-01-05T02:00:00Z",
        "window_end_utc": "2026-01-05T04:00:00Z",
    },
    "behaviour": {"slowdown": True, "ais_gap_minutes": 47},
}
GEN_BBOX = [80.1, 12.9, 80.55, 13.35]
GEN_START = "2026-01-05T00:00:00Z"
GEN_END = "2026-01-05T08:00:00Z"


def mc_rows():
    """Two well-behaved vessels plus every pathology clean.py must handle."""
    rows = []

    def add(mmsi, t, lat, lon, sog=10.0, cog=45.0, hdg=44.0, vtype=80,
            draft=8.5):
        rows.append({
            "MMSI": mmsi, "BaseDateTime": t, "LAT": lat, "LON": lon,
            "SOG": sog, "COG": cog, "Heading": hdg, "VesselName": "TEST",
            "IMO": "IMO1234567", "VesselType": vtype, "Status": 0,
            "Length": 180.0, "Width": 30.0, "Draft": draft,
            "Cargo": 80, "TransceiverClass": "A",
        })

    # vessel 1: tanker (code 80), reports every 10 min, one 40-min gap
    for i, minute in enumerate([0, 10, 20, 60, 70, 80, 90]):
        add(367000001, f"2023-01-01T{minute // 60:02d}:{minute % 60:02d}:00",
            28.5 + 0.01 * i, -90.0 + 0.01 * i)
    # vessel 2: cargo (code 70), heading 511 sentinel, SOG unavailable code,
    # COG out of range (370 -> wrapped to 10)
    for i, minute in enumerate([0, 15, 30, 45]):
        add(366000002, f"2023-01-01T00:{minute:02d}:00",
            29.0 + 0.01 * i, -90.5 + 0.01 * i,
            sog=102.3 if i == 1 else 12.0, hdg=511, vtype=70, cog=370.0)
    # duplicate fix (same mmsi, same timestamp)
    add(366000002, "2023-01-01T00:00:00", 29.0, -90.5, hdg=511, vtype=70)
    # bad MMSI (too short) -- must be dropped, not crash
    add(1234, "2023-01-01T00:05:00", 28.6, -90.1)
    # impossible SOG (80 kn, below the 102.2 unavailable code) -- row dropped
    add(367000001, "2023-01-01T01:35:00", 28.57, -89.93, sog=80.0)
    # outside bbox -- filtered by the bbox mask
    add(367000003, "2023-01-01T00:10:00", 40.0, -70.0)
    # position jump: same vessel teleports ~1.4 degrees in 5 minutes
    add(366000002, "2023-01-01T00:50:00", 29.03, -90.47, hdg=90, vtype=70)
    add(366000002, "2023-01-01T00:55:00", 28.2, -89.2, hdg=90, vtype=70)
    return rows


def dma_rows():
    rows = []
    for i, minute in enumerate([0, 10, 20, 30]):
        rows.append({
            "# Timestamp": f"01/01/2023 00:{minute:02d}:00",
            "Type of mobile": "Class A", "MMSI": 219000111,
            "Latitude": 29.2 + 0.01 * i, "Longitude": -90.2 + 0.01 * i,
            "Navigational status": "Under way using engine",
            "ROT": 0.0, "SOG": 11.0, "COG": 30.0, "Heading": 511,
            "IMO": 9000001, "Callsign": "OU1234", "Name": "DANISH TEST",
            "Ship type": "Tanker", "Cargo type": "", "Width": 30,
            "Length": 180, "Type of position fixing device": "GPS",
            "Draught": 9.1, "Destination": "TEST", "ETA": "",
            "Data source type": "AIS",
        })
    return rows
