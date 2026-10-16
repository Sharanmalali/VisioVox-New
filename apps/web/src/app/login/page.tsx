'use client';

/**
 * Sign in / create account.
 *
 * Firebase owns the credential (ADR-0016); this screen only drives it. Google
 * comes first because that is how most people get in; email sits below rather
 * than behind a toggle, so nobody has to hunt for the field. On large screens
 * the left half says what the product is, so a first-time visitor who arrived
 * here directly is not signing up for something unexplained.
 */

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { IconArrowLeft, IconCheck, Logo, LogoMark } from '@/components/icons';
import { ThemeToggle } from '@/components/shell/ThemeToggle';
import { Alert, Field, Key } from '@/components/ui';
import {
  explain,
  firebaseConfigured,
  registerWithEmail,
  sendReset,
  signInWithEmail,
  signInWithGoogle,
} from '@/lib/firebase';
import { useSession } from '@/lib/store';

type Mode = 'signin' | 'register';

/** Google's G, inline: a remote asset here would block the control most people use. */
function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" className="shrink-0">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.34A9 9 0 0 0 9 18Z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.94H.96a9 9 0 0 0 0 8.12l3.01-2.34Z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.9 11.43 0 9 0A9 9 0 0 0 .96 4.94l3.01 2.34C4.68 5.16 6.66 3.58 9 3.58Z"
      />
    </svg>
  );
}

function BrandPanel() {
  const points = [
    'One clean audio track for every person on screen',
    'Word-timed captions in 100 languages',
    'Face crops and voiceprints deleted after each job',
  ];
  return (
    <aside className="relative hidden overflow-hidden bg-[#0b0d16] text-white lg:flex lg:flex-col">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(50% 45% at 20% 15%, rgba(99,102,241,.55), transparent 70%), radial-gradient(45% 40% at 85% 85%, rgba(14,165,233,.35), transparent 70%)',
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          backgroundImage:
            'linear-gradient(rgba(255,255,255,.06) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.06) 1px, transparent 1px)',
          backgroundSize: '48px 48px',
          maskImage: 'radial-gradient(ellipse 70% 60% at 40% 40%, #000, transparent)',
        }}
      />
      <div className="relative flex flex-1 flex-col justify-between p-12 xl:p-16">
        <Link
          href="/"
          className="inline-flex items-center gap-2.5 self-start rounded-[var(--radius-sm)]"
        >
          <LogoMark size={30} />
          <span className="text-lg font-semibold tracking-[-0.02em]">VisioVox</span>
        </Link>

        <div className="flex max-w-md flex-col gap-8">
          <h2 className="display text-[44px] leading-[1.05]">
            Every voice,
            <br />
            on its own track.
          </h2>

          {/* Four speakers, one selected: the product in a glance. */}
          <div
            className="flex flex-col gap-2 rounded-[16px] border border-white/10 bg-white/[0.05] p-3 backdrop-blur"
            aria-hidden="true"
          >
            {['Ananya', 'Marcus', 'Priya', 'Daniel'].map((name, i) => (
              <div
                key={name}
                className={
                  'flex items-center gap-3 rounded-[10px] px-3 py-2 ' +
                  (i === 1 ? 'bg-white/[0.1] ring-1 ring-white/20' : '')
                }
              >
                <span
                  className="grid h-7 w-7 place-items-center rounded-full text-[11px] font-semibold"
                  style={{ background: `var(--spk-${String(i + 1)})` }}
                >
                  {i + 1}
                </span>
                <span className="flex-1 text-sm text-white/90">{name}</span>
                <span className="flex h-4 items-end gap-[2px]">
                  {[0.5, 0.9, 0.6, 1, 0.7].map((h, j) => (
                    <span
                      key={j}
                      className={
                        i === 1
                          ? 'eq-bar w-[3px] rounded-full bg-white'
                          : 'w-[3px] rounded-full bg-white/30'
                      }
                      style={{
                        height: `${String(h * 100)}%`,
                        animationDelay: `${String(j * 120)}ms`,
                      }}
                    />
                  ))}
                </span>
              </div>
            ))}
          </div>

          <ul className="flex flex-col gap-3">
            {points.map((p) => (
              <li key={p} className="flex items-start gap-3 text-[15px] text-white/80">
                <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white/15">
                  <IconCheck size={12} />
                </span>
                {p}
              </li>
            ))}
          </ul>
        </div>

        <p className="text-[13px] text-white/50">
          Processing runs on our own hardware. Nothing is sent to third-party AI services.
        </p>
      </div>
    </aside>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const signedIn = useSession((s) => s.signedIn);
  const ready = useSession((s) => s.ready);
  const hydrate = useSession((s) => s.hydrate);

  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [key, setKey] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; key?: string }>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  // Only once Firebase has reported: before that, "signed out" and "not yet
  // known" are the same value, and redirecting on it bounces someone who is
  // already signed in back to this page on every refresh.
  useEffect(() => {
    if (ready && signedIn) {
      router.replace('/projects');
    }
  }, [ready, signedIn, router]);

  /** Caught here so an obvious mistake costs no round trip; Firebase remains
   *  the authority and its refusal is what the user finally sees. */
  function validate(): { email?: string; key?: string } {
    const found: { email?: string; key?: string } = {};
    if (!email.trim()) found.email = 'Enter your email address.';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
      found.email = 'That email address is not valid.';
    if (!key) found.key = 'Enter your password.';
    else if (mode === 'register' && key.length < 12)
      found.key = `Use at least 12 characters (you have ${String(key.length)}).`;
    return found;
  }

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
      router.push('/projects');
    } catch (err) {
      // A closed popup is a decision, not a failure, so `explain` returns an
      // empty string for it and nothing is shown.
      const message = explain(err);
      if (message) setError(message);
    } finally {
      setBusy(null);
    }
  }

  function submit(e: React.SyntheticEvent) {
    e.preventDefault();
    const found = validate();
    setFieldErrors(found);
    if (Object.keys(found).length > 0) return;
    void run('email', () =>
      mode === 'signin' ? signInWithEmail(email, key) : registerWithEmail(email, key),
    );
  }

  async function reset() {
    if (!email.trim()) {
      setFieldErrors({ email: 'Enter your email address first, then choose “Forgot password?”.' });
      return;
    }
    setBusy('reset');
    setError(null);
    try {
      await sendReset(email);
      // Deliberately the same message whether or not the address exists —
      // otherwise this reports which addresses have accounts.
      setNotice(`If an account exists for ${email}, a reset link is on its way.`);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(null);
    }
  }

  function switchMode() {
    setMode(mode === 'signin' ? 'register' : 'signin');
    setFieldErrors({});
    setError(null);
    setNotice(null);
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <BrandPanel />

      <main id="content" className="relative flex flex-col bg-bg-base">
        <div className="flex items-center justify-between px-6 py-5 sm:px-10">
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] text-sm font-medium text-fg-muted transition-colors hover:text-fg-primary"
          >
            <IconArrowLeft size={16} />
            <span className="lg:hidden">
              <Logo size={22} />
            </span>
            <span className="hidden lg:inline">Back to home</span>
          </Link>
          <ThemeToggle />
        </div>

        <div className="flex flex-1 items-center justify-center px-6 pb-16 sm:px-10">
          <div className="w-full max-w-[400px]">
            {!firebaseConfigured() ? (
              <Alert tone="danger" title="Sign-in is not configured">
                The Firebase keys are missing from this build. Set the{' '}
                <code className="font-mono text-[13px] text-fg-primary">
                  NEXT_PUBLIC_FIREBASE_*
                </code>{' '}
                variables and rebuild — they are inlined at build time.
              </Alert>
            ) : (
              <form onSubmit={submit} className="flex flex-col gap-6" noValidate>
                <div className="flex flex-col gap-2">
                  <h1 className="text-[28px] font-semibold tracking-[-0.025em]">
                    {mode === 'signin' ? 'Welcome back' : 'Create your account'}
                  </h1>
                  <p className="text-[15px] text-fg-secondary">
                    {mode === 'signin'
                      ? 'Sign in to separate, caption and share your recordings.'
                      : 'Sign up with Google or an email address.'}
                  </p>
                </div>

                <Key
                  tone="secondary"
                  size="lg"
                  full
                  type="button"
                  loading={busy === 'google'}
                  disabled={busy !== null}
                  busyLabel="Opening Google…"
                  onClick={() => void run('google', signInWithGoogle)}
                >
                  <GoogleMark />
                  {mode === 'signin' ? 'Continue with Google' : 'Sign up with Google'}
                </Key>

                <div className="flex items-center gap-3" aria-hidden="true">
                  <span className="h-px flex-1 bg-border-subtle" />
                  <span className="text-[12px] font-medium text-fg-muted">or with email</span>
                  <span className="h-px flex-1 bg-border-subtle" />
                </div>

                <div className="flex flex-col gap-4">
                  <Field
                    label="Email"
                    id="auth-email"
                    type="email"
                    autoComplete="email"
                    placeholder="you@company.com"
                    required
                    value={email}
                    error={fieldErrors.email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                    }}
                  />
                  <Field
                    label="Password"
                    id="auth-key"
                    type="password"
                    autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                    placeholder={mode === 'signin' ? 'Your password' : 'At least 12 characters'}
                    required
                    value={key}
                    error={fieldErrors.key}
                    hint={mode === 'register' ? 'Use 12 or more characters.' : undefined}
                    aside={
                      mode === 'signin' ? (
                        <button
                          type="button"
                          onClick={() => void reset()}
                          disabled={busy !== null}
                          className="text-[13px] font-medium text-accent hover:underline disabled:opacity-50"
                        >
                          {busy === 'reset' ? 'Sending…' : 'Forgot password?'}
                        </button>
                      ) : undefined
                    }
                    onChange={(e) => {
                      setKey(e.target.value);
                    }}
                  />
                </div>

                {error !== null && <Alert tone="danger">{error}</Alert>}
                {notice !== null && (
                  <Alert tone="success" title="Check your inbox">
                    {notice}
                  </Alert>
                )}

                <Key
                  tone="primary"
                  size="lg"
                  full
                  type="submit"
                  loading={busy === 'email'}
                  disabled={busy !== null}
                  busyLabel={mode === 'signin' ? 'Signing in…' : 'Creating account…'}
                >
                  {mode === 'signin' ? 'Sign in' : 'Create account'}
                </Key>

                <p className="text-center text-sm text-fg-muted">
                  {mode === 'signin' ? 'New to VisioVox?' : 'Already have an account?'}{' '}
                  <button
                    type="button"
                    onClick={switchMode}
                    disabled={busy !== null}
                    className="font-medium text-accent hover:underline disabled:opacity-50"
                  >
                    {mode === 'signin' ? 'Create an account' : 'Sign in instead'}
                  </button>
                </p>
              </form>
            )}

            <p className="mt-10 text-center text-[12px] leading-relaxed text-fg-muted">
              By continuing you confirm you have the right to upload the recordings you process.
              Face crops and voiceprints are deleted when each job finishes.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
