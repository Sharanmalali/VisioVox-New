import { redirect } from 'next/navigation';

/**
 * Studio was a second upload screen that had fallen behind /upload (no link
 * imports, no Google Drive, no trimming, no heads-up before a long job). It
 * was folded into /upload; this keeps old links and bookmarks working.
 */
export default function StudioPage(): never {
  redirect('/upload');
}
