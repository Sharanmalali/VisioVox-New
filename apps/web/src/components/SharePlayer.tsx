'use client';

/**
 * Client boundary for the player on a public share page.
 *
 * The page itself is a Server Component so its Open Graph tags are real HTML;
 * the player needs Web Audio and the DOM, so it lives behind this boundary.
 */

import { InsightsPanel, insightBlocks, insightsFor } from '@/components/InsightsPanel';
import { Player } from '@/components/Player';
import { TranscriptPanel } from '@/components/TranscriptPanel';
import type { Manifest } from '@/lib/playback/manifest';

export function SharePlayer({ manifest, title }: { manifest: unknown; title: string }) {
  // The shape is validated by the player's own manifest parsing; a share that
  // arrives malformed should show the page and fail on playback rather than
  // blanking the whole route. Names arrive already applied to the manifest's
  // labels, and a recipient reads them but cannot change them.
  const parsed = manifest as Manifest;
  return (
    <Player
      manifest={parsed}
      aside={(context) => (
        <TranscriptPanel
          context={context}
          title={title}
          docxPreamble={() =>
            insightBlocks(
              insightsFor(
                context.lines,
                context.speakers.map((s) => s.ordinal),
                parsed.duration_ms,
                context.activity,
              ),
              context.nameOf,
            )
          }
        />
      )}
      below={(context) => <InsightsPanel context={context} durationMs={parsed.duration_ms} />}
    />
  );
}
