"""Live AIS: current vessel picture, stream control and archive coverage.

Three things here are deliberate.

**Viewport decimation never drops a vessel.** `/ais/live` caps how many rows
it returns and, when the cap bites, it says so in `truncated` and
`total_in_view`. It does not silently return the first 500. The same rule the
SAR tile server follows (P18): thinning detail is fine, hiding an object is
not, because the first casualty of silent culling is always the thing the
operator was looking for.

**The archive span is reported, not assumed.** `/ais/status` states when live
ingestion actually began on this deployment. A Bay of Bengal investigation
into an acquisition from before that has no live AIS, and the honest answer is
the span, not an empty result set.

**Starting and stopping the stream is an admin action.** It opens an outbound
connection and writes continuously to disk. An analyst can read the picture;
only an administrator turns the tap on.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.authz import current_user, require_role
from backend.models.db import (AisLiveState, AisStreamSession, User, Zone,
                               get_db, utcnow)
from backend.services import audit as audit_service
from backend.services import ais_live as worker_service

router = APIRouter(tags=["ais"])

# Above this many vessels a browser stops being able to draw them usefully and
# the JSON itself becomes the bottleneck. The cap is reported when it bites.
MAX_LIVE_ROWS = 4000
DEFAULT_LIVE_ROWS = 1500


def _row(row: AisLiveState) -> dict:
    """One live vessel. Nulls stay null -- see `AisLiveState`."""
    return {
        "mmsi": row.mmsi,
        "lat": row.lat,
        "lon": row.lon,
        "sog_kn": row.sog_kn,
        "cog_deg": row.cog_deg,
        "heading_deg": row.heading_deg,
        "nav_status": row.nav_status,
        "vessel_name": row.vessel_name,
        "callsign": row.callsign,
        "imo": row.imo,
        "vessel_type": row.vessel_type,
        "length_m": row.length_m,
        "width_m": row.width_m,
        "draught_m": row.draught_m,
        "destination": row.destination,
        "report_utc": row.report_utc,
        "received_utc": row.received_utc,
        "first_seen_utc": row.first_seen_utc,
        "message_count": row.message_count,
        "zone_id": row.zone_id,
        "source": row.source,
        "provider": row.provider,
    }


@router.get("/ais/live")
def live_vessels(bbox: Optional[str] = Query(
                     None, description="lon_min,lat_min,lon_max,lat_max"),
                 zone_id: Optional[str] = None,
                 vessel_type: Optional[str] = None,
                 max_age_minutes: int = Query(60, ge=1, le=1440),
                 limit: int = Query(DEFAULT_LIVE_ROWS, ge=1, le=MAX_LIVE_ROWS),
                 db: Session = Depends(get_db),
                 _user: User = Depends(current_user)):
    """The current vessel picture.

    `max_age_minutes` is what keeps this a LIVE picture. A vessel last heard
    from two hours ago is not where the row says it is, so the default window
    is an hour and the response states the window it used -- a marker whose
    age the client cannot see is a stale reading presented as current.
    """
    cutoff = worker_service._naive(
        utcnow() - timedelta(minutes=max_age_minutes))
    q = db.query(AisLiveState).filter(AisLiveState.report_utc >= cutoff)

    if zone_id:
        q = q.filter(AisLiveState.zone_id == zone_id)
    if vessel_type:
        q = q.filter(AisLiveState.vessel_type == vessel_type)
    if bbox:
        try:
            parts = [float(v) for v in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
        except ValueError:
            raise HTTPException(
                422, "bbox must be lon_min,lat_min,lon_max,lat_max "
                     "-- longitude first")
        lon_min, lat_min = min(parts[0], parts[2]), min(parts[1], parts[3])
        lon_max, lat_max = max(parts[0], parts[2]), max(parts[1], parts[3])
        q = (q.filter(AisLiveState.lon >= lon_min)
             .filter(AisLiveState.lon <= lon_max)
             .filter(AisLiveState.lat >= lat_min)
             .filter(AisLiveState.lat <= lat_max))

    total = q.count()
    rows = q.order_by(AisLiveState.report_utc.desc()).limit(limit).all()

    return {
        "vessels": [_row(r) for r in rows],
        "count": len(rows),
        "total_in_view": total,
        # Stated, never silent. A client that received 1500 of 4200 vessels
        # must be able to tell.
        "truncated": total > len(rows),
        "max_age_minutes": max_age_minutes,
        "as_of_utc": utcnow(),
        "provider": "AISStream",
        "source": "real",
    }


@router.get("/ais/live/geojson")
def live_geojson(bbox: Optional[str] = Query(None),
                 zone_id: Optional[str] = None,
                 max_age_minutes: int = Query(60, ge=1, le=1440),
                 limit: int = Query(DEFAULT_LIVE_ROWS, ge=1, le=MAX_LIVE_ROWS),
                 db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """The same picture as GeoJSON, for the globe and the 2D map."""
    payload = live_vessels(bbox=bbox, zone_id=zone_id, vessel_type=None,
                           max_age_minutes=max_age_minutes, limit=limit,
                           db=db, _user=user)
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [v["lon"], v["lat"]]},
        "properties": {k: val for k, val in v.items()
                       if k not in ("lon", "lat")},
    } for v in payload["vessels"]]
    return {"type": "FeatureCollection", "features": features,
            "total_in_view": payload["total_in_view"],
            "truncated": payload["truncated"],
            "as_of_utc": payload["as_of_utc"]}


@router.get("/ais/live/{mmsi}")
def live_vessel(mmsi: int, db: Session = Depends(get_db),
                _user: User = Depends(current_user)):
    row = db.get(AisLiveState, mmsi)
    if row is None:
        raise HTTPException(
            404, f"MMSI {mmsi} is not in the live picture. It may never have "
                 f"been received, or may have stopped transmitting long "
                 f"enough to be pruned -- its observations remain in the AIS "
                 f"archive either way.")
    return _row(row)


@router.get("/ais/status")
def stream_status(db: Session = Depends(get_db),
                  _user: User = Depends(current_user)):
    """Stream health, ingest counters and the real archive span.

    `functionally_working` is reported separately from `connected`: a socket
    that connected and has received nothing is REACHABLE and not working, and
    conflating the two is what standing rule 8 exists to prevent.
    """
    worker = worker_service.get_worker()
    status = worker.status()

    sessions = (db.query(AisStreamSession)
                .order_by(AisStreamSession.started_utc.desc())
                .limit(10).all())
    live_count = db.query(AisLiveState).count()
    oldest = db.query(func.min(AisStreamSession.started_utc)).scalar()

    archive = _archive_span(worker)

    return {
        "stream": status,
        "live_vessels": live_count,
        "live_ttl_hours": worker_service.LIVE_TTL_HOURS,
        # When ingestion FIRST ran here. This is the honest answer to "does
        # the archive cover my scene" -- anything before it does not.
        "ingestion_began_utc": oldest,
        "archive": archive,
        "recent_sessions": [{
            "id": s.id,
            "started_utc": s.started_utc,
            "ended_utc": s.ended_utc,
            "status": s.status,
            "messages_received": s.messages_received,
            "positions_stored": s.positions_stored,
            "statics_stored": s.statics_stored,
            "archived_rows": s.archived_rows,
            "rejects": (json.loads(s.rejects_json) if s.rejects_json else None),
            "first_message_utc": s.first_message_utc,
            "last_message_utc": s.last_message_utc,
            "reconnect_attempt": s.reconnect_attempt,
            "error_class": s.error_class,
            "error_detail": s.error_detail,
            # The redacted subscription. The single most useful field when a
            # stream connects and delivers nothing.
            "subscription": (json.loads(s.subscription_json)
                             if s.subscription_json else None),
            # A session that connected and received zero messages, called what
            # it is rather than counted as a success.
            "connected_but_silent": bool(
                s.status in ("connected", "disconnected")
                and not s.messages_received),
        } for s in sessions],
    }


def _archive_span(worker) -> dict:
    """What the live-AIS archive actually holds.

    Read from the store's own manifest rather than inferred from session rows:
    a session that ran for an hour and failed every flush archived nothing, and
    reporting its window as archive coverage would promise data that is not
    there.
    """
    try:
        from ais.index import AISStore
    except ImportError as exc:
        return {"available": False,
                "reason": f"AIS store unavailable: {exc}"}
    try:
        store = AISStore(worker.archive_root, granularity="day")
        parts = store.partitions(refresh=True)
    except Exception as exc:                       # noqa: BLE001
        return {"available": False,
                "reason": f"{type(exc).__name__}: {exc}"}

    if not parts:
        return {"available": True, "partitions": 0, "rows": 0,
                "note": "no live AIS has been archived on this deployment yet"}
    return {
        "available": True,
        "partitions": len(parts),
        "rows": sum(int(p.rows or 0) for p in parts),
        "regions": sorted({p.region for p in parts}),
        "t_start": min(p.t_start for p in parts),
        "t_end": max(p.t_end for p in parts),
        "root": str(worker.archive_root),
    }


@router.post("/ais/stream/start",
             dependencies=[Depends(require_role("admin"))])
def start_stream(request: Request, db: Session = Depends(get_db),
                 _user: User = Depends(current_user)):
    """Open the subscription.

    Subscribes to the active operational zones' bounding boxes, so drawing a
    zone is what extends coverage -- there is no separate list of boxes to keep
    in sync with the zone table.
    """
    worker = worker_service.get_worker()
    result = worker.start()
    audit_service.record(
        db, "ais.stream.start", request=request, resource="provider:AISStream",
        detail=json.dumps({"started": result.get("started"),
                           "reason": result.get("reason"),
                           "bboxes": result.get("bboxes")}, default=str))
    if not result.get("started") and result.get("reason"):
        # 409, not 500: "no key configured" and "already running" are states of
        # the system, not server errors, and the body names which.
        raise HTTPException(409, detail=result["reason"])
    return result


@router.post("/ais/stream/stop",
             dependencies=[Depends(require_role("admin"))])
def stop_stream(request: Request, db: Session = Depends(get_db),
                _user: User = Depends(current_user)):
    worker = worker_service.get_worker()
    result = worker.stop()
    audit_service.record(
        db, "ais.stream.stop", request=request, resource="provider:AISStream",
        detail=json.dumps(result.get("final_flush"), default=str))
    return result


@router.post("/ais/stream/flush",
             dependencies=[Depends(require_role("admin"))])
def flush_archive(_user: User = Depends(current_user)):
    """Force the buffered observations into the archive now.

    Exists because the automatic flush is on a five-minute timer and a demo
    should not have to wait for it to prove the archive is real.
    """
    return worker_service.get_worker().flush_archive()


@router.post("/ais/live/prune",
             dependencies=[Depends(require_role("admin"))])
def prune_live(ttl_hours: int = Query(worker_service.LIVE_TTL_HOURS,
                                      ge=1, le=168),
               db: Session = Depends(get_db),
               _user: User = Depends(current_user)):
    """Drop live rows older than the TTL. Observations stay in the archive."""
    removed = worker_service.prune_live_state(db, ttl_hours)
    return {"pruned": removed, "ttl_hours": ttl_hours,
            "note": "live rows only; archived observations are untouched"}


@router.get("/ais/zones/summary")
def zone_traffic(max_age_minutes: int = Query(60, ge=1, le=1440),
                 db: Session = Depends(get_db),
                 _user: User = Depends(current_user)):
    """Live vessel counts per operational zone.

    Feeds the officer dashboard's "current vessel activity". Zones with no
    traffic are included with a zero -- omitting them would make an empty zone
    indistinguishable from a zone that does not exist.
    """
    cutoff = worker_service._naive(
        utcnow() - timedelta(minutes=max_age_minutes))
    counts = dict(
        db.query(AisLiveState.zone_id, func.count(AisLiveState.mmsi))
        .filter(AisLiveState.report_utc >= cutoff)
        .group_by(AisLiveState.zone_id).all())

    zones = (db.query(Zone).filter(Zone.status == "active")
             .filter(Zone.kind == "operational").all())
    out = [{"zone_id": z.id, "name": z.name,
            "vessels": int(counts.get(z.id, 0))} for z in zones]
    return {
        "zones": sorted(out, key=lambda r: -r["vessels"]),
        # Vessels inside no declared zone. A real number, not a rounding
        # error: the theatre is an offshore boundary and traffic outside it is
        # still traffic.
        "outside_all_zones": int(counts.get(None, 0)),
        "max_age_minutes": max_age_minutes,
        "as_of_utc": utcnow(),
    }
