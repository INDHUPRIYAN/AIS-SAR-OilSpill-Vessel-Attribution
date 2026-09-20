/* Where you are, as links back up. Derived from the URL alone (crumbsFor), so
 * a deep link, a refresh and a back-button press all name the same place. */

import { Link, useLocation } from "react-router-dom";
import { ChevronRight } from "lucide-react";

import { crumbsFor } from "../../lib/shell";
import { STAGES, STAGE_INDEX } from "../../lib/stages";

function stageLabelOf(pathname) {
  const last = pathname.replace(/\/+$/, "").split("/").pop();
  return STAGE_INDEX[last] != null ? STAGES[STAGE_INDEX[last]].label : undefined;
}

export default function Breadcrumbs() {
  const { pathname } = useLocation();
  const crumbs = crumbsFor(pathname, stageLabelOf(pathname));
  return (
    <nav className="crumbs" aria-label="Breadcrumb" data-testid="breadcrumbs">
      <ol>
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          const cls = `crumb ${c.mono ? "mono" : ""} ${last ? "crumb-here" : ""}`;
          return (
            <li key={`${i}-${c.label}`}>
              {i > 0 && <ChevronRight size={12} className="crumb-sep" aria-hidden="true" />}
              {c.to && !last
                ? <Link className={cls} to={c.to}>{c.label}</Link>
                : <span className={cls} aria-current={last ? "page" : undefined}>{c.label}</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
