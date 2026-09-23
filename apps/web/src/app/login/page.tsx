'use client';

/**
 * Access terminal.
 *
 * Firebase owns the credential (ADR-0016); this screen only drives it. The
 * provider key comes first because that is how most people will get in, and
 * the email form sits below rather than behind a toggle — someone with a
 * passphrase should not have to hunt for the field.
 *
 * The terminal is stacked on two offset slabs so it reads as a block with mass
 * rather than a card floating on a background.
 */

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { Alert, Field, Key, Slab, SlabHead } from '@/components/ui';
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
 *  people use, and the CSP would have to admit another origin. */
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
      router.replace('/studio');
    }
  }, [ready, signedIn, router]);

  /** Caught here so an obvious mistake costs no round trip; Firebase remains
   *  the authority and its refusal is what the user finally sees. */
  function validate(): { email?: string; key?: string } {
    const found: { email?: string; key?: string } = {};
    if (!email.trim()) found.email = 'Enter an email address.';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) found.email = 'That address is not valid.';
    if (!key) found.key = 'Enter a passphrase.';
    else if (mode === 'register' && key.length < 12)
      found.key = `12 characters minimum. You have ${String(key.length)}.`;
    return found;
  }

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
      router.push('/studio');
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
      setFieldErrors({ email: 'Enter your email address first.' });
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

  if (!firebaseConfigured()) {
    return (
      <div className="mx-auto max-w-lg py-16">
        <Alert tone="danger" title="Sign-in is not configured">
          The Firebase keys are missing from this build. Set the{' '}
          <code className="text-chalk">NEXT_PUBLIC_FIREBASE_*</code> variables and rebuild — they
          are inlined at build time, so restarting will not pick them up.
        </Alert>
      </div>
    );
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center px-4 py-12">
      <div className="w-full max-w-[460px]">
        {/* Two slabs behind the real one: the terminal reads as a physical
            block rather than a card, and the offsets echo the key presses. */}
        <div className="relative">
          <div
            className="absolute inset-0 translate-x-5 translate-y-5 border-4 border-edge bg-crimson/25"
            aria-hidden="true"
          />
          <div
            className="absolute inset-0 translate-x-2.5 translate-y-2.5 border-4 border-edge bg-acid/20"
            aria-hidden="true"
          />

          <Slab depth="lg" className="relative">
            <SlabHead label="Access terminal" right="VX·01" />

            <form onSubmit={submit} className="flex flex-col gap-6 p-6 sm:p-8" noValidate>
              <div>
                <h1 className="display text-4xl leading-[0.92] sm:text-5xl">
                  {mode === 'signin' ? 'Hear one' : 'Open a'}
                  <br />
                  <span className="text-acid">{mode === 'signin' ? 'speaker.' : 'channel.'}</span>
                </h1>
                <p className="mt-3 max-w-[38ch] text-[13px] leading-relaxed text-dust">
                  {mode === 'signin'
                    ? 'Separate overlapping voices into their own tracks, locked to the video.'
                    : 'Twelve characters or more. Nothing is stored in the browser.'}
                </p>
              </div>

              <Key
                tone="bone"
                size="md"
                full
                type="button"
                loading={busy === 'google'}
                disabled={busy !== null}
                busyLabel="Opening…"
                onClick={() => void run('google', signInWithGoogle)}
              >
                <GoogleMark />
                {mode === 'signin' ? 'Continue with Google' : 'Sign up with Google'}
              </Key>

              <div className="flex items-center gap-3">
                <span className="h-1 flex-1 bg-edge" />
                <span className="etch">or passphrase</span>
                <span className="h-1 flex-1 bg-edge" />
              </div>

              <div className="flex flex-col gap-5">
                <Field
                  label="Email"
                  id="auth-email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@studio.com"
                  required
                  value={email}
                  error={fieldErrors.email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                  }}
                />
                <Field
                  label="Passphrase"
                  id="auth-key"
                  type="password"
                  autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                  placeholder="••••••••••••"
                  required
                  value={key}
                  error={fieldErrors.key}
                  hint={mode === 'register' ? '12 characters minimum.' : undefined}
                  onChange={(e) => {
                    setKey(e.target.value);
                  }}
                />
              </div>

              {error !== null && <Alert tone="danger">{error}</Alert>}
              {notice !== null && (
                <Alert tone="accent" title="Check your inbox">
                  {notice}
                </Alert>
              )}

              <Key
                tone="crimson"
                size="lg"
                full
                type="submit"
                loading={busy === 'email'}
                disabled={busy !== null}
                busyLabel="Opening…"
              >
                {mode === 'signin' ? 'Sign in' : 'Create account'}
              </Key>

              <div className="flex flex-wrap items-center justify-between gap-3 border-t-4 border-edge pt-5">
                <span className="font-mono text-[11px] text-dust">
                  {mode === 'signin' ? 'No account yet?' : 'Already have one?'}
                </span>
                <div className="flex gap-2">
                  {mode === 'signin' && (
                    <Key
                      tone="ghost"
                      size="sm"
                      type="button"
                      loading={busy === 'reset'}
                      disabled={busy !== null}
                      busyLabel="Sending…"
                      onClick={() => void reset()}
                    >
                      Reset
                    </Key>
                  )}
                  <Key
                    tone="ghost"
                    size="sm"
                    type="button"
                    disabled={busy !== null}
                    onClick={() => {
                      setMode(mode === 'signin' ? 'register' : 'signin');
                      setFieldErrors({});
                      setError(null);
                      setNotice(null);
                    }}
                  >
                    {mode === 'signin' ? 'Register' : 'Sign in'}
                  </Key>
                </div>
              </div>
            </form>
          </Slab>
        </div>

        <p className="mt-6 text-[11px] leading-relaxed text-dust">
          Voiceprints and face crops are deleted when a job finishes. Deleting your account removes
          your recordings and your sign-in together.
        </p>
      </div>
    </div>
  );
}
