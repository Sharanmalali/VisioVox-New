/**
 * Component primitives — the console.
 *
 * Everything tactile here is built from one idea: a hard black offset with no
 * blur, which the element falls into when pressed. Blur would make it a
 * shadow; the absence of blur makes it an extrusion. A pressed element travels
 * exactly its own offset, so it lands flush with the surface behind it.
 *
 * Every colour is a token. Nothing takes a raw hex or a stock Tailwind palette
 * value, because the contrast gate only checks the token file — a stray
 * `text-blue-400` is invisible to it and would ship unverified.
 *
 * The names are the vocabulary of the object: a Slab is bolted down, a Key is
 * pressed, a Field is recessed. `Card` and `Button` remain as aliases so the
 * rest of the app did not have to be rewritten to adopt this.
 */
'use client';

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  TextareaHTMLAttributes,
} from 'react';
import { useId } from 'react';

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

// -------------------------------------------------------------------- depth --

/** Hard offsets, zero blur. The whole design rests on these three values. */
export const DEPTH = {
  sm: 'shadow-[4px_4px_0_0_var(--edge)]',
  md: 'shadow-[7px_7px_0_0_var(--edge)]',
  lg: 'shadow-[12px_12px_0_0_var(--edge)]',
} as const;

/** Travel equal to the offset, so the element lands flush rather than sinking
 *  into the panel or hovering above it. */
const PRESS = {
  sm:
    'active:translate-x-[4px] active:translate-y-[4px] active:shadow-[0_0_0_0_var(--edge)] ' +
    'transition-[transform,box-shadow,background-color] duration-75 ease-out',
  md:
    'active:translate-x-[7px] active:translate-y-[7px] active:shadow-[0_0_0_0_var(--edge)] ' +
    'transition-[transform,box-shadow,background-color] duration-75 ease-out',
  lg:
    'active:translate-x-[12px] active:translate-y-[12px] active:shadow-[0_0_0_0_var(--edge)] ' +
    'transition-[transform,box-shadow,background-color] duration-75 ease-out',
} as const;

type Depth = keyof typeof DEPTH;

// --------------------------------------------------------------------- Slab --

/** A panel bolted to the rack. The structural unit of every screen. */
export function Slab({
  as: Tag = 'div',
  depth = 'md',
  className,
  children,
}: {
  as?: 'div' | 'section' | 'article' | 'form' | 'aside';
  depth?: Depth | 'none';
  className?: string;
  children: ReactNode;
}) {
  return (
    <Tag
      className={cx(
        'border-4 border-edge bg-panel',
        depth === 'none' ? '' : DEPTH[depth],
        className,
      )}
    >
      {children}
    </Tag>
  );
}

/** The strip across the top of a slab that says what the module is. */
export function SlabHead({
  label,
  tone = 'acid',
  right,
}: {
  label: string;
  tone?: 'acid' | 'crimson' | 'bone';
  right?: ReactNode;
}) {
  const bg = { acid: 'bg-acid', crimson: 'bg-crimson', bone: 'bg-chalk' }[tone];
  const fg = tone === 'crimson' ? 'text-white' : 'text-edge';
  return (
    <div
      className={cx(
        'flex items-center justify-between gap-3 border-b-4 border-edge px-5 py-2.5',
        bg,
        fg,
      )}
    >
      <span className="font-mono text-[11px] font-bold uppercase tracking-[0.22em]">{label}</span>
      {right !== undefined && <span className="font-mono text-[11px] font-bold">{right}</span>}
    </div>
  );
}

/** Kept so existing pages render as slabs without being rewritten. */
export function Card({
  children,
  className,
  as = 'div',
}: {
  children: ReactNode;
  className?: string;
  /** Accepted and ignored — there is no glass in this world. */
  glass?: boolean;
  as?: 'div' | 'section' | 'article' | 'form';
}) {
  return (
    <Slab as={as} className={cx('p-5', className)}>
      {children}
    </Slab>
  );
}

// ---------------------------------------------------------------------- Key --

export type KeyTone = 'acid' | 'crimson' | 'bone' | 'ghost';
type KeySize = 'sm' | 'md' | 'lg';

const TONE_FACE: Record<KeyTone, string> = {
  acid: 'bg-acid text-edge hover:bg-acid-hot',
  crimson: 'bg-crimson text-white hover:bg-crimson-hot',
  bone: 'bg-chalk text-edge hover:bg-white',
  ghost: 'bg-raised text-chalk hover:bg-[color-mix(in_srgb,var(--raised)_70%,var(--chalk))]',
};

const KEY_SIZE: Record<KeySize, string> = {
  sm: 'px-3 py-1.5 text-[11px]',
  md: 'px-5 py-3 text-sm',
  lg: 'px-7 py-4 text-base',
};

export interface KeyProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  tone?: KeyTone;
  size?: KeySize;
  full?: boolean;
  /** Blocks interaction and shows the label given in `busyLabel`. */
  loading?: boolean;
  busyLabel?: string | undefined;
  children: ReactNode;
}

export function Key({
  tone = 'acid',
  size = 'md',
  full = false,
  loading = false,
  busyLabel,
  disabled,
  className,
  children,
  ...rest
}: KeyProps) {
  const locked = disabled === true || loading;
  const depth = size === 'sm' ? 'sm' : 'md';
  return (
    <button
      {...rest}
      disabled={locked}
      aria-busy={loading || undefined}
      className={cx(
        'inline-flex items-center justify-center gap-2 border-4 border-edge',
        'font-mono font-bold uppercase tracking-[0.12em]',
        DEPTH[depth],
        PRESS[depth],
        TONE_FACE[tone],
        KEY_SIZE[size],
        full && 'w-full',
        locked && 'cursor-not-allowed opacity-40 active:translate-x-0 active:translate-y-0',
        className,
      )}
    >
      {loading && <Spinner size={size === 'lg' ? 16 : 13} />}
      {loading && busyLabel !== undefined ? busyLabel : children}
    </button>
  );
}

/** Kept so existing pages keep working; maps the old variants onto key tones. */
export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: KeySize;
  loading?: boolean;
  /** Accepted and ignored — depth replaced glow. */
  glow?: boolean;
  full?: boolean;
  children: ReactNode;
}

export function Button({ variant = 'primary', glow: _glow, ...rest }: ButtonProps) {
  const tone: KeyTone = (
    { primary: 'acid', secondary: 'ghost', ghost: 'ghost', danger: 'crimson' } as const
  )[variant];
  return <Key tone={tone} {...rest} />;
}

// ------------------------------------------------------------------ Spinner --

export function Spinner({ size = 16, label }: { size?: number; label?: string }) {
  return (
    <span
      role={label === undefined ? 'presentation' : 'status'}
      aria-label={label}
      className="inline-block shrink-0 animate-spin border-2 border-current border-t-transparent"
      style={{ width: size, height: size }}
    />
  );
}

// -------------------------------------------------------------------- Field --

export interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  // Explicitly `| undefined`: the project runs exactOptionalPropertyTypes,
  // so a caller passing a conditional value must be allowed to pass
  // undefined rather than having to spread the prop in conditionally.
  hint?: string | undefined;
  error?: string | undefined;
}

/** Recessed, the inverse of a key: the eye reads it as somewhere to put
 *  something in rather than something to press. */
export function Field({ label, hint, error, id, className, ...rest }: FieldProps) {
  const generated = useId();
  const fieldId = id ?? generated;
  const noteId = `${fieldId}-note`;
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={fieldId} className="etch">
        {label}
      </label>
      <input
        {...rest}
        id={fieldId}
        aria-invalid={error !== undefined || undefined}
        aria-describedby={error !== undefined || hint !== undefined ? noteId : undefined}
        className={cx(
          'w-full border-4 bg-void px-4 py-3 text-sm text-chalk',
          'shadow-[var(--depth-inset)] placeholder:text-dust/50',
          error !== undefined ? 'border-crimson' : 'border-edge focus:border-acid',
          className,
        )}
      />
      {error !== undefined ? (
        <span id={noteId} className="font-mono text-[11px] text-crimson">
          {error}
        </span>
      ) : hint !== undefined ? (
        <span id={noteId} className="font-mono text-[11px] text-dust">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string;
  hint?: string | undefined;
}

export function TextArea({ label, hint, id, className, ...rest }: TextAreaProps) {
  const generated = useId();
  const fieldId = id ?? generated;
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={fieldId} className="etch">
        {label}
      </label>
      <textarea
        {...rest}
        id={fieldId}
        className={cx(
          'w-full border-4 border-edge bg-void px-4 py-3 text-sm text-chalk',
          'shadow-[var(--depth-inset)] focus:border-acid',
          className,
        )}
      />
      {hint !== undefined && <span className="font-mono text-[11px] text-dust">{hint}</span>}
    </div>
  );
}

// ----------------------------------------------------------------- Progress --

export function Progress({
  value,
  max = 100,
  label,
  tone = 'accent',
}: {
  /** Omit for indeterminate — genuinely unknown progress, never a fake crawl. */
  value?: number;
  max?: number;
  label?: string;
  tone?: 'accent' | 'success' | 'warning';
}) {
  const pct = typeof value === 'number' ? Math.min(100, Math.max(0, (value / max) * 100)) : null;
  const face = { accent: 'bg-acid', success: 'bg-acid', warning: 'bg-[var(--warning)]' }[tone];

  return (
    <div className="flex flex-col gap-2">
      {(label !== undefined || pct !== null) && (
        <div className="flex items-baseline justify-between gap-3">
          {label !== undefined && <span className="etch">{label}</span>}
          {pct !== null && (
            <span className="font-mono text-[11px] font-bold tabular-nums text-acid">
              {Math.round(pct)}%
            </span>
          )}
        </div>
      )}
      <div
        role="progressbar"
        aria-valuenow={pct === null ? undefined : Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        className="h-5 w-full border-4 border-edge bg-void shadow-[var(--depth-inset)]"
      >
        {pct === null ? (
          <div className={cx('ticker h-full w-1/3', face)} />
        ) : (
          <div
            className={cx('h-full transition-[width] duration-300 ease-out', face)}
            style={{ width: `${String(pct)}%` }}
          />
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------ Badge / Alert --

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';

const TONE_EDGE: Record<Tone, string> = {
  neutral: 'bg-raised text-dust',
  accent: 'bg-acid text-edge',
  success: 'bg-acid text-edge',
  warning: 'bg-[var(--warning)] text-edge',
  danger: 'bg-crimson text-white',
};

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  return (
    <span
      className={cx(
        // self-start matters: inside a flex column the default is stretch, and
        // a tag that spans the page reads as a broken banner.
        'inline-flex w-fit shrink-0 self-start items-center gap-1.5 border-2 border-edge px-2 py-0.5',
        'font-mono text-[10px] font-bold uppercase tracking-[0.14em]',
        TONE_EDGE[tone],
      )}
    >
      {children}
    </span>
  );
}

export function Alert({
  tone = 'danger',
  title,
  children,
}: {
  tone?: Tone;
  title?: string;
  children: ReactNode;
}) {
  const stripe = {
    neutral: 'bg-dust',
    accent: 'bg-acid',
    success: 'bg-acid',
    warning: 'bg-[var(--warning)]',
    danger: 'bg-crimson',
  }[tone];

  return (
    <div
      // assertive for errors, polite otherwise: an error the user must act on
      // should interrupt, a status update should not.
      role={tone === 'danger' ? 'alert' : 'status'}
      className={cx('flex border-4 border-edge bg-panel', DEPTH.sm)}
    >
      {/* The tone is a solid bar, not a tinted background: at this contrast a
          wash would be either invisible or unreadable. */}
      <span className={cx('w-2.5 shrink-0', stripe)} aria-hidden="true" />
      <div className="px-4 py-3 text-sm">
        {title !== undefined && <p className="etch mb-1 text-chalk">{title}</p>}
        <div className="text-dust">{children}</div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------- Skeleton --

export function Skeleton({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className={cx('ticker border-4 border-edge bg-raised', className)} />
  );
}

// ------------------------------------------------------------- SpeakerDot --

/** Speaker identity. A square, because nothing here is round — but the hue is
 *  the same one the player and the transcript use, and colour is never the sole
 *  identifier (NFR-A11Y-06). */
export function SpeakerDot({ index, size = 10 }: { index: number; size?: number }) {
  const slot = ((index - 1) % 4) + 1;
  const hue = `var(--spk-${String(slot)})`;
  return (
    <span
      aria-hidden="true"
      className="inline-block shrink-0 border-2 border-edge"
      style={{ width: size, height: size, background: hue }}
    />
  );
}
