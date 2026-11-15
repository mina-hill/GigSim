"use client";

import { BriefcaseBusiness } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { Dashboard } from "@/components/dashboard";
import { Notice } from "@/components/ui";
import { ApiError, api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

import styles from "./workspace-access.module.css";

type AuthSession = components["schemas"]["AuthSession"];
type LocalUser = components["schemas"]["LocalUser"];
type PersonaId = LocalUser["persona"];

function describe(error: unknown) {
  return error instanceof Error ? error.message : "The account request could not be completed.";
}

export function WorkspaceAccess() {
  const [session, setSession] = useState<AuthSession>();
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [signingOut, setSigningOut] = useState(false);
  const requestInFlight = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    void api.GET("/api/v1/auth/session", { signal: controller.signal }).then(
      (response) => {
        if (!controller.signal.aborted) {
          try {
            setSession(unwrap(response));
            setError("");
          } catch (failure) {
            setError(describe(failure));
          }
        }
      },
      (failure: unknown) => {
        if (!controller.signal.aborted) setError(describe(failure));
      },
    );
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    function reloadSession() {
      setSession(undefined);
      setRevision((number) => number + 1);
    }
    window.addEventListener("gigbid:session-expired", reloadSession);
    return () => window.removeEventListener("gigbid:session-expired", reloadSession);
  }, []);

  async function signOut() {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    setError("");
    setSigningOut(true);
    try {
      setSession(unwrap(await api.POST("/api/v1/auth/logout")));
    } catch (failure) {
      setError(describe(failure));
    } finally {
      requestInFlight.current = false;
      setSigningOut(false);
    }
  }

  if (!session) {
    if (error) {
      return (
        <main className={styles.screen}>
          <section className={`surface ${styles.card}`}>
            <Notice title="Could not open GigBid">{error}</Notice>
            <button className="button secondary" onClick={() => setRevision((number) => number + 1)}>Try again</button>
          </section>
        </main>
      );
    }
    return <main className="initial-loading" role="status">Loading GigBid…</main>;
  }

  if (session.mode === "demo") return <Dashboard />;
  if (session.user) {
    return (
      <Dashboard
        key={session.user.id}
        workspacePersona={session.user.persona}
        username={session.user.username}
        onSignOut={() => void signOut()}
        signingOut={signingOut}
        accountError={error}
      />
    );
  }

  return (
    <SignIn
      personas={session.available_personas ?? []}
      onAuthenticated={(nextSession) => {
        setError("");
        setSession(nextSession);
      }}
      onAvailabilityChange={() => setRevision((number) => number + 1)}
    />
  );
}

function SignIn({
  personas, onAuthenticated, onAvailabilityChange,
}: {
  personas: NonNullable<AuthSession["available_personas"]>;
  onAuthenticated: (session: AuthSession) => void;
  onAvailabilityChange: () => void;
}) {
  const [register, setRegister] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const requestInFlight = useRef(false);
  const canSubmit = !register || personas.length > 0;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit || requestInFlight.current) return;
    requestInFlight.current = true;
    setError("");
    setBusy(true);
    const fields = new FormData(event.currentTarget);
    const username = String(fields.get("username") ?? "");
    const password = String(fields.get("password") ?? "");
    try {
      const response = register
        ? await api.POST("/api/v1/auth/register", {
          body: { username, password, persona: fields.get("persona") as PersonaId },
        })
        : await api.POST("/api/v1/auth/login", { body: { username, password } });
      onAuthenticated(unwrap(response));
    } catch (failure) {
      setError(describe(failure));
      if (register && failure instanceof ApiError && failure.status === 409) {
        onAvailabilityChange();
      }
    } finally {
      requestInFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <main className={styles.screen}>
      <section className={`surface ${styles.card}`} aria-labelledby="workspace-access-title">
        <div className={styles.brand}><BriefcaseBusiness size={17} aria-hidden="true" />GigBid</div>
        <div>
          <h1 id="workspace-access-title">{register ? "Create workspace" : "Sign in to GigBid"}</h1>
          {register && <p>Choose an available GigSim persona for your local account.</p>}
        </div>
        {error && <Notice>{error}</Notice>}
        {register && !canSubmit && <Notice>All local GigSim personas have accounts. Sign in to continue.</Notice>}
        <form className={styles.form} onSubmit={(event) => void submit(event)} key={String(register)}>
          <label className={styles.field}>
            Username
            <input name="username" autoComplete="username" required minLength={3} maxLength={32} />
          </label>
          <label className={styles.field}>
            Password
            <input
              name="password" type="password" autoComplete={register ? "new-password" : "current-password"}
              required minLength={12} maxLength={256} aria-describedby={register ? "password-length" : undefined}
            />
          </label>
          {register && <p id="password-length">Use at least 12 characters.</p>}
          {register && canSubmit && (
            <label className={styles.field}>
              GigSim persona
              <select name="persona" required>
                {personas.map((persona) => <option key={persona.id} value={persona.id}>{persona.name}</option>)}
              </select>
            </label>
          )}
          <button className="button primary" disabled={busy || !canSubmit}>
            {busy ? "Please wait…" : register ? "Create account" : "Sign in"}
          </button>
        </form>
        <button
          className={`button ghost ${styles.toggle}`} disabled={busy}
          onClick={() => { setRegister(!register); setError(""); }}
        >
          {register ? "I already have an account" : "Create a local account"}
        </button>
      </section>
    </main>
  );
}
