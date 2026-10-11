/**
 * Videos from Google Drive and Google Photos (the API's google_media.py).
 *
 * Access comes from the person, per pick, with the narrowest scopes Google
 * offers: `drive.file` grants this site only the files picked in the Drive
 * picker, and `photospicker.mediaitems.readonly` only the items picked in
 * Google Photos. The token is fetched through a separate, memory-only
 * Firebase instance - the same Google sign-in the site already uses, so no
 * new OAuth client is needed - which leaves the person's VisioVox session,
 * and whichever way they signed in, untouched. It is handed to the server
 * once, for the download, and forgotten.
 */

import { deleteApp, initializeApp } from 'firebase/app';
import {
  GoogleAuthProvider,
  browserPopupRedirectResolver,
  getAdditionalUserInfo,
  inMemoryPersistence,
  initializeAuth,
  signInWithPopup,
  signOut,
} from 'firebase/auth';

export const DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.file';
export const PHOTOS_SCOPE = 'https://www.googleapis.com/auth/photospicker.mediaitems.readonly';

const config = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY ?? '',
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN ?? '',
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID ?? '',
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID ?? '',
};
// The Picker's developer key: a key allowed to use the Picker API. Defaults
// to the site's Firebase browser key.
const PICKER_KEY = process.env.NEXT_PUBLIC_GOOGLE_PICKER_API_KEY || config.apiKey;
// The Cloud project number, which the Drive picker needs: it is the second
// field of the Firebase app id ("1:<number>:web:<hash>").
const PROJECT_NUMBER = config.appId.split(':')[1] ?? '';

/** A sentence for the person, never Google's or Firebase's codes. */
export class GoogleMediaError extends Error {}

const cached = new Map<string, { token: string; until: number }>();

function code(error: unknown): string {
  return typeof error === 'object' && error !== null && 'code' in error ? String(error.code) : '';
}

/**
 * A Google access token for `scope`, asking the person in a popup.
 * Reused for 45 minutes (tokens last an hour). Null if they closed the popup.
 */
export async function googleAccessToken(scope: string): Promise<string | null> {
  const hit = cached.get(scope);
  if (hit !== undefined && hit.until > Date.now()) return hit.token;

  const app = initializeApp(config, `google-media-${String(Date.now())}`);
  const auth = initializeAuth(app, {
    persistence: inMemoryPersistence,
    popupRedirectResolver: browserPopupRedirectResolver,
  });
  const provider = new GoogleAuthProvider();
  provider.addScope(scope);
  provider.setCustomParameters({ prompt: 'select_account' });
  let token: string | null = null;
  try {
    const result = await signInWithPopup(auth, provider);
    token = GoogleAuthProvider.credentialFromResult(result)?.accessToken ?? null;
    // Only a token was wanted. If that Google account was not yet a sign-in
    // of this site, leave no account behind.
    if (getAdditionalUserInfo(result)?.isNewUser === true) {
      await result.user.delete().catch(() => undefined);
    }
  } catch (error) {
    const c = code(error);
    if (c === 'auth/popup-closed-by-user' || c === 'auth/cancelled-popup-request') return null;
    if (c === 'auth/popup-blocked') {
      throw new GoogleMediaError(
        'Your browser blocked the Google window. Allow pop-ups for this site and try again.',
      );
    }
    // The Google account is registered here with another sign-in method:
    // Firebase refuses the sign-in but still hands over Google's token.
    token =
      GoogleAuthProvider.credentialFromError(
        error as Parameters<typeof GoogleAuthProvider.credentialFromError>[0],
      )?.accessToken ?? null;
    if (token === null) {
      throw new GoogleMediaError('Google did not give access. Try again.');
    }
  } finally {
    await signOut(auth).catch(() => undefined);
    await deleteApp(app).catch(() => undefined);
  }
  if (token === null) throw new GoogleMediaError('Google did not give access. Try again.');
  cached.set(scope, { token, until: Date.now() + 45 * 60 * 1000 });
  return token;
}

/** Forget a token Google has refused (expired or revoked), so the next pick asks again. */
export function forgetToken(scope: string): void {
  cached.delete(scope);
}

// ----------------------------------------------------------- Drive picker --

interface PickerDoc {
  id: string;
  name?: string;
  mimeType?: string;
  sizeBytes?: number | string;
}
interface PickerData {
  action: string;
  docs?: PickerDoc[];
}
interface PickerBuilderLike {
  addView(view: unknown): PickerBuilderLike;
  setOAuthToken(token: string): PickerBuilderLike;
  setDeveloperKey(key: string): PickerBuilderLike;
  setAppId(id: string): PickerBuilderLike;
  setTitle(title: string): PickerBuilderLike;
  setOrigin(origin: string): PickerBuilderLike;
  setCallback(fn: (data: PickerData) => void): PickerBuilderLike;
  build(): { setVisible(visible: boolean): void };
}
interface GooglePickerNamespace {
  picker: {
    PickerBuilder: new () => PickerBuilderLike;
    DocsView: new (viewId?: unknown) => {
      setIncludeFolders(b: boolean): unknown;
      setMimeTypes(types: string): unknown;
    };
    ViewId: { DOCS_VIDEOS: unknown };
    Action: { PICKED: string; CANCEL: string };
  };
}
interface GapiLike {
  load(name: string, callback: () => void): void;
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`) !== null) {
      resolve();
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.onload = () => {
      resolve();
    };
    script.onerror = () => {
      reject(new GoogleMediaError('Could not load Google Drive. Check your connection.'));
    };
    document.head.appendChild(script);
  });
}

async function pickerApi(): Promise<GooglePickerNamespace['picker']> {
  await loadScript('https://apis.google.com/js/api.js');
  const gapi = (window as unknown as { gapi?: GapiLike }).gapi;
  if (gapi === undefined) throw new GoogleMediaError('Could not load Google Drive.');
  await new Promise<void>((resolve) => {
    gapi.load('picker', () => {
      resolve();
    });
  });
  const google = (window as unknown as { google?: GooglePickerNamespace }).google;
  if (google?.picker === undefined) throw new GoogleMediaError('Could not load Google Drive.');
  return google.picker;
}

export interface DrivePick {
  fileId: string;
  name: string;
  mimeType: string;
  sizeBytes: number | null;
}

/** Google's own Drive picker, videos only; null if the person cancels. */
export async function pickFromDrive(token: string): Promise<DrivePick | null> {
  const picker = await pickerApi();
  return new Promise((resolve) => {
    const view = new picker.DocsView(picker.ViewId.DOCS_VIDEOS);
    view.setIncludeFolders(true);
    view.setMimeTypes(
      'video/mp4,video/quicktime,video/webm,video/x-matroska,video/x-msvideo,video/mpeg,video/3gpp',
    );
    new picker.PickerBuilder()
      .addView(view)
      .setOAuthToken(token)
      .setDeveloperKey(PICKER_KEY)
      .setAppId(PROJECT_NUMBER)
      .setOrigin(window.location.origin)
      .setTitle('Choose a video for VisioVox')
      .setCallback((data) => {
        if (data.action === picker.Action.PICKED) {
          const doc = data.docs?.[0];
          if (doc === undefined) {
            resolve(null);
            return;
          }
          const size = doc.sizeBytes === undefined ? null : Number(doc.sizeBytes);
          resolve({
            fileId: doc.id,
            name: doc.name ?? 'video',
            mimeType: doc.mimeType ?? '',
            sizeBytes: size !== null && Number.isFinite(size) ? size : null,
          });
        } else if (data.action === picker.Action.CANCEL) {
          resolve(null);
        }
      })
      .build()
      .setVisible(true);
  });
}
