'use client';

/**
 * Firebase identity for the browser (ADR-0016).
 *
 * Firebase owns the session. This module is the only place the SDK is touched,
 * so the rest of the app keeps asking for "the current ID token" and does not
 * care where it comes from.
 *
 * **The config here is not a secret.** A Firebase web API key identifies the
 * project; it authorises nothing on its own. What protects the account is the
 * server verifying the ID token's signature and audience, which
 * `firebase_auth.verify` does on every request. Restricting the key by HTTP
 * referrer in the Google Cloud console is still worth doing - it stops other
 * sites burning your quota - but it is not what keeps data safe.
 *
 * The ID token lives in memory and lasts an hour. It is never written to
 * localStorage: the app renders transcripts derived from user media, so an XSS
 * reaching a persisted credential is the failure this avoids.
 */

import { initializeApp, getApps, type FirebaseApp } from 'firebase/app';
import {
  GoogleAuthProvider,
  browserSessionPersistence,
  createUserWithEmailAndPassword,
  getAuth,
  onAuthStateChanged,
  sendPasswordResetEmail,
  setPersistence,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
  type Auth,
  type User,
} from 'firebase/auth';

const config = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY ?? '',
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN ?? '',
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID ?? '',
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID ?? '',
};

export function firebaseConfigured(): boolean {
  return Boolean(config.apiKey && config.projectId);
}

let app: FirebaseApp | null = null;

function client(): Auth {
  if (!firebaseConfigured()) {
    throw new Error(
      'Firebase is not configured. Set NEXT_PUBLIC_FIREBASE_* and rebuild — these are ' +
        'inlined at build time, so a restart will not pick them up.',
    );
  }
  app ??= getApps()[0] ?? initializeApp(config);
  return getAuth(app);
}

/**
 * Session-scoped persistence: the session ends with the tab.
 *
 * Firebase defaults to IndexedDB, which survives a browser restart and is
 * readable by any script on the origin — the same property that kept tokens
 * out of localStorage rules it out here. The cost is signing in again after
 * closing the browser, which is the right trade for a tool that renders
 * untrusted-derived text.
 */
async function configured(): Promise<Auth> {
  const auth = client();
  await setPersistence(auth, browserSessionPersistence);
  return auth;
}

export async function registerWithEmail(email: string, password: string): Promise<User> {
  const auth = await configured();
  const { user } = await createUserWithEmailAndPassword(auth, email, password);
  return user;
}

export async function signInWithEmail(email: string, password: string): Promise<User> {
  const auth = await configured();
  const { user } = await signInWithEmailAndPassword(auth, email, password);
  return user;
}

export async function signInWithGoogle(): Promise<User> {
  const auth = await configured();
  const provider = new GoogleAuthProvider();
  // Always show the chooser. Silently reusing whichever Google account the
  // browser happens to hold is how people end up with two VisioVox accounts
  // and no idea which one has their recordings.
  provider.setCustomParameters({ prompt: 'select_account' });
  const { user } = await signInWithPopup(auth, provider);
  return user;
}

export async function sendReset(email: string): Promise<void> {
  const auth = await configured();
  await sendPasswordResetEmail(auth, email);
}

export async function signOutEverywhere(): Promise<void> {
  await signOut(client());
}

/**
 * The current ID token, refreshed when it is close to expiring.
 *
 * Firebase caches and renews this itself, so calling it per request is cheap
 * and is the intended usage — holding one for an hour is not.
 */
export async function idToken(force = false): Promise<string | null> {
  if (!firebaseConfigured()) return null;
  const user = client().currentUser;
  if (!user) return null;
  return user.getIdToken(force);
}

/** Fires once on load with the restored user, then on every change. */
export function watchAuth(fn: (user: User | null) => void): () => void {
  if (!firebaseConfigured()) {
    fn(null);
    return () => undefined;
  }
  return onAuthStateChanged(client(), fn);
}

/**
 * Firebase error codes, rewritten for the person reading them.
 *
 * The raw codes leak implementation ("auth/invalid-credential") and some are
 * actively misleading — `email-already-in-use` on a Google-created account
 * tells someone to sign in with a password they never set.
 */
export function explain(error: unknown): string {
  const code =
    typeof error === 'object' && error !== null && 'code' in error ? String(error.code) : '';

  switch (code) {
    case 'auth/invalid-email':
      return 'That email address is not valid.';
    case 'auth/missing-password':
      return 'Enter your password.';
    case 'auth/weak-password':
      return 'Passwords need at least 6 characters. Longer is better.';
    case 'auth/email-already-in-use':
      return 'An account already exists for that address. Try signing in, or use Google if that is how you set it up.';
    case 'auth/invalid-credential':
    case 'auth/wrong-password':
    case 'auth/user-not-found':
      // One message for all three: distinguishing them confirms which
      // addresses have accounts.
      return 'That email and password do not match an account.';
    case 'auth/too-many-requests':
      return 'Too many attempts. Wait a few minutes, or reset your password.';
    case 'auth/popup-closed-by-user':
    case 'auth/cancelled-popup-request':
      return '';
    case 'auth/popup-blocked':
      return 'Your browser blocked the sign-in window. Allow pop-ups for this site and try again.';
    case 'auth/account-exists-with-different-credential':
      return 'That address is already registered with a different sign-in method. Use the one you set up originally.';
    case 'auth/network-request-failed':
      return 'Could not reach the sign-in service. Check your connection.';
    case 'auth/operation-not-allowed':
      return 'That sign-in method is not enabled for this project.';
    default:
      return 'Could not sign you in. Try again.';
  }
}
