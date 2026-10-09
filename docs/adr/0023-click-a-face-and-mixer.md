# ADR-0023: Click a face to listen, and the listener's own mix

- **Status:** accepted
- **Date:** 2026-10-10

## Context

Selecting a speaker from a list of cards asks the listener to map "Speaker 2"
to a face themselves. And a single selected voice is not always what a
listener wants: often it is everyone, with one person quieter or louder.

## Decision

**Click a face.** S5 already tracks every face frame by frame; its runner now
keeps each track's box every sixth frame (about 4 per second) as fractions of
the frame. A person's boxes (all their merged tracks, on the 25 fps video
clock) ride in their caption JSON as `faces.boxes` - like `activity`, because
the manifest schema is frozen. The player draws a clickable region over each
face on screen at the current time (nearest sample within 400 ms), mapped
through the video's letterboxing, grown to take in the head, and kept clear of
the native control bar. Clicking selects that person's separated track; the
person being heard carries a ring and their name.

**Mix.** The WebAudio engine already plays every track at once and switches by
gain. `setMix(levels)` sets each track's gain (0-2, gliding over ~30 ms);
selecting a single person ends the mix. The mixer offers every separated voice
and the original mix (the room), with an "Only" button per voice. HLS cannot
mix (one element, one rendition) and does not offer it.

**Export the mix.** An export may carry `mix` (ordinal -> level, 0 = the
original mix), stored as a canonical "0:0,1:100,2:30" so the same mix is the
same file. The worker sums the voices at their levels with ffmpeg `amix`
(no normalisation) followed by a limiter at 0.95, then renders as any export
(clip and burned-in captions included; captions are those of the audible
voices).

## Consequences

- Projects processed before this have no boxes: their faces are not
  clickable, and everything else works as before.
- A face that is detected but never spoke was dropped in S3 and has no region.
- Boxes add a few hundred kB per speaker per hour to the caption file.
