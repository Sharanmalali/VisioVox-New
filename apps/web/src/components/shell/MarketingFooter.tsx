import Link from 'next/link';

import { Logo } from '@/components/icons';

const COLUMNS = [
  {
    title: 'Product',
    links: [
      { href: '/#product', label: 'Features' },
      { href: '/#how-it-works', label: 'How it works' },
      { href: '/#languages', label: 'Languages' },
      { href: '/demo', label: 'Live demo' },
    ],
  },
  {
    title: 'Trust',
    links: [
      { href: '/#results', label: 'Measured results' },
      { href: '/#privacy', label: 'Privacy' },
      { href: '/#limits', label: 'What it will not do' },
    ],
  },
  {
    title: 'Account',
    links: [
      { href: '/login', label: 'Sign in' },
      { href: '/upload', label: 'Upload a recording' },
      { href: '/projects', label: 'Your projects' },
    ],
  },
];

export function MarketingFooter() {
  return (
    <footer className="border-t border-border-subtle bg-bg-subtle">
      <div className="container-x grid gap-12 py-16 md:grid-cols-[1.4fr_repeat(3,1fr)]">
        <div className="flex max-w-xs flex-col gap-4">
          <Logo />
          <p className="text-sm leading-relaxed text-fg-muted">
            Face-guided speaker separation for real recordings. One clean track per person, in sync
            with the video, with captions.
          </p>
        </div>
        {COLUMNS.map((col) => (
          <div key={col.title} className="flex flex-col gap-3">
            <h3 className="text-[13px] font-semibold text-fg-primary">{col.title}</h3>
            <ul className="flex flex-col gap-2.5">
              {col.links.map((link) => (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    className="text-sm text-fg-muted transition-colors hover:text-fg-primary"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-border-subtle">
        <div className="container-x flex flex-col gap-2 py-6 text-[13px] text-fg-muted sm:flex-row sm:items-center sm:justify-between">
          <p>© {new Date().getFullYear()} VisioVox. All rights reserved.</p>
          <p>
            Processing runs on our own hardware. Face crops and voiceprints are deleted after each
            job.
          </p>
        </div>
      </div>
    </footer>
  );
}
