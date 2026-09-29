'use client';

/**
 * Client-side session and API access (ADR-0016).
 *
 * Firebase owns the session; this holds no credential of its own. The API
 * client asks for a fresh ID token before every request, and Firebase serves
 * it from its own cache and renews it when it is close to expiring — so the
 * app never has to think about refresh, rotation or expiry.
 *
 * Zustand selectors are always narrow (repository convention). Subscribing to
 * the whole store re-renders every consumer on any change, which is the
 * documented cause of the player dropping from 60 fps to 10.
 */

import { VisioVoxClient } from '@visiovox/ts-client';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { create } from 'zustand';

import { idToken, signOutEverywhere, watchAuth } from '@/lib/firebase';

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

/**
 * The token the SSE stream needs.
 *
 * `EventSource` cannot set an Authorization header, so the progress stream
 * takes the token on the query string. Kept here as the last value Firebase
 * handed out, because that call is async and building a URL is not.
 */
let lastToken: string | null = null;

let clientSingleton: VisioVoxClient | null = null;

export function api(): VisioVoxClient {
  clientSingleton ??= new VisioVoxClient({
    baseUrl: API_BASE,
    // Resolved per request rather than stored. Firebase caches and renews, so
    // this is the intended usage and costs nothing when the token is fresh.
    getToken: async () => {
      const token = await idToken();
      lastToken = token;
      return token;
    },
  });
  return clientSingleton;
}

export function accessToken(): string | null {
  return lastToken;
}

interface SessionState {
  signedIn: boolean;
  email: string | null;
  ready: boolean;
  setSignedIn: (signedIn: boolean, email?: string | null) => void;
  hydrate: () => void;
  signOut: () => Promise<void>;
}

export const useSession = create<SessionState>((set) => ({
  signedIn: false,
  email: null,
  ready: false,

  setSignedIn: (signedIn, email = null) => {
    set({ signedIn, email });
  },

  /**
   * Subscribe to Firebase rather than fetch anything.
   *
   * The listener fires once with the restored user — or null — which is what
   * makes `ready` meaningful: before it fires, "signed out" and "not yet known"
   * are indistinguishable, and redirecting on the difference is how a refresh
   * bounces someone to the login page they were already past.
   */
  hydrate: () => {
    watchAuth((user) => {
      lastToken = null;
      set({ signedIn: user !== null, email: user?.email ?? null, ready: true });
      if (user) {
        // Materialise the local row on the API side. The dependency creates it
        // on first sight, so this is just the earliest convenient moment.
        void api()
          .me()
          .catch(() => undefined);
      }
    });
  },

  signOut: async () => {
    await signOutEverywhere();
    lastToken = null;
    set({ signedIn: false, email: null });
  },
}));

/**
 * Gate for a page that calls the API as soon as it mounts.
 *
 * Firebase restores a session asynchronously, so on a fresh page load - a
 * link from the vault, a refresh, a pasted URL - the first render happens
 * before any user is known. A request made then goes out with no token and
 * the API rightly answers 401, which such a page used to show as its error
 * ("401: missing bearer token") and never retried. Returns true once a
 * signed-in session is restored; sends a signed-out visitor to the login page.
 */
export function useRequireSignIn(): boolean {
  const router = useRouter();
  const signedIn = useSession((s) => s.signedIn);
  const ready = useSession((s) => s.ready);
  const hydrate = useSession((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (ready && !signedIn) router.replace('/login');
  }, [ready, signedIn, router]);

  return ready && signedIn;
}
