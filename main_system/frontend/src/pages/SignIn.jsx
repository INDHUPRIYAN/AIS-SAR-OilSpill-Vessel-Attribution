/* Sign in.
 *
 * The server answers "invalid email or password" for both an unknown account
 * and a wrong password, and this form shows that message verbatim rather than
 * trying to be more helpful. Distinguishing the two would turn the form into
 * an account-enumeration oracle, and these accounts belong to named
 * investigators. */

import { useState } from "react";
import { motion } from "framer-motion";
import { LogIn, Moon, ShieldCheck, Sun, Waves } from "lucide-react";

import { Spinner } from "../components/ui";
import { useSession } from "../lib/session";
import { useTheme } from "../lib/theme";

const VERBS = ["Track", "Detect", "Trace", "Investigate", "Attribute", "Protect"];

export default function SignIn() {
  const { signIn } = useSession();
  const { theme, toggle } = useTheme();
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
    <div className="signin" data-testid="sign-in">
      <div className="signin-hero">
        <div className="signin-hero-inner">
          <div className="brand" style={{ padding: 0 }}>
            <div className="brand-mark"><Waves size={16} /></div>
            <div>
              <div className="brand-name">OCEAN<b>TRACE</b></div>
              <div className="brand-sub">Maritime intelligence platform</div>
            </div>
          </div>
          <div className="signin-verbs mono">
            {VERBS.map((v, i) => (
              <span key={v}>
                {i > 0 && <i>·</i>}{v.toUpperCase()}
              </span>
            ))}
          </div>
          <p className="signin-lede">
            Sentinel-1 SAR detection, drift hindcast and forecast, AIS
            reconstruction and explainable vessel attribution — every layer
            labelled with where it came from.
          </p>
          <div className="signin-facts mono tiny">
            <span>SAR · YOLO screen · U-Net segmentation</span>
            <span>CMEMS currents · ERA5 wind · Lagrangian drift</span>
            <span>AIS gating · weighted scoring · sealed provenance</span>
          </div>
        </div>
        <div className="signin-globe" aria-hidden="true" />
      </div>

      <div className="signin-form-col">
        <button className="hdr-btn signin-theme" onClick={toggle}
          title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>
        <motion.form initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
          onSubmit={submit} className="panel signin-form" data-testid="sign-in-form">
          <header className="panel-head">
            <span className="panel-title"><ShieldCheck size={13} /> Sign in</span>
            <span className="panel-tools tiny mono muted">restricted</span>
          </header>
          <div className="panel-body stack" style={{ gap: 12 }}>
            <div className="tiny muted" style={{ lineHeight: 1.6 }}>
              Authorised use only. Actions are recorded against your account in
              a hash-chained audit trail.
            </div>

            <label className="field">
              <span>Email</span>
              <input id="email" type="email" autoComplete="username" value={email}
                onChange={(e) => setEmail(e.target.value)} required autoFocus />
            </label>

            <label className="field">
              <span>Password</span>
              <input id="password" type="password" autoComplete="current-password"
                value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>

            {error && (
              <div role="alert" className="notice notice-danger">{error}</div>
            )}

            <button className="btn btn-primary btn-block" type="submit"
              disabled={busy || !email || !password} data-testid="sign-in-submit">
              {busy ? <Spinner /> : <LogIn size={13} />} Sign in
            </button>
          </div>
        </motion.form>
      </div>

      <style>{`
        .signin { position: relative; display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(360px, 0.9fr); height: 100vh; background: var(--bg-0); z-index: 1; }
        .signin-hero { position: relative; overflow: hidden; border-right: 1px solid var(--line); background: radial-gradient(ellipse at 30% 20%, rgba(34,195,238,0.08), transparent 55%), var(--bg-1); }
        .signin-hero-inner { position: relative; z-index: 1; padding: 48px; max-width: 560px; display: flex; flex-direction: column; gap: 22px; }
        .signin-verbs { display: flex; flex-wrap: wrap; gap: 8px; font-size: 11px; letter-spacing: 0.22em; color: var(--accent); }
        .signin-verbs i { color: var(--ink-3); font-style: normal; margin-right: 8px; }
        .signin-lede { margin: 0; font-size: 14px; line-height: 1.65; color: var(--ink-1); max-width: 460px; }
        .signin-facts { display: flex; flex-direction: column; gap: 4px; color: var(--ink-3); letter-spacing: 0.04em; }
        .signin-globe { position: absolute; right: -22%; bottom: -30%; width: 78%; aspect-ratio: 1; border-radius: 50%;
          background: radial-gradient(circle at 35% 30%, rgba(34,195,238,0.16), rgba(14,22,33,0.9) 62%, transparent 70%);
          box-shadow: inset 0 0 80px rgba(34,195,238,0.12), 0 0 120px rgba(34,195,238,0.08); }
        .signin-globe::after { content: ""; position: absolute; inset: 0; border-radius: 50%;
          background: repeating-linear-gradient(0deg, transparent 0 28px, rgba(34,195,238,0.08) 28px 29px), repeating-linear-gradient(90deg, transparent 0 28px, rgba(34,195,238,0.08) 28px 29px);
          -webkit-mask: radial-gradient(circle, #000 60%, transparent 70%); mask: radial-gradient(circle, #000 60%, transparent 70%); }
        :root[data-theme="light"] .signin-globe { background: radial-gradient(circle at 35% 30%, rgba(3,105,161,0.16), rgba(228,234,241,0.95) 62%, transparent 70%); box-shadow: inset 0 0 80px rgba(3,105,161,0.1); }
        .signin-form-col { position: relative; display: grid; place-items: center; padding: 32px; }
        .signin-theme { position: absolute; top: 14px; right: 14px; }
        .signin-form { width: min(400px, 100%); }
        @media (max-width: 900px) { .signin { grid-template-columns: 1fr; } .signin-hero { display: none; } }
      `}</style>
    </div>
  );
}
