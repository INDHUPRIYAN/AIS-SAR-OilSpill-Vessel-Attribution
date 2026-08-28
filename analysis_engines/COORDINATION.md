# Messages to send

The handbook names exactly two coordination points (§10.2, Part F) plus whatever I find
that belongs to someone else. Each block below is written to be pasted straight into
chat.

---

## → Keerthana (NetCDF variable names — the one written coordination point)

> Hi Keerthana — confirming the NetCDF variable names once, in writing, as the handbook
> asks. My drift engine reads:
>
> - `currents.nc` → `u` (eastward, m/s), `v` (northward, m/s)
> - `wind.nc` → `u10`, `v10` (10 m wind, m/s)
> - coordinates → `time`, `lat`, `lon`; `time` CF-encoded or datetime64, `lat`/`lon` 1-D
>   and regular
>
> **You don't have to match these exactly.** The reader also accepts the usual aliases —
> `uo`/`vo`, `water_u`/`water_v`, `eastward_sea_water_velocity`, `10u`/`10v`,
> `latitude`/`longitude`, `valid_time` — and just logs a warning saying which it matched.
> Descending latitude and reversed time axes are handled too.
>
> Two things that *would* break me, so worth knowing:
>
> 1. **Curvilinear grids** (2-D lat/lon arrays) → hard `BAD_GRID`. GLORYS and HYCOM
>    regional subsets are regular, so this should not come up, but a raw native-grid
>    product would.
> 2. **Coverage.** I need the grid to cover the slick bbox at minimum — that is fatal.
>    Covering the whole 24 h drift reach is preferable but only a warning; beyond the edge
>    I hold the boundary velocity. For the demo scene the particles travel ~16 km, so a
>    margin of ~0.25° around the scene bbox would remove the warning entirely.
>
> Units are assumed m/s. If ERA5 or CMEMS hands you something else, say so and I'll add
> the conversion rather than silently scaling.
>
> Your tiny hand-built NetCDF is also my mock — mine currently lives at
> `analysis_engines/samples/inputs/{currents,wind}.nc` if you want to diff against it.

---

## → Krishnan (parquet columns — confirmation, plus one gotcha)

> Hi Krishnan — the parquet columns work as specified, no changes needed. Confirming what
> I read:
>
> - **Required** (hard `MISSING_INPUT` if absent): `mmsi`, `timestamp`, `lat`, `lon`
> - **Used when present**, degrades with a warning otherwise: `sog_kn`, `cog_deg`,
>   `heading_deg`, `vessel_name`, `imo`, `vessel_type`, `length_m`, `width_m`, `draft_m`,
>   `status`, `source`, `culprit`
>
> `vessel_type` drives the prior, so the strings matter a little: I match
> case-insensitively on `tanker`, `bulk carrier`, `cargo`, `tug`, `fishing`, `passenger`,
> `ferry`, and anything unrecognised falls to a neutral 0.5.
>
> **One gotcha that cost me an hour, so you don't hit it too:** pandas 2.x preserves the
> source time unit, so a parquet written from microsecond timestamps comes back as
> `datetime64[us]`, not `[ns]`. Taking the int64 view and dividing by 1e9 then puts every
> fix in **1970**, and everything silently fails a time-window check for reasons that have
> nothing to do with the data. My loader now converts explicitly. Worth checking anywhere
> you do epoch arithmetic in the generator or the interpolator.
>
> For the 50-scenario benchmark: I built an equivalent seeded set to unblock myself
> (`analysis_engines/benchmark/`), and it reports 86% top-1 / 100% top-3. When yours is
> ready it drops straight in — my harness only needs a parquet path and the culprit MMSI
> per scenario. Happy to swap to yours as the headline number.
>
> One request if it's cheap: a spread of difficulty. If every culprit slows down *and*
> goes dark, the hit rate comes out near 100% and means nothing. Mine deliberately
> includes scenarios where the culprit does neither and innocent tankers run the same
> course through the same region.

---

## → Indhu (integration, plus two repo items that are yours)

> Hi Indhu — engines A, B and C are done and integration-ready. One command runs all
> three: `cd analysis_engines && python scripts/run_all.py` (~3.5 s, no network, no GPU).
> `INTEGRATION.md` has the calling convention, every error class, and what I suggest the
> UI does with each.
>
> **Two deliberate deviations you should know about before wiring the UI:**
>
> 1. `suspects.json` carries an extra `origin_window` block (`start_utc`, `end_utc`,
>    `peak_utc`, `engine_used`) so you can caption the suspect list without re-opening
>    `origin_cloud.geojson`. Purely additive — every §4.4 field is unchanged.
> 2. My temporal gate means "in the origin region **during** the window", not "has any fix
>    during the window". Taken literally the handbook wording filters almost nothing,
>    because AIS tracks are continuous and nearly every vessel is transmitting during the
>    window — just somewhere else. So "filtered out: outside time window" now means *the
>    vessel was there, but at the wrong time*, which is a more useful thing for the UI to
>    say.
>
> **Three things worth designing around:**
>
> - `origin_cloud.geojson` is **2.4 MB** (300 particles × 25 hourly timesteps). Fine for
>   one investigation, maybe not for several open at once — `output_every_h` in
>   `config/drift.yaml` is the dial. Tell me what the UI can take and I'll set it.
> - Drift currently reports `engine_used: "fallback"` on every run, because OpenDrift is
>   not installed (no conda on my machine). The Euler integrator is complete and validated
>   against a hand-computed analytic case, so the demo path works — but the architecture
>   slide's "OpenDrift OpenOil" is aspirational until that install happens. Handbook §5.2's
>   escalation rule anticipated this.
> - `age_hours_est` will visibly disagree with the hindcast window (~30 h vs ~12 h on the
>   demo scene). Both are correct: the age proxy is dominated by an assumed slick
>   thickness, which is why its confidence is hard-wired to `"low"`. Please lead with the
>   hindcast window in the UI.
>
> **Two repo items that are yours to fix:**
>
> 1. The root `.gitignore` has a bare `docs/` rule, which matches at any depth — so all
>    four handbooks under `analysis_engines/docs/` are **untracked**. Same for anyone
>    else's `docs/`. It needs to be `/docs/` to scope it to the repo root. Right now those
>    documents exist only on our laptops.
> 2. `contracts/schemas/` holds only `sar_detection.json` and `vessel_attribution.json`,
>    whose field names don't match the handbook's `slick.geojson` / `suspects.json`. I've
>    been coding to handbook §4.2–4.4. My Pydantic models in `engines/schemas/` are the
>    working definition of all four contracts and can be lifted into `contracts/` if you
>    want a single source of truth.
>
> Ready for the 30-minute walkthrough whenever suits — `HANDOVER.md` has the agenda.
