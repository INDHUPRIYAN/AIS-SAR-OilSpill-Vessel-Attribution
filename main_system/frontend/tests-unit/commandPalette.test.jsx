/* ⌘K.
 *
 * A command palette is the easiest surface in the product to make dishonest,
 * because it answers instantly and an answer always looks like an answer.
 * These pin the four ways it would lie without anyone noticing:
 *
 *  - offering a near-miss on an identifier as though it were a match;
 *  - rendering a failed request as "no results";
 *  - reaching only the screens somebody remembered to list;
 *  - describing shortcuts the shell no longer has.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CommandPalette, { ShortcutOverlay } from "../src/components/CommandPalette";
import {
  KEYMAP, ROUTES, ShellProvider, useRegisterCommands, useShell,
} from "../src/lib/shell";

/* A stand-in for the routed app: shows which route is current, so "the
 * palette navigated" is observable rather than assumed. */
function Where() {
  return (
    <Routes>
      {ROUTES.map((r) => (
        <Route key={r.to} path={r.to} element={<div>at {r.to}</div>} />
      ))}
      <Route path="*" element={<div>at /</div>} />
    </Routes>
  );
}

function Harness({ contribute = null }) {
  return (
    <MemoryRouter initialEntries={["/"]}>
      <ShellProvider>
        <Where />
        {contribute && <Contributor commands={contribute} />}
        <CommandPalette />
        <ShortcutOverlay />
      </ShellProvider>
    </MemoryRouter>
  );
}

function Contributor({ commands }) {
  useRegisterCommands(() => commands, [commands]);
  return null;
}

function mockSearch(handler) {
  global.fetch = vi.fn(async (path) => {
    if (String(path).startsWith("/api/search")) return handler(String(path));
    return { ok: true, status: 200, json: async () => ({}) };
  });
}

const ok = (body) => ({ ok: true, status: 200, json: async () => body });

const openPalette = () =>
  fireEvent.keyDown(window, { key: "k", metaKey: true });

const type = (value) =>
  fireEvent.change(screen.getByTestId("palette-input"), { target: { value } });

beforeEach(() => {
  mockSearch(async () => ok({ query: "", count: 0, results: [], matching: "" }));
});

afterEach(() => { vi.restoreAllMocks(); });

// --------------------------------------------------------------- opening --

describe("opening and closing", () => {
  it("opens on ⌘K and on Ctrl+K", async () => {
    render(<Harness />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
  });

  it("closes on Escape from inside its own input", () => {
    // Escape has to escape the field it is typed into, or the palette is a
    // trap for anyone who opened it by accident.
    render(<Harness />);
    openPalette();
    fireEvent.keyDown(screen.getByTestId("palette-input"), { key: "Escape" });
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("forgets the previous query when reopened", () => {
    render(<Harness />);
    openPalette();
    type("baltic");
    fireEvent.keyDown(window, { key: "Escape" });
    openPalette();
    expect(screen.getByTestId("palette-input")).toHaveValue("");
  });
});

// ---------------------------------------------------------------- routes --

describe("it reaches every screen", () => {
  it("lists one command per declared route", () => {
    render(<Harness />);
    openPalette();
    for (const r of ROUTES) {
      expect(screen.getByTestId(`palette-command-route:${r.to}`)).toBeInTheDocument();
    }
  });

  it("navigates when a route command is chosen", async () => {
    render(<Harness />);
    openPalette();
    fireEvent.click(screen.getByTestId("palette-command-route:/vessels"));
    await waitFor(() => expect(screen.getByText("at /vessels")).toBeInTheDocument());
    // and it closes behind itself
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });

  it("filters commands by substring, not by fuzzy guessing", () => {
    render(<Harness />);
    openPalette();
    type("vessel");
    expect(screen.getByTestId("palette-command-route:/vessels")).toBeInTheDocument();

    type("vessle");            // one transposition
    expect(screen.queryByTestId("palette-command-route:/vessels")).not.toBeInTheDocument();
  });
});

// ------------------------------------------------------- search over API --

describe("entity search", () => {
  it("queries the endpoint and opens the row's own route", async () => {
    mockSearch(async () => ok({
      query: "367653160", count: 1, matching: "substring…",
      results: [{
        kind: "vessel", id: "367653160", label: "367653160",
        context: "MMSI 367653160 · no name in the source data",
        route: "/vessels?mmsi=367653160", tier: 0,
      }],
    }));
    render(<Harness />);
    openPalette();
    type("367653160");

    const row = await screen.findByTestId("palette-entity-vessel-367653160");
    // The row shows what the source actually held: the number, and the fact
    // that no name came with it.
    expect(within(row).getByText(/no name in the source data/)).toBeInTheDocument();

    fireEvent.click(row);
    await waitFor(() => expect(screen.getByText("at /vessels")).toBeInTheDocument());
  });

  it("says a search failed instead of showing an empty list", async () => {
    mockSearch(async () => ({
      ok: false, status: 500, json: async () => ({ detail: "database is locked" }),
    }));
    render(<Harness />);
    openPalette();
    type("gulf");

    const note = await screen.findByTestId("palette-error");
    expect(note).toHaveTextContent(/database is locked/);
    expect(note).toHaveTextContent(/not an empty result/);
    expect(screen.queryByTestId("palette-empty")).not.toBeInTheDocument();
  });

  it("repeats the server's matching rule when nothing matched", async () => {
    mockSearch(async () => ok({
      query: "zzzz", count: 0, results: [],
      matching: "substring, case-insensitive. No fuzzy matching: a near-miss "
                + "on a nine-digit MMSI is not a result.",
    }));
    render(<Harness />);
    openPalette();
    type("zzzz");

    const empty = await screen.findByTestId("palette-empty");
    expect(empty).toHaveTextContent(/No match for "zzzz"/);
    expect(empty).toHaveTextContent(/No fuzzy matching/);
  });

  it("does not search on a single character", async () => {
    const spy = vi.fn(async () => ok({ results: [], count: 0 }));
    mockSearch(spy);
    render(<Harness />);
    openPalette();
    type("3");
    await new Promise((r) => setTimeout(r, 250));
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByTestId("palette-empty"))
      .toHaveTextContent(/at least two characters/);
  });

  it("debounces: typing an MMSI is one request, not nine", async () => {
    const spy = vi.fn(async () => ok({ results: [], count: 0, matching: "" }));
    mockSearch(spy);
    render(<Harness />);
    openPalette();
    for (const v of ["36", "367", "3676", "36765", "367653", "3676531",
                     "36765316", "367653160"]) type(v);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 250));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toContain("q=367653160");
  });
});

// ------------------------------------------------------ view-contributed --

describe("commands contributed by the mounted view", () => {
  const layerToggle = { id: "layer:sar", group: "Layer", label: "Hide sar" };

  it("appear in the palette and run when chosen", () => {
    const run = vi.fn();
    render(<Harness contribute={[{ ...layerToggle, run }]} />);
    openPalette();
    fireEvent.click(screen.getByTestId("palette-command-layer:sar"));
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("bind their own key, and that key does not fire while typing", () => {
    const run = vi.fn();
    render(<Harness contribute={[{ id: "measure", keys: ["M"], label: "Measure", run }]} />);

    fireEvent.keyDown(window, { key: "m" });
    expect(run).toHaveBeenCalledTimes(1);

    openPalette();
    fireEvent.keyDown(screen.getByTestId("palette-input"), { key: "m" });
    expect(run).toHaveBeenCalledTimes(1);   // still one: typing is not a shortcut
  });
});

// -------------------------------------------------------------- keyboard --

describe("keyboard", () => {
  it("moves with the arrows and opens with Enter", async () => {
    render(<Harness />);
    openPalette();
    const input = screen.getByTestId("palette-input");
    const first = ROUTES[0].to;
    const second = ROUTES[1].to;

    expect(screen.getByTestId(`palette-command-route:${first}`))
      .toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(screen.getByTestId(`palette-command-route:${second}`))
      .toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(screen.getByTestId(`palette-command-route:${first}`))
      .toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByText(`at ${first}`)).toBeInTheDocument());
  });

  it("wraps at both ends rather than dead-ending", () => {
    render(<Harness />);
    openPalette();
    const input = screen.getByTestId("palette-input");
    fireEvent.keyDown(input, { key: "ArrowUp" });
    const last = ROUTES[ROUTES.length - 1].to;
    expect(screen.getByTestId(`palette-command-route:${last}`))
      .toHaveAttribute("aria-selected", "true");
  });
});

// ------------------------------------------------------ shortcut overlay --

describe("the ? overlay", () => {
  it("is generated from the live keymap", () => {
    render(<Harness contribute={[{ id: "measure", keys: ["M"], scope: "workspace",
                                   label: "Measure tool", run: () => {} }]} />);
    fireEvent.keyDown(window, { key: "?" });
    const overlay = screen.getByTestId("shortcut-overlay");

    // every base binding
    for (const k of KEYMAP) {
      expect(within(overlay).getByTestId(`shortcut-${k.id}`)).toHaveTextContent(k.label);
    }
    // and the one the mounted view contributed, which is the half that would
    // otherwise be documented by hand and go stale
    expect(within(overlay).getByTestId("shortcut-measure"))
      .toHaveTextContent("Measure tool");
  });

  it("lists nothing the shell cannot dispatch", () => {
    render(<Harness />);
    fireEvent.keyDown(window, { key: "?" });
    const rows = screen.getByTestId("shortcut-overlay").querySelectorAll(".cp-row");
    expect(rows).toHaveLength(KEYMAP.length);
  });

  it("gives way to the palette rather than stacking on top of it", () => {
    render(<Harness />);
    fireEvent.keyDown(window, { key: "?" });
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.queryByTestId("shortcut-overlay")).not.toBeInTheDocument();
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
  });
});

// ----------------------------------------------------------- run context --

describe("run in context", () => {
  function Probe() {
    const { runId, setRunId, clearRun } = useShell();
    return (
      <div>
        <span data-testid="ctx">{runId ?? "none"}</span>
        <button onClick={() => setRunId("inv-gulf-flagship-20230108-2day")}>set</button>
        <button onClick={clearRun}>clear</button>
      </div>
    );
  }

  it("survives navigation and is only dropped when closed explicitly", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <ShellProvider>
          <Probe />
          <Where />
          <CommandPalette />
        </ShellProvider>
      </MemoryRouter>);

    fireEvent.click(screen.getByText("set"));
    expect(screen.getByTestId("ctx")).toHaveTextContent("inv-gulf-flagship-20230108-2day");

    openPalette();
    fireEvent.click(screen.getByTestId("palette-command-route:/catalog"));
    await waitFor(() => expect(screen.getByText("at /catalog")).toBeInTheDocument());
    // still there: the honesty strip does not empty because you navigated
    expect(screen.getByTestId("ctx")).toHaveTextContent("inv-gulf-flagship-20230108-2day");

    fireEvent.click(screen.getByText("clear"));
    expect(screen.getByTestId("ctx")).toHaveTextContent("none");
  });
});
