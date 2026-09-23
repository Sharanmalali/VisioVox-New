import type { Metadata } from 'next';
import { Space_Mono, Syne } from 'next/font/google';
import type { ReactNode } from 'react';
import './globals.css';

/* Self-hosted by next/font: no request to fonts.gstatic.com at runtime, so the
 * console cannot render in a fallback face on a slow connection. Space Mono is
 * the voice of the interface and Syne is the display face; both are loaded
 * with `swap` so text is readable before the face arrives. */
const mono = Space_Mono({
  subsets: ['latin'],
  weight: ['400', '700'],
  display: 'swap',
  variable: '--font-mono-face',
});

const display = Syne({
  subsets: ['latin'],
  weight: ['600', '700', '800'],
  display: 'swap',
  variable: '--font-display-face',
});

export const metadata: Metadata = {
  title: 'VisioVox',
  description: 'Hear one speaker at a time.',
};

/* No chrome here. The console draws its own fascia, and a global header
 * would sit above it as a second bar from the old design. Pages that want
 * navigation render it themselves. */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${mono.variable} ${display.variable}`}>
      <body>
        <main className="main">{children}</main>
      </body>
    </html>
  );
}
