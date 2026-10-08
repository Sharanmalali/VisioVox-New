import type { Metadata, Viewport } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import type { ReactNode } from 'react';
import './globals.css';

/* Self-hosted by next/font: no request to fonts.gstatic.com at runtime, and
 * `swap` so text is readable before the face arrives. Inter carries the whole
 * interface; JetBrains Mono is only for timecodes, sizes and code. */
const sans = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-sans-face',
});

const mono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  display: 'swap',
  variable: '--font-mono-face',
});

export const metadata: Metadata = {
  title: {
    default: 'VisioVox — Hear every voice, one at a time',
    template: '%s · VisioVox',
  },
  description:
    'Upload a video where people talk over each other. VisioVox follows each face, gives every speaker their own clean audio track, and captions them in 100 languages.',
  applicationName: 'VisioVox',
  openGraph: {
    title: 'VisioVox — Hear every voice, one at a time',
    description:
      'Face-guided speaker separation for real recordings: one track per person, in sync with the video, with captions.',
    type: 'website',
    siteName: 'VisioVox',
  },
  twitter: { card: 'summary_large_image' },
};

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#0a0b10' },
  ],
  width: 'device-width',
  initialScale: 1,
};

/* Runs before first paint, so neither theme flashes the other. A stored choice
 * wins; otherwise the system preference decides. Kept tiny and dependency-free
 * because it blocks rendering by design. */
const THEME_BOOTSTRAP = `(function(){try{var s=localStorage.getItem('vv-theme');var d=s?s==='dark':matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.dataset.theme=d?'dark':'light';}catch(e){}})();`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-[var(--radius-sm)] focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-fg"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
