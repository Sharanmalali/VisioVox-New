/**
 * Join class names, skipping falsy parts.
 *
 * Its own module, free of 'use client', so Server Components can call it too;
 * `@/components/ui` re-exports it for client code.
 */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}
