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
            Continue with Google
          </Button>
        </div>

        <div className="flex items-center gap-3">
          <span className="h-px flex-1 bg-border-subtle" />
          <span className="text-xs text-fg-muted">or use an email address</span>
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
