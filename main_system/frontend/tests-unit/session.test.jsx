/* The session layer decides whether a user sees the app or the sign-in form,
 * so these tests pin the two ways that goes wrong invisibly:
 *
 *  - a token cached anywhere JavaScript can read it (the localStorage admin
 *    token this replaced), and
 *  - a signed-in reload flashing the login form because "not checked yet" was
 *    treated as "not signed in".
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionProvider, useSession } from "../src/lib/session";
import { Unauthenticated } from "../src/lib/api";

const ADMIN = { id: 1, email: "ops@example.invalid", role: "admin", active: true };

function Probe() {
  const { user, checking } = useSession();
  if (checking) return <div>checking</div>;
  return <div>{user ? `signed in: ${user.role}` : "signed out"}</div>;
}

function mockFetch(handler) {
  global.fetch = vi.fn(handler);
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("session", () => {
  it("asks the server who is signed in, rather than trusting local state", async () => {
    const calls = [];
    mockFetch(async (path, opts) => {
      calls.push([path, opts]);
      return { ok: true, status: 200, json: async () => ADMIN };
    });

    render(<SessionProvider><Probe /></SessionProvider>);
    await waitFor(() => expect(screen.getByText("signed in: admin")).toBeInTheDocument());

    expect(calls[0][0]).toBe("/api/auth/me");
  });

  it("sends credentials so the HttpOnly cookie actually rides along", async () => {
    let opts;
    mockFetch(async (_p, o) => {
      opts = o;
      return { ok: true, status: 200, json: async () => ADMIN };
    });

    render(<SessionProvider><Probe /></SessionProvider>);
    await waitFor(() => expect(screen.getByText("signed in: admin")).toBeInTheDocument());

    // Without this the cookie is never sent and every request looks anonymous.
    expect(opts.credentials).toBe("include");
  });

  it("treats a 401 as signed out, not as an error page", async () => {
    mockFetch(async () => ({
      ok: false, status: 401, json: async () => ({ detail: "authentication required" }),
    }));

    render(<SessionProvider><Probe /></SessionProvider>);
    await waitFor(() => expect(screen.getByText("signed out")).toBeInTheDocument());
  });

  it("shows 'checking' before the first answer, so a reload does not flash the form", async () => {
    let release;
    mockFetch(() => new Promise((resolve) => {
      release = () => resolve({ ok: true, status: 200, json: async () => ADMIN });
    }));

    render(<SessionProvider><Probe /></SessionProvider>);
    expect(screen.getByText("checking")).toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.getByText("signed in: admin")).toBeInTheDocument());
  });

  it("keeps no token anywhere JavaScript can read", async () => {
    mockFetch(async () => ({ ok: true, status: 200, json: async () => ADMIN }));

    render(<SessionProvider><Probe /></SessionProvider>);
    await waitFor(() => expect(screen.getByText("signed in: admin")).toBeInTheDocument());

    // The whole point of moving off the shared admin token (audit AD-06):
    // there is nothing here for a scripting bug to lift.
    expect(localStorage.length).toBe(0);
    expect(JSON.stringify(localStorage)).not.toMatch(/token/i);
  });
});

describe("Unauthenticated", () => {
  it("is distinguishable from an ordinary failure", () => {
    const e = new Unauthenticated();
    expect(e).toBeInstanceOf(Error);
    expect(e.status).toBe(401);
    expect(e.name).toBe("Unauthenticated");
  });
});
