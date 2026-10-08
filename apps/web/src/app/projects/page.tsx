'use client';

/**
 * Projects: everything you have separated.
 *
 * A list, not a grid of thumbnails: the useful facts about a recording are its
 * name, whether it is ready, how many people it found and how long it is, and
 * a list lets the eye run down exactly those. The whole row is the link.
 */

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  IconChevronRight,
  IconClock,
  IconFileVideo,
  IconFolder,
  IconGlobe,
  IconPlus,
} from '@/components/icons';
import { AppPage, AppShell, PageHeader } from '@/components/shell/AppShell';
import { Alert, Badge, ButtonLink, EmptyState, Skeleton, SpeakerDot } from '@/components/ui';
import { clockDuration, languageName, relativeTime, statusMeta } from '@/lib/format';
import { api, useRequireSignIn } from '@/lib/store';
import type { ProjectResponse } from '@visiovox/ts-client';

function ProjectRow({ p }: { p: ProjectResponse }) {
  const status = statusMeta(p.status);
  // Before processing finishes the stored length is a placeholder, not the
  // probed one; show nothing rather than a wrong number.
  const duration = p.status === 'ready' ? clockDuration(p.duration_ms) : null;
  const language = languageName(p.language);
  return (
    <li>
      <Link
        href={`/projects/${p.id}`}
        className="group flex items-center gap-4 px-4 py-4 transition-colors hover:bg-bg-subtle sm:px-6"
      >
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-bg-inset text-fg-secondary ring-1 ring-border-subtle">
          <IconFileVideo size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-medium text-fg-primary">{p.title}</p>
          <p className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-fg-muted">
            <span>{relativeTime(p.created_at)}</span>
            {duration !== null && (
              <span className="inline-flex items-center gap-1">
                <IconClock size={13} />
                <span className="tabular">{duration}</span>
              </span>
            )}
            {language !== null && (
              <span className="inline-flex items-center gap-1">
                <IconGlobe size={13} />
                {language}
              </span>
            )}
          </p>
        </div>
        {p.speaker_count !== null && p.speaker_count > 0 && (
          <span className="hidden items-center gap-2 text-[13px] text-fg-secondary md:inline-flex">
            <span className="flex -space-x-1">
              {Array.from({ length: Math.min(p.speaker_count, 4) }, (_, i) => (
                <SpeakerDot key={i} index={i + 1} size={12} />
              ))}
            </span>
            {p.speaker_count} speaker{p.speaker_count === 1 ? '' : 's'}
          </span>
        )}
        <Badge tone={status.tone} dot={status.live ? 'pulse' : true}>
          {status.label}
        </Badge>
        <IconChevronRight
          size={18}
          className="shrink-0 text-fg-muted transition-transform group-hover:translate-x-0.5"
        />
      </Link>
    </li>
  );
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const authed = useRequireSignIn();

  const load = useCallback(async () => {
    try {
      const list = await api().listProjects(100);
      setProjects(list.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load your projects');
    }
  }, []);

  useEffect(() => {
    if (authed) void load();
  }, [load, authed]);

  const shown = useMemo(() => {
    if (projects === null) return null;
    const q = query.trim().toLowerCase();
    return q ? projects.filter((p) => p.title.toLowerCase().includes(q)) : projects;
  }, [projects, query]);

  const counts = useMemo(() => {
    const all = projects ?? [];
    return {
      total: all.length,
      ready: all.filter((p) => p.status === 'ready').length,
      working: all.filter((p) => statusMeta(p.status).live).length,
    };
  }, [projects]);

  return (
    <AppShell>
      <AppPage>
        <PageHeader
          title="Projects"
          description="Every recording you have separated, newest first."
          actions={
            <ButtonLink href="/upload">
              <IconPlus size={16} />
              New upload
            </ButtonLink>
          }
        />

        {error !== null && <Alert tone="danger">{error}</Alert>}

        {projects !== null && projects.length > 0 && (
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <dl className="flex gap-6 text-sm">
              <div className="flex items-baseline gap-1.5">
                <dd className="font-semibold tabular">{counts.total}</dd>
                <dt className="text-fg-muted">total</dt>
              </div>
              <div className="flex items-baseline gap-1.5">
                <dd className="font-semibold tabular text-success">{counts.ready}</dd>
                <dt className="text-fg-muted">ready</dt>
              </div>
              {counts.working > 0 && (
                <div className="flex items-baseline gap-1.5">
                  <dd className="font-semibold tabular text-accent">{counts.working}</dd>
                  <dt className="text-fg-muted">in progress</dt>
                </div>
              )}
            </dl>
            <label className="relative w-full sm:w-72">
              <span className="sr-only">Search projects</span>
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                strokeLinecap="round"
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.5-3.5" />
              </svg>
              <input
                type="search"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                }}
                placeholder="Search by name"
                className="h-10 w-full rounded-[var(--radius-sm)] border border-border-default bg-bg-surface pl-9 pr-3 text-sm shadow-xs placeholder:text-fg-muted focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-ring)] focus:outline-none"
              />
            </label>
          </div>
        )}

        {shown === null && error === null ? (
          <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface shadow-sm">
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="flex items-center gap-4 border-b border-border-subtle px-6 py-4 last:border-0"
              >
                <Skeleton className="h-11 w-11" />
                <div className="flex flex-1 flex-col gap-2">
                  <Skeleton className="h-4 w-1/3" />
                  <Skeleton className="h-3 w-1/5" />
                </div>
                <Skeleton className="h-6 w-20 rounded-full" />
              </div>
            ))}
          </div>
        ) : shown !== null && projects !== null && projects.length === 0 ? (
          <EmptyState
            icon={<IconFolder size={22} />}
            title="No projects yet"
            action={
              <ButtonLink href="/upload">
                <IconPlus size={16} />
                Upload your first recording
              </ButtonLink>
            }
          >
            Upload a video where people talk over each other, and each person will get their own
            track here.
          </EmptyState>
        ) : shown !== null && shown.length === 0 ? (
          <p className="rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface px-6 py-10 text-center text-sm text-fg-muted shadow-sm">
            No projects match “{query}”.
          </p>
        ) : shown !== null ? (
          <ul className="divide-y divide-border-subtle overflow-hidden rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface shadow-sm">
            {shown.map((p) => (
              <ProjectRow key={p.id} p={p} />
            ))}
          </ul>
        ) : null}
      </AppPage>
    </AppShell>
  );
}
