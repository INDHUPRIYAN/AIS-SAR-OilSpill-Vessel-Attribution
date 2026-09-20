/* Who is signed in, for the whole app.
 *
 * The session itself is an HttpOnly cookie the browser holds and this code
 * cannot read. So "am I signed in" is answered the only honest way available:
 * by asking the server (`GET /api/auth/me`). There is no client-side copy of
 * the token to go stale, and no localStorage entry for a scripting bug to
 * lift -- which is what replaced the shared admin token (audit AD-06).
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api, Unauthenticated } from "./api";

const SessionContext = createContext(null);

export function SessionProvider({ children }) {
  const [user, setUser] = useState(null);
  // Distinct from "no user": until the first /me resolves we do not know, and
  // rendering a sign-in form during that window makes an authenticated reload
  // flash the login page.
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState(null);
  // The evaluator view has a Login button: it opens the sign-in screen over a
  // session that already works, so production RBAC can be demonstrated.
  const [loginOpen, setLoginOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setUser(await api.me());
      setError(null);
    } catch (e) {
      if (e instanceof Unauthenticated) setUser(null);
      else setError(e);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const signIn = useCallback(async (email, password) => {
    const { user: signedIn } = await api.login(email, password);
    setUser(signedIn);
    setLoginOpen(false);
    return signedIn;
  }, []);

  /* Sign out, then ask the server who we are now: in the public evaluator
   * view that is the evaluator again, otherwise nobody (the sign-in form). */
  const signOut = useCallback(async () => {
    try { await api.logout(); } catch { /* the cookie is cleared either way */ }
    setUser(null);
    await refresh();
  }, [refresh]);

  const openLogin = useCallback(() => setLoginOpen(true), []);
  const closeLogin = useCallback(() => setLoginOpen(false), []);

  return (
    <SessionContext.Provider value={{
      user, checking, error, signIn, signOut, refresh,
      loginOpen, openLogin, closeLogin,
      isEvaluator: Boolean(user?.evaluator),
    }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside a SessionProvider");
  return ctx;
}

/** True when the signed-in user holds any of `roles`. Admin holds everything,
 *  mirroring `require_role` on the server so the UI and the API agree about
 *  what is permitted. This only hides controls -- the server is what enforces.
 */
export function useHasRole(...roles) {
  const { user } = useSession();
  return hasRole(user, ...roles);
}

/* Mirrors `IMPLICIT_ROLES` on the server: both administrator roles pass every
 * `require_role` check. The previous version knew only about `admin`, which
 * locked a super_admin out of the credential page the server would have let
 * them into. */
export const IMPLICIT_ROLES = new Set(["admin", "super_admin"]);

/** Pure form of useHasRole, for places that already hold the user. */
export function hasRole(user, ...roles) {
  if (!user) return false;
  if (IMPLICIT_ROLES.has(user.role)) return true;
  return roles.includes(user.role);
}

/** True only for the platform-authority role. `require_super_admin` on the
 *  server admits nobody else -- not even admin. */
export function isSuperAdmin(user) {
  return user?.role === "super_admin";
}
