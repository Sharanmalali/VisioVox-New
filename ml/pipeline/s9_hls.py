"""HLS packaging for long projects (docs/12 §4).

The player has two engines. `WebAudioSyncEngine` fetches whole tracks and puts
them on one AudioContext clock, which is sample-accurate and is what makes
switching speakers feel instant. It also means downloading and decoding
every track up front, and the memory that takes is duration times the number
of tracks: past about 50 track-minutes (ten minutes of four speakers and the
original mix) it stops being reasonable.

Past that point `HlsSyncEngine` takes over: the browser streams and handles A/V
sync natively, at the cost of a small gap when switching. The manifest's
`playback_hint` says which engine to use, and that decision is made server-side
so duration and track-count thresholds are not duplicated in the client.

This module produces the HLS side: the picture as a video playlist, one audio
rendition per speaker track plus the original mix, one subtitle rendition per
speaker, and a master playlist tying them together.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SEGMENT_SECONDS = 6
# Thresholds from docs/12 §2. Kept here rather than in the client so the
# decision is made once, server-side.
WEBAUDIO_MAX_DURATION_MS = 10 * 60 * 1000
WEBAUDIO_MAX_TRACKS = 4
# The budget those two set: ten minutes of four speakers and the original mix,
# all decoded at once. Duration and track count trade against each other: a
# one-minute clip with eight people fits; an hour of one person does not.
WEBAUDIO_MAX_TRACK_MS = WEBAUDIO_MAX_DURATION_MS * (WEBAUDIO_MAX_TRACKS + 1)


class HlsError(RuntimeError):
    """ffmpeg could not produce the HLS rendition."""


@dataclass(frozen=True)
class AudioRendition:
    name: str  # shown in the player's track picker
    group_id: str
    playlist: str  # relative URI
    default: bool = False
    autoselect: bool = False


@dataclass(frozen=True)
class SubtitleRendition:
    name: str
    group_id: str
    playlist: str
    language: str = "en"


def choose_engine(duration_ms: int, n_tracks: int) -> str:
    """Which playback engine the client should use.

    `n_tracks` counts everything the browser would decode: every speaker and
    the original mix. Web Audio buys sample-accurate switching; HLS buys not
    downloading everything first. The crossover is total memory, which is
    duration multiplied by track count - not either one alone (a one-minute
    clip with five people was once sent to HLS for having five tracks).
    """
    if duration_ms * max(n_tracks, 1) > WEBAUDIO_MAX_TRACK_MS:
        return "hls"
    return "webaudio"


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise HlsError("ffmpeg not on PATH")
    return path


def segment_audio(source: Path, out_dir: Path, name: str) -> str:
    """Segment one audio track into HLS. Returns the playlist filename.

    Segments are re-encoded to AAC rather than copied, because segment
    boundaries have to land on frame boundaries for a switch to be gapless and
    a stream-copy inherits whatever boundaries the source happened to have.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    playlist = f"{name}.m3u8"
    proc = subprocess.run(  # noqa: S603 - resolved path, fixed argv
        [
            _ffmpeg(),
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "1",
            "-f",
            "hls",
            "-hls_time",
            str(SEGMENT_SECONDS),
            "-hls_playlist_type",
            "vod",
            "-hls_segment_filename",
            str(out_dir / f"{name}_%03d.ts"),
            str(out_dir / playlist),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not (out_dir / playlist).exists():
        raise HlsError(f"segmenting {source.name} failed: {proc.stderr.strip()[:200]}")
    return playlist


def segment_video(source: Path, out_dir: Path, name: str = "v") -> str:
    """Segment the picture into HLS without re-encoding. Returns the playlist name.

    The packaged video.mp4 has no soundtrack (the speakers are the audio), so
    this is the variant stream; the audio renditions ride beside it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    playlist = f"{name}.m3u8"
    proc = subprocess.run(  # noqa: S603 - resolved path, fixed argv
        [
            _ffmpeg(),
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-an",
            "-f",
            "hls",
            "-hls_time",
            str(SEGMENT_SECONDS),
            "-hls_playlist_type",
            "vod",
            "-hls_segment_filename",
            str(out_dir / f"{name}_%03d.ts"),
            str(out_dir / playlist),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not (out_dir / playlist).exists():
        raise HlsError(f"segmenting {source.name} failed: {proc.stderr.strip()[:200]}")
    return playlist


def write_subtitle_playlist(vtt_name: str, out_dir: Path, name: str, duration_ms: int) -> str:
    """A single-segment subtitle playlist pointing at an existing WebVTT file.

    Captions are small enough that segmenting them buys nothing, and a
    one-segment playlist is valid HLS.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    playlist = f"{name}.m3u8"
    seconds = max(1, round(duration_ms / 1000))
    (out_dir / playlist).write_text(
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        f"#EXT-X-TARGETDURATION:{seconds}\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXT-X-PLAYLIST-TYPE:VOD\n"
        f"#EXTINF:{seconds}.0,\n"
        f"{vtt_name}\n"
        "#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    return playlist


def write_master_playlist(
    out_dir: Path,
    audio: list[AudioRendition],
    subtitles: list[SubtitleRendition],
    video_playlist: str | None = None,
    bandwidth: int = 1_500_000,
) -> Path:
    """The master playlist the player loads first."""
    if not audio:
        raise HlsError("a master playlist needs at least one audio rendition")

    lines = ["#EXTM3U", "#EXT-X-VERSION:6", ""]
    for a in audio:
        lines.append(
            f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="{a.group_id}",NAME="{a.name}",'
            f"DEFAULT={'YES' if a.default else 'NO'},"
            f"AUTOSELECT={'YES' if a.autoselect else 'NO'},"
            f'URI="{a.playlist}"'
        )
    for s in subtitles:
        lines.append(
            f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="{s.group_id}",NAME="{s.name}",'
            f'LANGUAGE="{s.language}",DEFAULT=NO,AUTOSELECT=NO,URI="{s.playlist}"'
        )

    lines.append("")
    group = audio[0].group_id
    tags = f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},AUDIO="{group}"'
    if subtitles:
        tags += f',SUBTITLES="{subtitles[0].group_id}"'
    lines.append(tags)
    # A video-less project still needs a variant line; pointing it at the
    # default audio rendition is how audio-only HLS is expressed.
    lines.append(video_playlist or audio[0].playlist)
    lines.append("")

    master = out_dir / "master.m3u8"
    out_dir.mkdir(parents=True, exist_ok=True)
    master.write_text("\n".join(lines), encoding="utf-8")
    return master


@dataclass
class HlsResult:
    master: Path
    audio: list[AudioRendition]
    subtitles: list[SubtitleRendition]
    segment_count: int


def package_hls(
    out_dir: Path,
    tracks: list[tuple[str, Path]],
    captions: list[tuple[str, str]] | None = None,
    duration_ms: int = 0,
    video_playlist: str | None = None,
    video: Path | None = None,
) -> HlsResult:
    """Build the full HLS tree.

    `tracks` is (display name, audio path), rendition `audio/a<i>.m3u8` in
    that order; `captions` is (display name, vtt filename) already written into
    `out_dir`; `video`, when given, is segmented into `video/v.m3u8` and
    becomes the variant stream - without it the player shows no picture.
    """
    if video is not None:
        video_playlist = f"video/{segment_video(video, out_dir / 'video')}"
    audio_dir = out_dir / "audio"
    renditions: list[AudioRendition] = []
    for i, (name, path) in enumerate(tracks):
        slug = f"a{i}"
        playlist = segment_audio(path, audio_dir, slug)
        renditions.append(
            AudioRendition(
                name=name,
                group_id="aud",
                playlist=f"audio/{playlist}",
                default=(i == 0),
                autoselect=(i == 0),
            )
        )

    subs: list[SubtitleRendition] = []
    if captions:
        subs_dir = out_dir / "subs"
        for i, (name, vtt) in enumerate(captions):
            src = out_dir / vtt
            if src.exists():
                subs_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, subs_dir / vtt)
            playlist = write_subtitle_playlist(vtt, subs_dir, f"s{i}", duration_ms)
            subs.append(SubtitleRendition(name=name, group_id="subs", playlist=f"subs/{playlist}"))

    master = write_master_playlist(out_dir, renditions, subs, video_playlist)
    segments = len(list(audio_dir.glob("*.ts")))
    return HlsResult(master=master, audio=renditions, subtitles=subs, segment_count=segments)
