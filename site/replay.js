/* Incident replay, built into the project page.
 *
 * Plays the sealed flagship run from demo/flagship.json (written by
 * scripts/site/export_demo.py from the run's own artefacts) so the case can be
 * shown when the live system -- the team's machine behind a Tailscale Funnel --
 * is switched off. It is a recording of the pipeline's output, not a
 * computation: every number on screen is copied from the run, and the map is
 * an inline SVG over a Natural Earth coastline, so the page needs no tile
 * server and no login.
 *
 * Plain script, no framework: the page must keep working on a projector with
 * a poor connection, and a 470 KB JSON (95 KB over the wire) is the only fetch.
 */
(function () {
  "use strict";

  var DATA_URL = "demo/flagship.json";
  var W = 960, H = 600;                 // SVG viewBox; the CSS scales it
  var HOUR = 3600e3;

  var $ = function (id) { return document.getElementById(id); };
  var root = $("rp");
  if (!root) return;

  // ---- SVG helpers ----------------------------------------------------------
  var NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function ease(p) { p = clamp(p, 0, 1); return p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; }
  function lerp(a, b, p) { return a + (b - a) * p; }
  var pad2 = function (n) { return (n < 10 ? "0" : "") + n; };
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmtUTC(ms) {
    var d = new Date(ms);
    return pad2(d.getUTCDate()) + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear() +
      " · " + pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes()) + " UTC";
  }
  function fmtClock(ms) {
    var d = new Date(ms);
    return pad2(d.getUTCDate()) + " " + MONTHS[d.getUTCMonth()] + " " + pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes()) + "Z";
  }
  var nf = function (n, dp) { return Number(n).toLocaleString("en-GB", { maximumFractionDigits: dp == null ? 0 : dp, minimumFractionDigits: dp == null ? 0 : dp }); };
  function latlon(p) {
    return Math.abs(p[1]).toFixed(3) + "° " + (p[1] >= 0 ? "N" : "S") + ", " +
      Math.abs(p[0]).toFixed(3) + "° " + (p[0] >= 0 ? "E" : "W");
  }

  // ---- load -----------------------------------------------------------------
  var loading = $("rp-loading");
  fetch(DATA_URL, { cache: "force-cache" })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(start)
    .catch(function (e) {
      loading.textContent = "The replay data could not be loaded (" + e.message + "). The screens below show the same case.";
    });

  function start(D) {
    loading.hidden = true;

    // ---- projection: equirectangular, 1 unit = 0.01°, x scaled by cos(lat) --
    var bb = D.run.bbox;
    var lat0 = (bb[1] + bb[3]) / 2;
    var KX = Math.cos(lat0 * Math.PI / 180) * 100, KY = 100;
    function wx(lon) { return lon * KX; }
    function wy(lat) { return -lat * KY; }
    function pathOf(points, close) {
      var s = "";
      for (var i = 0; i < points.length; i++) {
        s += (i ? "L" : "M") + wx(points[i][0]).toFixed(2) + " " + wy(points[i][1]).toFixed(2);
      }
      return s + (close ? "Z" : "");
    }
    function bboxOf(rings) {
      var w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
      rings.forEach(function (r) { r.forEach(function (p) {
        if (p[0] < w) w = p[0]; if (p[0] > e) e = p[0]; if (p[1] < s) s = p[1]; if (p[1] > n) n = p[1];
      }); });
      return [w, s, e, n];
    }
    function padBox(b, f) {
      var cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2, hw = (b[2] - b[0]) / 2 * f, hh = (b[3] - b[1]) / 2 * f;
      return [cx - hw, cy - hh, cx + hw, cy + hh];
    }
    /* A camera is {x, y, k}: world point (x, y) at the viewBox centre, k px per unit. */
    function fit(b, padPx) {
      var ww = (wx(b[2]) - wx(b[0])), hh = (wy(b[1]) - wy(b[3]));
      var k = Math.min((W - 2 * padPx) / ww, (H - 2 * padPx) / hh);
      return { x: (wx(b[0]) + wx(b[2])) / 2, y: (wy(b[1]) + wy(b[3])) / 2, k: k };
    }
    function toScreen(cam, lon, lat) {
      return [(wx(lon) - cam.x) * cam.k + W / 2, (wy(lat) - cam.y) * cam.k + H / 2];
    }

    // ---- data views ----------------------------------------------------------
    var acquired = Date.parse(D.run.acquired_utc);
    var aisStart = Date.parse(D.ais.window_utc[0]), aisEnd = Date.parse(D.ais.window_utc[1]);
    var aisMinute0 = Math.floor(aisStart / 60e3) * 60e3;        // tracks count minutes from here
    var steps = D.hindcast.steps;
    var stepMs = (D.hindcast.timestep_minutes || 60) * 60e3;
    /* The probable origin is the cloud at the middle of the engine's origin
     * window, as the live system's replay defines it (lib/replay.js). */
    var winStart = Date.parse(D.hindcast.window_start_utc), winEnd = Date.parse(D.hindcast.window_end_utc);
    var originStep = clamp(Math.round((acquired - (winStart + winEnd) / 2) / stepMs - 1e-9), 0, steps.length - 1);
    var origin = steps[originStep];
    var ranked = D.attribution.ranked;
    var suspectIds = {};
    ranked.forEach(function (s) { suspectIds[s.mmsi] = s; });
    var tracks = D.ais.tracks;
    var suspectTracks = tracks.filter(function (t) { return t.role === "suspect"; });
    var stage = {};
    D.stages.forEach(function (s) { stage[s.stage] = s; });

    var sceneBox = bb;
    var slickBox = bboxOf(D.slick.pieces.map(function (p) { return p.ring; }));
    var hindBox = bboxOf(steps.map(function (s) { return s.ring; }).concat([slickBox2ring(slickBox)]));
    var fcBox = bboxOf(D.forecast.envelopes.map(function (e) { return e.ring; }).concat([slickBox2ring(slickBox)]));
    function slickBox2ring(b) { return [[b[0], b[1]], [b[2], b[3]]]; }

    // ---- beats ----------------------------------------------------------------
    var top = ranked[0];
    var det = stage.detect || {}, cha = stage.characterise || {}, hin = stage.drift_hindcast || {},
        fc = stage.drift_forecast || {}, att = stage.attribution || {};
    var screenWarn = (det.warnings || []).filter(function (w) { return /screen rejected/.test(w); })[0] || "";
    var infWarn = (det.warnings || []).filter(function (w) { return /inference/.test(w); })[0] || "";
    var shortScene = D.run.scene_id.replace(/_COG$/, "").split("_").slice(0, 5).join("_") + "…";

    var BEATS = [
      { id: "acquire", label: "Acquire", dur: 7, cam: fit(sceneBox, 60), camFrom: fit(D.map.context_bbox, 20), camEnd: 0.8,
        time: function () { return acquired; },
        kicker: "Stage 1 · Scene",
        head: "Sentinel-1A radar scene acquired",
        body: shortScene + " · IW GRD, " + D.run.pixel_spacing_m + " m, " + D.run.polarisation + " · " + fmtUTC(acquired) +
          " · served from the local Copernicus (CDSE) cache.",
        meta: "cached scene" },
      { id: "detect", label: "Detect", dur: 9, cam: fit(sceneBox, 60),
        time: function () { return acquired; },
        kicker: "Stage 2 · Two-stage AI detection",
        head: nf(D.detect.classes.oil) + " oil candidates among " + nf(D.detect.classes.oil + D.detect.classes.lookalike) + " dark patches",
        body: "YOLO screen then U-Net segmentation, both ONNX on CPU. " + (screenWarn ? screenWarn.replace("screen rejected", "The screen rejected") + ". " : "") +
          "Confidence " + D.detect.confidence.toFixed(2) + (infWarn ? " · " + infWarn : "") + ".",
        meta: det.seconds ? nf(det.seconds) + " s" : "" },
      { id: "characterise", label: "Characterise", dur: 6, cam: fit(padBox(slickBox, 1.7), 50),
        time: function () { return acquired; },
        kicker: "Stage 3 · Slick characterisation",
        head: nf(D.slick.count) + " slick pieces · " + nf(D.slick.area_km2, 1) + " km²",
        body: "Largest piece " + nf(D.slick.pieces[0].area_km2, 1) + " km², major axis " + nf(D.slick.major_axis_m / 1000, 1) +
          " km at " + nf(D.slick.orientation_deg, 0) + "°. Weathering puts its age near " + nf(D.slick.age_hours, 0) +
          " h (" + D.slick.age_confidence + " confidence). Engine: " + D.slick.engine + ".",
        meta: cha.seconds ? nf(cha.seconds) + " s" : "" },
      { id: "hindcast", label: "Hindcast", dur: 12, cam: fit(padBox(hindBox, 1.25), 50),
        time: function (u) { return acquired - D.hindcast.backtrack_hours * HOUR * clamp((u - 0.05) / 0.8, 0, 1); },
        kicker: "Stage 4 · Drift hindcast and origin",
        head: "Drifting the slick back " + nf(D.hindcast.backtrack_hours) + " h to where it started",
        body: nf(D.hindcast.n_particles) + " particles, " + D.hindcast.engine + " stepping, currents from " + D.hindcast.currents +
          " and wind from " + D.hindcast.wind + ". Release window " + fmtClock(winStart) + " → " + fmtClock(winEnd) +
          "; probable origin " + latlon(origin.center) + " (± " + nf(D.hindcast.uncertainty_km, 2) + " km).",
        meta: hin.seconds ? nf(hin.seconds, 1) + " s" : "" },
      { id: "ais", label: "AIS traffic", dur: 10, cam: fit(sceneBox, 60),
        time: function (u) { return aisStart + (aisEnd - aisStart) * clamp((u - 0.05) / 0.9, 0, 1); },
        kicker: "Stage 5 · Real AIS traffic",
        head: nf(D.ais.rows) + " real AIS positions · " + nf(D.ais.unique_mmsi) + " vessels",
        body: D.ais.provider + ", " + fmtClock(aisStart) + " → " + fmtClock(aisEnd) + ". " + nf(D.ais.measured_rows) +
          " measured, " + nf(D.ais.interpolated_rows) + " interpolated to a 5-minute cadence. No synthetic fleet: the archive covers the origin window.",
        meta: D.ais.wall_seconds ? nf(D.ais.wall_seconds) + " s" : "" },
      { id: "attribute", label: "Attribute", dur: 12, cam: fit(padBox(hindBox, 1.6), 50),
        time: function () { return Date.parse(topPassage()); },
        kicker: "Stage 6 · Vessel attribution",
        head: nf(ranked.length) + " vessels pass the gates · " + nf(D.attribution.filtered_out) + " filtered out",
        body: nf(D.attribution.considered) + " vessels came near the origin region in time and space. Gates on distance, timing and heading against the slick axis leave " +
          nf(ranked.length) + "; each is scored on weighted evidence, not probability. #1 " + top.mmsi + ": " + top.reason,
        meta: att.seconds ? nf(att.seconds, 1) + " s" : "" },
      { id: "forecast", label: "Forecast", dur: 8, cam: fit(padBox(fcBox, 1.4), 50),
        time: function (u) { return acquired + 12 * HOUR * clamp((u - 0.05) / 0.75, 0, 1); },
        kicker: "Stage 7 · Forward drift",
        head: "Where the oil goes next: " + D.forecast.horizons_h.map(function (h) { return "+" + h + " h"; }).join(" and "),
        body: D.forecast.envelopes.filter(function (e) { return e.confidence_level === 0.9; }).map(function (e) {
            return "+" + e.horizon_h + " h: " + nf(e.area_km2, 1) + " km² at 90 %"; }).join(" · ") +
          ". " + (fc.detail || "") + ". Weathering (" + D.forecast.weathering.model + ", " + D.forecast.weathering.oil_type_assumed.replace("_", " ") +
          " assumed, " + D.forecast.weathering.confidence + " confidence).",
        meta: fc.seconds ? nf(fc.seconds, 1) + " s" : "" },
      { id: "report", label: "Report", dur: 8, cam: fit(padBox(fcBox, 1.4), 50),
        time: function () { return acquired; },
        kicker: "Sealed run · ⌗ " + D.run.digest.slice(0, 8),
        head: "Five stages on real inputs, " + nf(D.run.total_seconds) + " s end to end",
        body: "Recorded " + fmtUTC(Date.parse(D.run.generated_utc)) + ". Artefacts are content-hashed; the live system verifies this digest before it shows the case. Models: " +
          D.run.models.map(function (m) { return m.name; }).join(" + ") + ".",
        meta: "sealed" },
    ];
    var TOTAL = 0;
    BEATS.forEach(function (b) { b.start = TOTAL; TOTAL += b.dur; });
    function topPassage() {
      var m = /at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC/.exec(top.reason || "");
      return m ? m[1].replace(" ", "T") + ":00Z" : D.hindcast.window_end_utc;
    }

    // ---- build the SVG scene ------------------------------------------------
    var svg = $("rp-svg");
    var gWorld = el("g", { id: "rp-world" }, svg);
    var gScreen = el("g", { id: "rp-screen" }, svg);
    var NSS = "non-scaling-stroke";

    var gLand = el("g", { "class": "rp-land" }, gWorld);
    D.map.land.forEach(function (r) { el("path", { d: pathOf(r, true), "vector-effect": NSS }, gLand); });
    var gCoast = el("g", { "class": "rp-coast" }, gWorld);
    D.map.coast.forEach(function (l) { el("path", { d: pathOf(l), "vector-effect": NSS }, gCoast); });
    var foot = el("path", { "class": "rp-foot", d: pathOf([[bb[0], bb[1]], [bb[2], bb[1]], [bb[2], bb[3]], [bb[0], bb[3]]], true), "vector-effect": NSS }, gWorld);

    // AIS: one path per vessel, drawn faint; suspects get their own bright paths
    // that grow with the clock. Positions at the current time are one path of
    // dots (a zero-length segment with a round cap draws as a dot at any zoom).
    var gTracks = el("g", { "class": "rp-tracks" }, gWorld);
    var trackEls = tracks.map(function (t) {
      return el("path", { d: pathOf(t.pts), "vector-effect": NSS, "class": "rp-track rp-track-" + t.role }, gTracks);
    });
    var gBoxes = el("g", { "class": "rp-boxes" }, gWorld);
    var boxes = D.detect.candidate_boxes.slice().sort(function (a, b) { return a[0] - b[0]; });
    var boxEls = boxes.map(function (b) {
      return el("path", { d: pathOf([[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]], true), "vector-effect": NSS,
        "class": "rp-box rp-box-" + b[4] }, gBoxes);
    });
    var gSlick = el("g", { "class": "rp-slick" }, gWorld);
    D.slick.pieces.forEach(function (p) { el("path", { d: pathOf(p.ring, true), "vector-effect": NSS }, gSlick); });
    var gForecast = el("g", { "class": "rp-forecast" }, gWorld);
    var fcEls = D.forecast.envelopes.map(function (e) {
      return el("path", { d: pathOf(e.ring, true), "vector-effect": NSS, "class": "rp-fc rp-fc-" + (e.confidence_level === 0.9 ? "outer" : "inner") }, gForecast);
    });
    var gHind = el("g", { "class": "rp-hind" }, gWorld);
    var trail = el("path", { "class": "rp-trail", "vector-effect": NSS }, gHind);
    var ellipseEls = steps.map(function (s) { return el("path", { d: pathOf(s.ring, true), "vector-effect": NSS, "class": "rp-ellipse", style: "display:none" }, gHind); });
    var particles = el("path", { "class": "rp-particles", "vector-effect": NSS, d: dots(D.hindcast.particles) }, gHind);
    var axis = el("path", { "class": "rp-axis", "vector-effect": NSS, d: axisPath() }, gHind);
    var suspTrailEls = suspectTracks.map(function () { return el("path", { "class": "rp-susp-trail", "vector-effect": NSS }, gWorld); });
    var ships = el("path", { "class": "rp-ships", "vector-effect": NSS }, gWorld);

    // screen layer: things whose size must not change with zoom
    var scan = el("line", { "class": "rp-scan", y1: 0, y2: H }, gScreen);
    var gSusp = el("g", { "class": "rp-susp" }, gScreen);
    var suspMarks = suspectTracks.map(function (t) {
      var g = el("g", { "class": "rp-susp-mark" }, gSusp);
      el("circle", { r: 5 }, g);
      var rank = suspectIds[t.mmsi].rank;
      // The ranked vessels all crossed the same small region, so their labels
      // alternate sides and rows rather than stacking on one point.
      var left = rank % 2 === 0, row = rank > 2 ? 16 : -6;
      el("text", { x: left ? -9 : 9, y: row + 10, "text-anchor": left ? "end" : "start" }, g).textContent = "#" + rank + " · " + t.mmsi;
      return { g: g, t: t, rank: rank };
    });
    var originG = el("g", { "class": "rp-origin" }, gScreen);
    el("circle", { "class": "rp-origin-ping", r: 8 }, originG);
    el("circle", { "class": "rp-origin-dot", r: 3.5 }, originG);
    var originLabel = el("text", { x: 12, y: -8 }, originG);
    originLabel.textContent = "probable origin · " + fmtClock(Date.parse(origin.t)) + " · ± " + nf(D.hindcast.uncertainty_km, 2) + " km";

    function dots(pts) {
      var s = "";
      for (var i = 0; i < pts.length; i++) s += "M" + wx(pts[i][0]).toFixed(2) + " " + wy(pts[i][1]).toFixed(2) + "h0.01";
      return s;
    }
    function axisPath() {
      var c = D.slick.centroid, a = (90 - D.slick.orientation_deg) * Math.PI / 180;
      var dl = D.slick.major_axis_m / 2 / 111320;                  // degrees of latitude
      var dx = Math.cos(a) * dl / Math.cos(c[1] * Math.PI / 180), dy = Math.sin(a) * dl;
      return pathOf([[c[0] - dx, c[1] - dy], [c[0] + dx, c[1] + dy]]);
    }

    // ---- side panel -----------------------------------------------------------
    var rail = $("rp-rail");
    var railEls = BEATS.map(function (b, i) {
      var li = document.createElement("li");
      li.innerHTML = '<span class="rp-rail-n">' + pad2(i + 1) + '</span><span class="rp-rail-l">' + b.label +
        '</span><span class="rp-rail-m mono">' + (b.meta || "") + "</span>";
      li.addEventListener("click", function () { seek(b.start); play(); });
      rail.appendChild(li);
      return li;
    });
    var rankList = $("rp-rank");
    var rankEls = ranked.map(function (s) {
      var li = document.createElement("li");
      li.innerHTML = '<div class="rp-rank-row"><span class="rp-rank-n">#' + s.rank + '</span><span class="mono">' + s.mmsi +
        '</span><span class="rp-rank-score mono">' + s.total_score.toFixed(2) + '</span></div>' +
        '<div class="rp-rank-bar"><i style="width:0%"></i></div>' +
        '<div class="rp-rank-sub">' + Object.keys(s.sub_scores).map(function (k) {
          return '<span title="' + k + '">' + k.replace("_", " ") + " " + s.sub_scores[k].toFixed(2) + "</span>"; }).join("") + "</div>";
      rankList.appendChild(li);
      return { li: li, bar: li.querySelector(".rp-rank-bar i"), s: s };
    });
    var filteredNote = document.createElement("li");
    filteredNote.className = "rp-rank-filtered";
    filteredNote.textContent = nf(D.attribution.filtered_out) + " more vessels were filtered out: " +
      (D.attribution.filtered[0] ? D.attribution.filtered[0].reason.replace("Filtered out: ", "e.g. ") : "");
    rankList.appendChild(filteredNote);

    // report card (static markup in index.html): fill the ranked list
    var reportList = $("rp-report-list");
    if (reportList) {
      ranked.forEach(function (s) {
        var li = document.createElement("li");
        li.innerHTML = '<span class="rp-rank-n">#' + s.rank + '</span><span class="mono">MMSI ' + s.mmsi + '</span><span class="mono rp-rank-score">' + s.total_score.toFixed(2) + "</span>";
        reportList.appendChild(li);
      });
    }
    var digestEls = document.querySelectorAll("[data-rp-digest]");
    for (var i = 0; i < digestEls.length; i++) digestEls[i].textContent = D.run.digest.slice(0, 8);

    // ---- render ------------------------------------------------------------------
    var kickerEl = $("rp-kicker"), headEl = $("rp-head"), bodyEl = $("rp-body"), clockEl = $("rp-hud-clock"),
        stageEl = $("rp-hud-stage"), timeEl = $("rp-time"), seekEl = $("rp-seek"), report = $("rp-report");
    var lastBeat = -1;

    function beatAt(t) {
      for (var i = BEATS.length - 1; i >= 0; i--) if (t >= BEATS[i].start) return i;
      return 0;
    }
    function cameraAt(i, u) {
      var b = BEATS[i], from = i ? BEATS[i - 1].cam : b.camFrom || b.cam, to = b.cam;
      var p = ease(u / (b.camEnd || 0.4));
      return { x: lerp(from.x, to.x, p), y: lerp(from.y, to.y, p), k: Math.exp(lerp(Math.log(from.k), Math.log(to.k), p)) };
    }
    function posAt(t, minute) {
      var p = t.pts;
      if (minute < p[0][2] || minute > p[p.length - 1][2]) return null;
      var lo = 0, hi = p.length - 1;
      while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (p[mid][2] <= minute) lo = mid; else hi = mid; }
      var a = p[lo], b2 = p[hi], f = b2[2] === a[2] ? 0 : (minute - a[2]) / (b2[2] - a[2]);
      return [lerp(a[0], b2[0], f), lerp(a[1], b2[1], f)];
    }
    function setOpacity(node, v) { node.style.opacity = v; }
    function show(node, on) { node.style.display = on ? "" : "none"; }

    function render(t) {
      var i = beatAt(t), b = BEATS[i], u = clamp((t - b.start) / b.dur, 0, 1), id = b.id;
      var cam = cameraAt(i, u);
      gWorld.setAttribute("transform", "translate(" + (W / 2 - cam.x * cam.k) + " " + (H / 2 - cam.y * cam.k) + ") scale(" + cam.k + ")");
      var T = b.time(u);

      // -- side panel and HUD (only when the beat changes) --
      if (i !== lastBeat) {
        lastBeat = i;
        kickerEl.textContent = b.kicker; headEl.textContent = b.head; bodyEl.textContent = b.body;
        stageEl.textContent = pad2(i + 1) + " · " + b.label.toUpperCase();
        railEls.forEach(function (li, j) { li.className = j < i ? "done" : j === i ? "now" : ""; });
        rankList.hidden = !(id === "attribute");
        report.hidden = id !== "report";
        root.setAttribute("data-beat", id);
      }
      clockEl.textContent = fmtClock(T);
      timeEl.textContent = fmtClock(T);
      seekEl.value = Math.round(t / TOTAL * 1000);

      // -- footprint draws itself in the first beat --
      if (id === "acquire") {
        var len = foot.getTotalLength ? foot.getTotalLength() : 0;
        foot.style.strokeDasharray = len ? len + "" : "";
        foot.style.strokeDashoffset = len ? len * (1 - ease(clamp((u - 0.3) / 0.6, 0, 1))) : 0;
      } else { foot.style.strokeDasharray = ""; foot.style.strokeDashoffset = 0; }

      // -- detection: a scan sweeps the footprint; boxes appear behind it --
      var scanning = id === "detect" && u < 0.8;
      show(scan, scanning);
      var west = toScreen(cam, bb[0], bb[1])[0], east = toScreen(cam, bb[2], bb[1])[0];
      var scanX = lerp(west, east, ease(u / 0.8));
      if (scanning) { scan.setAttribute("x1", scanX); scan.setAttribute("x2", scanX); }
      var boxesOn = id === "detect";
      show(gBoxes, boxesOn);
      if (boxesOn) {
        var fadeLook = clamp((u - 0.8) / 0.2, 0, 1);              // look-alikes rejected
        for (var k = 0; k < boxes.length; k++) {
          var bx = toScreen(cam, boxes[k][0], boxes[k][1])[0];
          var on = u >= 0.8 || bx <= scanX;
          boxEls[k].style.opacity = on ? (boxes[k][4] === "lookalike" ? 1 - fadeLook : 1) : 0;
        }
      }
      // slick: appears late in detect, stays; dim during hindcast and AIS
      var slickOp = id === "acquire" ? 0 : id === "detect" ? clamp((u - 0.55) / 0.3, 0, 1) :
        (id === "hindcast" || id === "ais" || id === "attribute") ? 0.55 : 1;
      setOpacity(gSlick, slickOp);
      show(axis, id === "characterise");
      setOpacity(axis, clamp((u - 0.4) / 0.3, 0, 1));

      // -- hindcast: hourly ellipse, trail of centres, then the particle cloud --
      var hindOn = id === "hindcast" || id === "ais" || id === "attribute" || id === "characterise";
      show(gHind, hindOn);
      var stepF = 0;
      if (id === "hindcast") stepF = (acquired - T) / stepMs;
      else if (id === "characterise") stepF = 0;
      else stepF = originStep;
      var stepI = clamp(Math.round(stepF), 0, steps.length - 1);
      ellipseEls.forEach(function (e, j) { show(e, j === stepI); });
      var trailPts = [];
      for (var j = 0; j <= Math.floor(stepF) && j < steps.length; j++) trailPts.push(steps[j].center);
      if (stepF < steps.length - 1 && Math.floor(stepF) < steps.length - 1) {
        var a = steps[Math.floor(stepF)].center, c2 = steps[Math.floor(stepF) + 1].center, f = stepF - Math.floor(stepF);
        trailPts.push([lerp(a[0], c2[0], f), lerp(a[1], c2[1], f)]);
      }
      trail.setAttribute("d", trailPts.length > 1 ? pathOf(trailPts) : "");
      show(particles, id === "hindcast" && u > 0.85);
      setOpacity(particles, clamp((u - 0.85) / 0.1, 0, 1));
      var originOn = (id === "hindcast" && u > 0.88) || id === "ais" || id === "attribute" || id === "forecast" || id === "report";
      show(originG, originOn);
      if (originOn) {
        var op = toScreen(cam, origin.center[0], origin.center[1]);
        originG.setAttribute("transform", "translate(" + op[0].toFixed(1) + " " + op[1].toFixed(1) + ")");
        show(originLabel, id === "hindcast");
      }

      // -- AIS: traffic and moving ships --
      var aisOn = id === "ais" || id === "attribute";
      show(gTracks, aisOn); show(ships, aisOn); show(gSusp, aisOn);
      suspTrailEls.forEach(function (e) { show(e, aisOn); });
      if (aisOn) {
        var fadeIn = id === "ais" ? clamp(u / 0.15, 0, 1) : 1;
        var otherOp = id === "ais" ? 0.45 * fadeIn : lerp(0.45, 0.06, ease(u / 0.3));
        var filtOp = id === "ais" ? 0.55 * fadeIn : lerp(0.55, 0.22, ease(u / 0.3));
        for (var n = 0; n < tracks.length; n++) {
          trackEls[n].style.opacity = tracks[n].role === "suspect" ? 0.35 : tracks[n].role === "filtered" ? filtOp : otherOp;
        }
        var minute = (T - aisMinute0) / 60e3;
        var d = "";
        for (n = 0; n < tracks.length; n++) {
          if (tracks[n].role === "suspect") continue;
          var p = posAt(tracks[n], minute);
          if (p) d += "M" + wx(p[0]).toFixed(2) + " " + wy(p[1]).toFixed(2) + "h0.01";
        }
        ships.setAttribute("d", d);
        ships.style.opacity = id === "ais" ? fadeIn : lerp(1, 0.25, ease(u / 0.3));
        suspectTracks.forEach(function (st, m) {
          var pts = st.pts.filter(function (q) { return q[2] <= minute; });
          var cur = posAt(st, minute);
          if (cur) pts = pts.concat([[cur[0], cur[1], minute]]);
          suspTrailEls[m].setAttribute("d", pts.length > 1 ? pathOf(pts) : "");
          var mark = suspMarks[m];
          var at = cur || (minute > st.pts[st.pts.length - 1][2] ? st.pts[st.pts.length - 1] : null);
          show(mark.g, !!at);
          if (at) {
            var sp = toScreen(cam, at[0], at[1]);
            mark.g.setAttribute("transform", "translate(" + sp[0].toFixed(1) + " " + sp[1].toFixed(1) + ")");
            mark.g.setAttribute("class", "rp-susp-mark" + (id === "attribute" && mark.rank === 1 ? " top" : ""));
          }
        });
      }
      // ranking bars fill one after another
      if (id === "attribute") {
        rankEls.forEach(function (r, m) {
          var p = clamp((u - (0.3 + m * 0.14)) / 0.12, 0, 1);
          r.li.style.opacity = p; r.bar.style.width = (r.s.total_score * 100 * p).toFixed(0) + "%";
        });
        filteredNote.style.opacity = clamp((u - 0.86) / 0.1, 0, 1);
      }

      // -- forecast envelopes: each appears as the clock reaches its horizon --
      var fcOn = id === "forecast" || id === "report";
      show(gForecast, fcOn);
      if (fcOn) {
        D.forecast.envelopes.forEach(function (e, m) {
          var valid = Date.parse(e.valid_utc);
          fcEls[m].style.opacity = id === "report" ? 1 : clamp((T - (valid - HOUR)) / HOUR, 0, 1);
        });
      }
    }

    // ---- transport ---------------------------------------------------------------
    var playing = false, wantPlaying = false, t = 0, last = 0, raf = 0;
    var playBtn = $("rp-play");
    function frame(now) {
      if (!playing) return;
      var dt = Math.min(0.1, (now - last) / 1000); last = now;
      t += dt;
      if (t >= TOTAL) { t = TOTAL - 0.001; render(t); pause(); playBtn.textContent = "Replay"; return; }
      render(t);
      raf = requestAnimationFrame(frame);
    }
    function play() {
      if (t >= TOTAL - 0.01) t = 0;
      wantPlaying = true;
      if (playing) return;
      playing = true; last = performance.now();
      playBtn.textContent = "Pause"; playBtn.setAttribute("aria-pressed", "true");
      raf = requestAnimationFrame(frame);
    }
    function pause(keepIntent) {
      if (!keepIntent) wantPlaying = false;
      playing = false; cancelAnimationFrame(raf);
      playBtn.textContent = t >= TOTAL - 0.01 ? "Replay" : "Play"; playBtn.setAttribute("aria-pressed", "false");
    }
    function seek(to) { t = clamp(to, 0, TOTAL - 0.001); lastBeat = -1; render(t); }
    playBtn.addEventListener("click", function () { playing ? pause() : play(); });
    $("rp-restart").addEventListener("click", function () { seek(0); play(); });
    seekEl.addEventListener("input", function () { pause(true); seek(seekEl.value / 1000 * TOTAL); });
    seekEl.addEventListener("change", function () { if (wantPlaying) play(); });
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    seek(0);
    // Start when the section scrolls into view; stop while it is off screen.
    var started = false;
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            if (!started && !reduced) { started = true; play(); }
            else if (wantPlaying && !playing) play();
          } else if (playing) { pause(true); }
        });
      }, { threshold: 0.35 }).observe(root);
    }
    // A hash link (#replay) means the visitor asked for it: start at once.
    if (location.hash === "#replay" && !started) { started = true; play(); }
    window.addEventListener("hashchange", function () { if (location.hash === "#replay") { seek(0); play(); } });
  }
})();
