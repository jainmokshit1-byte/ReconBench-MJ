import { ArrowRight, LockKeyhole } from "lucide-react";
import { Link } from "react-router";

export function LoginPage() {
  return (
    <main className="login-page">
      <section className="login-card">
        <div className="brand-mark login-mark" aria-hidden="true">R</div>
        <div className="login-eyebrow">RECONBENCH</div>
        <h1>Sign in to your workspace</h1>
        <p>Authentication is introduced in Phase 3. This page reserves its route and visual treatment.</p>
        <label className="login-field">
          Email address
          <input type="email" placeholder="name@company.com" disabled />
        </label>
        <label className="login-field">
          Password
          <input type="password" placeholder="Enter your password" disabled />
        </label>
        <button className="button button-primary login-submit" type="button" disabled>
          Continue <ArrowRight size={16} aria-hidden="true" />
        </button>
        <div className="login-secure"><LockKeyhole size={14} /> Protected workspace access</div>
        <Link className="login-back-link" to="/home">Back to the workspace shell</Link>
      </section>
    </main>
  );
}
