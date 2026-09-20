/* An address the app does not answer to. It says which address, and leaves
 * it in the URL bar: silently landing on the dashboard made a mistyped deep
 * link look like a working page and erased the evidence. */

import { Link, useLocation } from "react-router-dom";

import { useShell } from "../lib/shell";

export default function NotFound() {
  const location = useLocation();
  const { openPalette } = useShell();
  return (
    <div className="nf" data-testid="not-found">
      <div className="nf-box">
        <div className="label">Page not found</div>
        <h1 style={{ margin: 0, fontSize: "var(--fs-2xl)" }}>Nothing lives at this address</h1>
        <div className="nf-path">{location.pathname}{location.search}</div>
        <div className="muted" style={{ lineHeight: 1.6 }}>
          The link may be mistyped, or the record it pointed to may have a different
          identifier. Search finds investigations, vessels, detections and scenes by id.
        </div>
        <div className="row" style={{ gap: 8 }}>
          <Link className="btn btn-primary" to="/">Dashboard</Link>
          <Link className="btn" to="/investigations">Investigations</Link>
          <button className="btn" onClick={openPalette}>Search</button>
        </div>
      </div>
    </div>
  );
}
