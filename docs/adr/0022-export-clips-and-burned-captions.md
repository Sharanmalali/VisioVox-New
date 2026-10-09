# ADR-0022: Export clips and burned-in captions

- **Status:** accepted
- **Date:** 2026-10-09

## Context

People share moments, not hour-long recordings, and video shared to feeds
autoplays muted - captions that are not in the picture are not seen. Building
this also exposed that the export path had never run end to end in
production: no export had ever been requested, and three faults would each
have stopped one.

## Decision

An export may be a **clip** (`start_ms`, `end_ms`; at least 0.5 s, inside the
recording) and may carry **burned-in captions** (`captions`: `"original"` or
the code of a ready translation, ADR-0020). Both are part of the export's
identity, so the same request returns the same file.

- Captions are written as ASS (`ml/pipeline/s10_captions.py`) and drawn by
  ffmpeg's libass, which shapes Indian scripts with HarfBuzz; Noto fonts
  (`fonts-noto-core`) cover them. White text on a translucent box sized to the
  frame; with more than one speaker each line leads with the name in that
  speaker's colour, and overlapping lines stack.
- A single-speaker export carries that speaker's captions only, matching its
  audio; the original mix carries everyone's.
- Clips or captions mean re-encoding (libx264, CRF 20): a copied stream cuts
  only at keyframes and cannot be drawn on. The whole recording with no
  captions is still a remux.
- In the page: the Download panel has "Whole recording / A clip" with editable
  times and "Now" buttons, and every transcript line has a clip button - a
  second press on a later line stretches the clip.

Faults fixed on the way:

1. The worker read `projects/<id>/source.mp4`, which packaging never writes:
   every video export would have come out as audio. It reads S9's `video.mp4`.
2. The rendered file stayed on local disk, but the download link presigned
   an object-store key that did not exist. It is now uploaded to
   `exports/<project>/<file>`.
3. Download was a plain link to an API route that requires the bearer token,
   which a browser does not send on navigation: 401. The page now fetches
   `GET .../exports/{id}/link` (with the token) and navigates to the signed
   URL; Share uses the same link.

## Consequences

- Clips and captioned exports cost an encode (seconds for a clip, minutes for
  an hour at 720p) on the shared GPU-queue worker.
- Burned-in captions inherit the captions' errors, and cannot be turned off
  in the downloaded file - which is the point of them.
