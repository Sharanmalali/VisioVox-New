'use client';

/**
 * Create and revoke share links (docs/28 §W7, §D3).
 *
 * The WhatsApp button is a `wa.me` link, which is a real, documented deep link.
 * There is no Instagram button, because a web app cannot post to Instagram —
 * the Graph API needs a business account and app review and does not accept
 * third-party uploads. On mobile the native share sheet offers Instagram
 * anyway, which is the honest route to it.
 */

import { useCallback, useEffect, useState } from 'react';

import { IconArrowUpRight, IconCheck, IconLink } from '@/components/icons';
import { Alert, Badge, Button, Select, Slab, SlabHead } from '@/components/ui';
import { api } from '@/lib/store';
import type { ShareResponse } from '@visiovox/ts-client';

export function SharePanel({
  projectId,
  projectTitle,
  speakers,
}: {
  projectId: string;
  projectTitle: string;
  speakers: { ordinal: number; label: string }[];
}) {
  const [shares, setShares] = useState<ShareResponse[]>([]);
  const [speaker, setSpeaker] = useState<string>('all');
  const [fresh, setFresh] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    const list = await api().listShares(projectId);
    setShares(list.items);
  }, [projectId]);

  useEffect(() => {
    void refresh().catch(() => {
      setError('Could not load existing links.');
    });
  }, [refresh]);

  function linkFor(token: string): string {
    return `${window.location.origin}/s/${token}`;
  }

  async function create() {
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const row = await api().createShare(projectId, {
        speaker_ordinal: speaker === 'all' ? null : Number(speaker),
        password: null,
        expires_in_days: 30,
      });
      // Shown once: only the hash is stored, so it cannot be recovered later.
      if (row.token) setFresh(linkFor(row.token));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the link');
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    setError(null);
    try {
      await api().revokeShare(projectId, id);
      setFresh(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not revoke the link');
    }
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setError('Could not reach the clipboard. Select the link and copy it.');
    }
  }

  const live = shares.filter((s) => s.revoked_at === null);

  return (
    <Slab>
      <SlabHead
        icon={<IconLink size={17} />}
        label="Share"
        description="Anyone with the link can watch — no account needed."
      />
      <div className="flex flex-col gap-5 p-5 sm:p-6">
        <Select
          label="Who to share"
          value={speaker}
          onChange={(e) => {
            setSpeaker(e.target.value);
          }}
        >
          <option value="all">Everyone (all speakers)</option>
          {speakers.map((s) => (
            <option key={s.ordinal} value={String(s.ordinal)}>
              {s.label} only
            </option>
          ))}
        </Select>
        <Button onClick={() => void create()} loading={busy} busyLabel="Creating…" full>
          <IconLink size={16} />
          Create link
        </Button>

        {error !== null && <Alert tone="danger">{error}</Alert>}

        {fresh !== null && (
          <div className="flex flex-col gap-3 rounded-[var(--radius-md)] border border-[color-mix(in_srgb,var(--success)_30%,transparent)] bg-success-soft p-4">
            <p className="text-sm font-semibold text-success">Link created</p>
            <div className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-border-default bg-bg-surface py-1.5 pl-3 pr-1.5">
              <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{fresh}</span>
              <Button
                size="sm"
                variant={copied ? 'secondary' : 'primary'}
                onClick={() => void copy(fresh)}
              >
                {copied ? <IconCheck size={14} /> : null}
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <a
                href={`https://wa.me/?text=${encodeURIComponent(`${projectTitle} — ${fresh}`)}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] border border-border-default bg-bg-surface px-3 text-[13px] font-medium shadow-xs transition-colors hover:border-border-strong"
              >
                Send on WhatsApp
                <IconArrowUpRight size={13} />
              </a>
              <span className="text-[12.5px] text-fg-secondary">
                Copy it now — only a hash is stored, so it cannot be shown again.
              </span>
            </div>
          </div>
        )}

        {live.length > 0 && (
          <div className="flex flex-col gap-2">
            <p className="text-[13px] font-medium text-fg-secondary">Active links</p>
            <ul className="flex flex-col divide-y divide-border-subtle rounded-[var(--radius-md)] border border-border-subtle">
              {live.map((row) => (
                <li key={row.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                  <span className="font-medium">
                    {row.speaker_ordinal !== null
                      ? `Speaker ${String(row.speaker_ordinal)}`
                      : 'Everyone'}
                  </span>
                  <Badge>
                    {row.access_count} view{row.access_count === 1 ? '' : 's'}
                  </Badge>
                  {row.expires_at !== null && (
                    <span className="text-[12.5px] text-fg-muted">
                      Expires {new Date(row.expires_at).toLocaleDateString()}
                    </span>
                  )}
                  <Button
                    size="sm"
                    variant="danger"
                    className="ml-auto"
                    onClick={() => void revoke(row.id)}
                  >
                    Revoke
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <p className="text-[12.5px] leading-relaxed text-fg-muted">
          Revoking takes effect immediately and cannot be undone. Shared pages are never indexed by
          search engines.
        </p>
      </div>
    </Slab>
  );
}
