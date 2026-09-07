/* Sign in.
 *
 * The server answers "invalid email or password" for both an unknown account
 * and a wrong password, and this form shows that message verbatim rather than
 * trying to be more helpful. Distinguishing the two would turn the form into
 * an account-enumeration oracle, and these accounts belong to named
 * investigators.
 */

import { useState } from "react";
import { motion } from "framer-motion";
import { LogIn, ShieldCheck } from "lucide-react";

import { Spinner } from "../components/ui";
import { useSession } from "../lib/session";

export default function SignIn() {
  const { signIn } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
    } catch (err) {
      setError(err.message || "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page" style={{ display: "grid", placeItems: "center", minHeight: "70vh" }}>
      <motion.form
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        onSubmit={submit}
        className="card"
        style={{ width: 400 }}
      >
        <div className="card-head">
          <ShieldCheck size={14} color="var(--accent)" />
          <span className="card-title">Sign in to OceanTrace</span>
        </div>
        <div className="card-body">
          <div className="tiny muted" style={{ marginBottom: 12, lineHeight: 1.6 }}>
            Authorised use only. Actions are recorded against your account.
          </div>

          <label className="stat-label" htmlFor="email">Email</label>
          <input
            id="email" type="email" autoComplete="username" value={email}
            onChange={(e) => setEmail(e.target.value)} required
            style={{ width: "100%", marginBottom: 10 }}
          />

          <label className="stat-label" htmlFor="password">Password</label>
          <input
            id="password" type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} required
            style={{ width: "100%" }}
          />

          {error && (
            <div role="alert" className="tiny" style={{ color: "var(--danger)", marginTop: 10 }}>
              {error}
            </div>
          )}

          <button
            className="btn btn-primary"
            type="submit"
            disabled={busy || !email || !password}
            style={{ width: "100%", marginTop: 14 }}
          >
            {busy ? <Spinner /> : <LogIn size={13} />} Sign in
          </button>
        </div>
      </motion.form>
    </div>
  );
}
