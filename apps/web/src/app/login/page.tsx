'use client';

/**
 * Sign in (ADR-0016). Firebase owns the credential; this page only drives it.
 *
 * The provider buttons come first because that is how most people will get in,
 * and the email form is below rather than hidden behind a toggle — a password
 * user should not have to hunt for the field.
 */

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { Alert, Button, Card, Field } from '@/components/ui';
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

/** Google's G, inline. A remote asset here would block the one control most
 *  people use, and the CSP would have to allow another origin. */
function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
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

export default function LoginPage() {
  const router = useRouter();
  const signedIn = useSession((s) => s.signedIn);
  const ready = useSession((s) => s.ready);
  const hydrate = useSession((s) => s.hydrate);

  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
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
    if (ready && signedIn) router.replace('/projects');
  }, [ready, signedIn, router]);

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

  async function reset() {
    if (!email.trim()) {
      setError('Enter your email address first, then choose Reset password.');
      return;
    }
    setBusy('reset');
    setError(null);
    try {
      await sendReset(email);
      // Deliberately the same message whether or not the address exists —
      // otherwise this endpoint reports which addresses have accounts.
      setNotice(`If an account exists for ${email}, a reset link is on its way.`);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(null);
    }
  }

  if (!firebaseConfigured()) {
    return (
      <div className="mx-auto max-w-lg py-16">
        <Alert tone="danger" title="Sign-in is not configured">
          The Firebase keys are missing from this build. Set the{' '}
          <code className="font-mono text-xs">NEXT_PUBLIC_FIREBASE_*</code> variables and rebuild —
          they are inlined at build time, so restarting will not pick them up.
        </Alert>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-md flex-col gap-6 py-10">
      <header className="flex flex-col gap-2">
        <h1 className="font-display text-4xl leading-tight">
          {mode === 'signin' ? 'Sign in' : 'Create an account'}
        </h1>
        <p className="text-sm text-fg-secondary">
          {mode === 'signin'
            ? 'Separate overlapping speakers into their own tracks.'
            : 'Free while it is in development. No card, no trial.'}
        </p>
      </header>

      <Card className="flex flex-col gap-5">
        <div className="flex flex-col gap-2.5">
          <Button
            variant="secondary"
            full
            loading={busy === 'google'}
            disabled={busy !== null}
            onClick={() => void run('google', signInWithGoogle)}
          >
            {busy !== 'google' && <GoogleMark />}
            {mode === 'signin' ? 'Continue with Google' : 'Sign up with Google'}
          </Button>
        </div>

        <div className="flex items-center gap-3">
          <span className="h-px flex-1 bg-border-subtle" />
          <span className="text-xs text-fg-muted">
            {mode === 'signin' ? 'or use an email address' : 'or sign up with an email address'}
          </span>
          <span className="h-px flex-1 bg-border-subtle" />
        </div>

        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              'email',
              mode === 'signin'
                ? () => signInWithEmail(email, password)
                : () => registerWithEmail(email, password),
            );
          }}
        >
          <Field
            label="Email"
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
            }}
          />
          <Field
            label="Password"
            id="password"
            type="password"
            autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            required
            {...(mode === 'register' ? { minLength: 12, hint: 'At least 12 characters.' } : {})}
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
            }}
          />

          {error !== null && <Alert tone="danger">{error}</Alert>}
          {notice !== null && <Alert tone="accent">{notice}</Alert>}

          <Button type="submit" full loading={busy === 'email'} disabled={busy !== null}>
            {mode === 'signin' ? 'Sign in' : 'Create account'}
          </Button>
        </form>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border-subtle pt-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setMode(mode === 'signin' ? 'register' : 'signin');
              setError(null);
              setNotice(null);
            }}
          >
            {mode === 'signin' ? 'Create an account' : 'I already have an account'}
          </Button>
          {mode === 'signin' && (
            <Button
              variant="ghost"
              size="sm"
              loading={busy === 'reset'}
              onClick={() => void reset()}
            >
              Reset password
            </Button>
          )}
        </div>
      </Card>

      <p className="text-xs leading-relaxed text-fg-muted">
        Voiceprints and face crops are deleted when a job finishes. Deleting your account removes
        your recordings and your sign-in together.
      </p>
    </div>
  );
}
