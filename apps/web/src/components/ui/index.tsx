/**
 * Component primitives.
 *
 * Quiet by default and loud only where it matters: surfaces are paper with a
 * hairline and a soft contact shadow, the primary action is ink, and the one
 * accent colour marks focus, progress and the current item. Every colour is a
 * token, because the contrast gate only checks the token file - a stray
 * `text-blue-400` would ship unverified.
 *
 * The export names are the ones the app has always used (Card, Button, Key,
 * Slab, Field...), so redesigning these did not mean rewriting every caller.
 */
'use client';

import Link from 'next/link';
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react';
import { useId } from 'react';

import { IconAlert, IconCheckCircle, IconChevronDown, IconInfo } from '@/components/icons';
import { cx } from '@/lib/cx';

export { cx };

// ----------------------------------------------------------------- Surfaces --

type SurfaceTag = 'div' | 'section' | 'article' | 'form' | 'aside';

/** The structural unit of every screen: paper, a hairline, a contact shadow. */
export function Card({
  as: Tag = 'div',
  className,
  children,
  interactive = false,
}: {
  as?: SurfaceTag;
  className?: string;
  children: ReactNode;
  /** Lifts on hover; for cards that are themselves a link or a button. */
  interactive?: boolean;
  /** Accepted for older callers and ignored. */
  glass?: boolean;
}) {
  return (
    <Tag
      className={cx(
        'rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface p-6 shadow-sm',
        interactive &&
          'transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:border-border-default hover:shadow-md',
        className,
      )}
    >
      {children}
    </Tag>
  );
}

/** A card without padding, for compositions with a header strip and a body. */
export function Slab({
  as: Tag = 'div',
  className,
  children,
}: {
  as?: SurfaceTag;
  /** Accepted for older callers and ignored. */
  depth?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Tag
      className={cx(
        'overflow-hidden rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface shadow-sm',
        className,
      )}
    >
      {children}
    </Tag>
  );
}

/** The header of a Slab: a title, and optionally something on the right. */
export function SlabHead({
  label,
  description,
  right,
  icon,
}: {
  label: string;
  description?: string;
  right?: ReactNode;
  icon?: ReactNode;
  /** Accepted for older callers and ignored. */
  tone?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border-subtle px-5 py-4 sm:px-6">
      <div className="flex min-w-0 items-start gap-3">
        {icon !== undefined && (
          <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-bg-inset text-fg-secondary">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-fg-primary">{label}</h2>
          {description !== undefined && (
            <p className="mt-0.5 text-[13px] text-fg-muted">{description}</p>
          )}
        </div>
      </div>
      {right !== undefined && <div className="shrink-0">{right}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ Buttons --

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'accent';
/** Older tone names still accepted by `Key`. */
export type KeyTone = ButtonVariant | 'acid' | 'crimson' | 'bone';
type Size = 'sm' | 'md' | 'lg';

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'bg-primary text-primary-fg shadow-sm hover:bg-primary-hover',
  accent: 'bg-accent text-fg-on-accent shadow-sm hover:bg-accent-hover',
  secondary:
    'border border-border-default bg-bg-surface text-fg-primary shadow-xs hover:border-border-strong hover:bg-bg-subtle',
  ghost: 'text-fg-secondary hover:bg-bg-inset hover:text-fg-primary',
  danger:
    'border border-[color-mix(in_srgb,var(--danger)_35%,transparent)] bg-danger-soft text-danger hover:bg-[color-mix(in_srgb,var(--danger)_14%,var(--bg-surface))]',
};

const SIZE: Record<Size, string> = {
  sm: 'h-8 gap-1.5 rounded-[var(--radius-sm)] px-3 text-[13px]',
  md: 'h-10 gap-2 rounded-[var(--radius-sm)] px-4 text-sm',
  lg: 'h-12 gap-2 rounded-[var(--radius-md)] px-6 text-[15px]',
};

const BASE =
  'inline-flex select-none items-center justify-center whitespace-nowrap font-medium tracking-[-0.005em] ' +
  'transition-[background-color,border-color,color,box-shadow,transform] duration-150 ease-out ' +
  'active:translate-y-px disabled:pointer-events-none disabled:opacity-50';

function variantOf(tone: KeyTone): ButtonVariant {
  if (tone === 'acid' || tone === 'crimson') return 'primary';
  if (tone === 'bone') return 'secondary';
  return tone;
}

export interface KeyProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  tone?: KeyTone;
  size?: Size;
  full?: boolean;
  /** Blocks interaction and shows `busyLabel` beside a spinner. */
  loading?: boolean;
  busyLabel?: string | undefined;
  children: ReactNode;
}

export function Key({
  tone = 'primary',
  size = 'md',
  full = false,
  loading = false,
  busyLabel,
  disabled,
  className,
  children,
  type = 'button',
  ...rest
}: KeyProps) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled === true || loading}
      aria-busy={loading || undefined}
      className={cx(BASE, VARIANT[variantOf(tone)], SIZE[size], full && 'w-full', className)}
    >
      {loading && <Spinner size={size === 'lg' ? 16 : 14} />}
      {loading && busyLabel !== undefined ? busyLabel : children}
    </button>
  );
}

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: ButtonVariant;
  size?: Size;
  loading?: boolean;
  busyLabel?: string | undefined;
  full?: boolean;
  /** Accepted for older callers and ignored. */
  glow?: boolean;
  children: ReactNode;
}

export function Button({ variant = 'primary', glow: _glow, ...rest }: ButtonProps) {
  return <Key tone={variant} {...rest} />;
}

/** A link that looks like a button - for navigation, never for actions. */
export function ButtonLink({
  href,
  variant = 'primary',
  size = 'md',
  className,
  children,
  label,
}: {
  href: string;
  variant?: ButtonVariant;
  size?: Size;
  className?: string | undefined;
  children: ReactNode;
  /** Accessible name, when the visible content is only an icon. */
  label?: string | undefined;
}) {
  const external = /^https?:\/\//.test(href);
  const classes = cx(BASE, VARIANT[variant], SIZE[size], className);
  return external ? (
    <a href={href} className={classes} target="_blank" rel="noopener noreferrer" aria-label={label}>
      {children}
    </a>
  ) : (
    <Link href={href} className={classes} aria-label={label}>
      {children}
    </Link>
  );
}

// ------------------------------------------------------------------ Spinner --

export function Spinner({ size = 16, label }: { size?: number; label?: string }) {
  return (
    <span
      role={label === undefined ? 'presentation' : 'status'}
      aria-label={label}
      className="inline-block shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent opacity-80"
      style={{ width: size, height: size }}
    />
  );
}

// ------------------------------------------------------------------- Fields --

const CONTROL =
  'w-full rounded-[var(--radius-sm)] border bg-bg-surface px-3.5 text-sm text-fg-primary shadow-xs ' +
  'placeholder:text-fg-muted transition-[border-color,box-shadow] duration-150 ' +
  'hover:border-border-strong focus:outline-none focus-visible:outline-none ' +
  'focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-ring)] disabled:opacity-60';

function FieldLabel({ htmlFor, children }: { htmlFor: string; children: ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="text-[13px] font-medium text-fg-primary">
      {children}
    </label>
  );
}

function FieldNote({
  id,
  error,
  hint,
}: {
  id: string;
  error?: string | undefined;
  hint?: string | undefined;
}) {
  if (error !== undefined) {
    return (
      <span id={id} className="flex items-center gap-1.5 text-[12.5px] text-danger">
        <IconAlert size={13} />
        {error}
      </span>
    );
  }
  if (hint !== undefined) {
    return (
      <span id={id} className="text-[12.5px] text-fg-muted">
        {hint}
      </span>
    );
  }
  return null;
}

export interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  // Explicitly `| undefined`: the project runs exactOptionalPropertyTypes, so
  // a caller passing a conditional value must be allowed to pass undefined.
  hint?: string | undefined;
  error?: string | undefined;
  /** Rendered at the right of the label row, e.g. a "Forgot?" link. */
  aside?: ReactNode;
}

export function Field({ label, hint, error, aside, id, className, ...rest }: FieldProps) {
  const generated = useId();
  const fieldId = id ?? generated;
  const noteId = `${fieldId}-note`;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-3">
        <FieldLabel htmlFor={fieldId}>{label}</FieldLabel>
        {aside}
      </div>
      <input
        {...rest}
        id={fieldId}
        aria-invalid={error !== undefined || undefined}
        aria-describedby={error !== undefined || hint !== undefined ? noteId : undefined}
        className={cx(
          CONTROL,
          'h-11',
          error !== undefined ? 'border-danger' : 'border-border-default',
          className,
        )}
      />
      <FieldNote id={noteId} error={error} hint={hint} />
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
    <div className="flex flex-col gap-1.5">
      <FieldLabel htmlFor={fieldId}>{label}</FieldLabel>
      <textarea
        {...rest}
        id={fieldId}
        className={cx(CONTROL, 'border-border-default py-2.5', className)}
      />
      <FieldNote id={`${fieldId}-note`} hint={hint} />
    </div>
  );
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  hint?: string | undefined;
  children: ReactNode;
}

export function Select({ label, hint, id, className, children, ...rest }: SelectProps) {
  const generated = useId();
  const fieldId = id ?? generated;
  const noteId = `${fieldId}-note`;
  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel htmlFor={fieldId}>{label}</FieldLabel>
      <div className="relative">
        <select
          {...rest}
          id={fieldId}
          aria-describedby={hint !== undefined ? noteId : undefined}
          className={cx(CONTROL, 'h-11 appearance-none border-border-default pr-10', className)}
        >
          {children}
        </select>
        <IconChevronDown
          size={16}
          className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-fg-muted"
        />
      </div>
      <FieldNote id={noteId} hint={hint} />
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  children,
  id,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  children: ReactNode;
  id?: string;
}) {
  const generated = useId();
  const boxId = id ?? generated;
  return (
    <label
      htmlFor={boxId}
      className="group flex cursor-pointer items-start gap-3 text-sm text-fg-secondary"
    >
      <input
        id={boxId}
        type="checkbox"
        checked={checked}
        onChange={(e) => {
          onChange(e.target.checked);
        }}
        className="mt-0.5 h-[18px] w-[18px] shrink-0 cursor-pointer rounded-[5px] accent-[var(--accent)]"
      />
      <span className="leading-relaxed group-hover:text-fg-primary">{children}</span>
    </label>
  );
}

// ----------------------------------------------------------------- Progress --

export function Progress({
  value,
  max = 100,
  label,
  tone = 'accent',
  showValue = true,
}: {
  /** Omit for indeterminate - genuinely unknown progress, never a fake crawl. */
  value?: number;
  max?: number;
  label?: string;
  tone?: 'accent' | 'success' | 'warning';
  showValue?: boolean;
}) {
  const pct = typeof value === 'number' ? Math.min(100, Math.max(0, (value / max) * 100)) : null;
  const fill = { accent: 'bg-accent', success: 'bg-success', warning: 'bg-warning' }[tone];

  return (
    <div className="flex flex-col gap-2">
      {(label !== undefined || (pct !== null && showValue)) && (
        <div className="flex items-baseline justify-between gap-3 text-[13px]">
          {label !== undefined && <span className="font-medium text-fg-secondary">{label}</span>}
          {pct !== null && showValue && (
            <span className="font-mono text-[12px] tabular-nums text-fg-muted">
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
        className="h-2 w-full overflow-hidden rounded-full bg-bg-inset"
      >
        {pct === null ? (
          <div className={cx('progress-indeterminate h-full w-1/3 rounded-full', fill)} />
        ) : (
          <div
            className={cx('h-full rounded-full transition-[width] duration-500 ease-out', fill)}
            style={{ width: `${String(pct)}%` }}
          />
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------- Badge / Alert --

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';

const BADGE: Record<Tone, string> = {
  neutral: 'border-border-default bg-bg-subtle text-fg-secondary',
  accent: 'border-[color-mix(in_srgb,var(--accent)_30%,transparent)] bg-accent-soft text-accent',
  success:
    'border-[color-mix(in_srgb,var(--success)_30%,transparent)] bg-success-soft text-success',
  warning:
    'border-[color-mix(in_srgb,var(--warning)_30%,transparent)] bg-warning-soft text-warning',
  danger: 'border-[color-mix(in_srgb,var(--danger)_30%,transparent)] bg-danger-soft text-danger',
};

export function Badge({
  children,
  tone = 'neutral',
  dot = false,
}: {
  children: ReactNode;
  tone?: Tone;
  /** A leading status dot; pulses for in-progress states. */
  dot?: boolean | 'pulse';
}) {
  return (
    <span
      className={cx(
        // self-start: inside a flex column the default is stretch, and a tag
        // that spans the page reads as a broken banner.
        'inline-flex w-fit shrink-0 self-start items-center gap-1.5 rounded-full border px-2.5 py-0.5',
        'text-[12px] font-medium leading-5',
        BADGE[tone],
      )}
    >
      {dot !== false && (
        <span
          aria-hidden="true"
          className={cx('h-1.5 w-1.5 rounded-full bg-current', dot === 'pulse' && 'ticker')}
        />
      )}
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
  const surface = {
    neutral: 'border-border-default bg-bg-subtle',
    accent: 'border-[color-mix(in_srgb,var(--accent)_28%,transparent)] bg-accent-soft',
    success: 'border-[color-mix(in_srgb,var(--success)_28%,transparent)] bg-success-soft',
    warning: 'border-[color-mix(in_srgb,var(--warning)_28%,transparent)] bg-warning-soft',
    danger: 'border-[color-mix(in_srgb,var(--danger)_28%,transparent)] bg-danger-soft',
  }[tone];
  const ink = {
    neutral: 'text-fg-secondary',
    accent: 'text-accent',
    success: 'text-success',
    warning: 'text-warning',
    danger: 'text-danger',
  }[tone];
  const Icon =
    tone === 'success'
      ? IconCheckCircle
      : tone === 'danger' || tone === 'warning'
        ? IconAlert
        : IconInfo;

  return (
    <div
      // assertive for errors, polite otherwise: an error the user must act on
      // should interrupt, a status update should not.
      role={tone === 'danger' ? 'alert' : 'status'}
      className={cx('flex gap-3 rounded-[var(--radius-md)] border px-4 py-3', surface)}
    >
      <Icon size={18} className={cx('mt-0.5 shrink-0', ink)} />
      <div className="min-w-0 text-sm leading-relaxed">
        {title !== undefined && <p className={cx('mb-0.5 font-semibold', ink)}>{title}</p>}
        <div className="text-fg-secondary">{children}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Skeleton --

export function Skeleton({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className={cx('skeleton rounded-[var(--radius-sm)]', className)} />
  );
}

// ------------------------------------------------------------- SpeakerDot --

/** Speaker identity: the same hue the player and transcript use. Colour is
 *  never the sole identifier (NFR-A11Y-06) - it always sits beside a name. */
export function SpeakerDot({ index, size = 10 }: { index: number; size?: number }) {
  const slot = ((index - 1 + 4) % 4) + 1;
  return (
    <span
      aria-hidden="true"
      className="inline-block shrink-0 rounded-full ring-2 ring-bg-surface"
      style={{ width: size, height: size, background: `var(--spk-${String(slot)})` }}
    />
  );
}

// ------------------------------------------------------------------- Tabs --

/** A segmented tab bar. Real tabs semantics: one tablist, one selected tab. */
export function Tabs<T extends string>({
  value,
  onChange,
  items,
  label,
}: {
  value: T;
  onChange: (next: T) => void;
  items: ReadonlyArray<{ id: T; label: string; icon?: ReactNode; badge?: ReactNode }>;
  label: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="inline-flex gap-1 rounded-[var(--radius-md)] border border-border-subtle bg-bg-inset p-1"
    >
      {items.map((item) => {
        const on = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => {
              onChange(item.id);
            }}
            className={cx(
              'inline-flex h-8 items-center gap-2 rounded-[var(--radius-sm)] px-3.5 text-[13px] font-medium transition-[background-color,color,box-shadow] duration-150',
              on
                ? 'bg-bg-surface text-fg-primary shadow-sm'
                : 'text-fg-muted hover:text-fg-primary',
            )}
          >
            {item.icon}
            {item.label}
            {item.badge}
          </button>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------- EmptyState --

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-[var(--radius-lg)] border border-dashed border-border-default bg-bg-subtle px-6 py-14 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-[var(--radius-md)] border border-border-subtle bg-bg-surface text-fg-secondary shadow-sm">
        {icon}
      </span>
      <h3 className="text-base font-semibold">{title}</h3>
      {children !== undefined && <p className="max-w-sm text-sm text-fg-muted">{children}</p>}
      {action !== undefined && <div className="mt-2">{action}</div>}
    </div>
  );
}

// ----------------------------------------------------------- SectionHeader --

export function SectionHeader({
  eyebrow,
  title,
  children,
  align = 'left',
}: {
  eyebrow?: string;
  title: ReactNode;
  children?: ReactNode;
  align?: 'left' | 'center';
}) {
  return (
    <div
      className={cx(
        'flex max-w-2xl flex-col gap-3',
        align === 'center' && 'mx-auto items-center text-center',
      )}
    >
      {eyebrow !== undefined && (
        <span className="inline-flex items-center gap-2 text-[13px] font-semibold text-accent">
          {eyebrow}
        </span>
      )}
      <h2 className="display text-3xl sm:text-[40px]">{title}</h2>
      {children !== undefined && (
        <p className="text-[17px] leading-relaxed text-fg-secondary">{children}</p>
      )}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-[5px] border border-border-default bg-bg-subtle px-1.5 py-0.5 font-mono text-[11px] text-fg-secondary">
      {children}
    </kbd>
  );
}
