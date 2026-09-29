# Track W — decisions and standing rules

> **Read this before writing any Track W code.** It is the short answer to "why is it like this"
> and "what am I not allowed to change without asking".
>
> Part 1 is the **decision register** — what was decided, and what it means in practice.
> Part 2 is the **standing rules** that follow from those decisions.
> Part 3 is the **build log** — decisions taken *during* implementation, appended as they happen.
>
> Rationale lives in [`../28-product-delivery-plan.md`](../28-product-delivery-plan.md) §3.
> This file is the operative summary; that file is the argument.

---

## Part 1 — Decision register

| # | Decision | Status | What it means when you are writing code |
|---|---|---|---|
| **D1** | **Split the design direction.** Expressive 3D on landing/auth/upload/**waiting**; restrained studio instrument in the player/transcript/export | ✅ Settled | Before adding a visual flourish, ask which surface you are on. Glow in the player is a bug, not a style choice |
| **D2** | **No fixed upload cap.** The limit is computed from live disk, queue and measured throughput, and displayed to the user up front | ✅ Settled | Never write a size constant into the UI or a config as *the* limit. The 50 GB ceiling is a backstop, not the number users see |
| **D3** | **Sharing is Web Share API + `wa.me` + share links.** Direct posting to Instagram is impossible from a web app | ✅ Settled | Do not build a button that implies direct posting. Label what each control actually does |
| **D4** | **Postgres is the ledger, Redis is disposable.** Quotas, shares, exports, audit and upload sessions are tables | ✅ Settled | If losing Redis would lose correctness rather than speed, it is in the wrong store |
| **D5** | **Zero-cost self-hosted deployment.** The workstation is the server, exposed via a free tunnel. Nothing rented, no card | ✅ Settled | Never introduce a paid or card-required service. `docs/17` (managed + Kubernetes) is **aspirational and must not be followed literally** |
| **D6.1** | Tiering — plans may be unnecessary without billing | ⏳ Open | Do not wire plan-based branching until decided |
| **D6.2** | Retention — 30 days default; 7 recommended on a fixed disk | ⏳ Open | Keep retention a config value, not a literal |
| **D6.3** | Public signup vs invite-only | ⏳ Open | Keep the registration path swappable |

---

## Part 2 — Standing rules

### Inherited — these already bind, do not restate or weaken them
[`../../CLAUDE.md`](../../CLAUDE.md) holds the **8 project invariants** (identical sample counts,
integer-millisecond timing, ephemeral embeddings, server-side ownership checks, sandboxed ffmpeg,
Faithful-track transcription, idempotent stages, partial results) and the Python/TypeScript/ML
conventions. Track W does not get exemptions from any of them.

Two are load-bearing for this track specifically:
- **Invariant 4 — every artifact access is ownership-checked server-side.** IDOR is the highest-impact
  vulnerability in this product. Share links (W7) are the obvious place to get this wrong.
- **Invariant 5 — ffmpeg never runs outside the sandbox, never with credentials.** The upload probe
  (W2) and the export render (W6) are both ffmpeg on user-supplied bytes.

### Track W additions

**Money**
- No paid services, no managed databases, no rented GPU, no "free tier that later bills". If a
  hosted thing is genuinely needed, find the no-card option or self-host it. (D5)

**Disk**
- **Never call `df /` or `statvfs("/")` to decide anything.** Inside this distro it reports the
  vhdx's virtual ceiling, not real free space. Measure the configured media volume. (W0)
- Any code path that writes media must reserve headroom first and release it on
  completion/abort/timeout.

**Limits**
- Admit jobs on **probed duration × speaker count**, never on client-declared metadata, never on
  bytes alone. (D2)
- Every user-facing limit is computed and explained. "Too large" without a reason or a remedy is
  not an acceptable error message.

**Estimates**
- **An ETA that was not measured is not shipped.** Until the pipeline is benchmarked (W8), show a
  range or show nothing. Never a fake countdown, never a bar that parks at 99%. (D2)

**Truthfulness in the UI**
- The interface does not claim capabilities the product lacks — no fake Instagram posting (D3), no
  invented confidence, no progress that is not real progress. This is the same principle as
  `docs/14` §1.3 ("uncertainty is visible") applied to the product surface.

**Process**
- A task is ticked in a phase file only when its **verification command has been run and passed**.
- Every measurement is recorded with its date and the command that produced it.
- If implementation contradicts the plan, **fix `28-product-delivery-plan.md` in the same commit**.
  Two documents disagreeing is worse than one being wrong.

---

## Part 3 — Build log

Decisions taken during implementation that Part 1 did not anticipate. Append; do not rewrite.

### 2026-09-16 — W0 · storage route

- **Media lives on a dedicated ext4 vhdx on `E:`, mounted at `/srv/media`.** Native ext4 on NVMe,
  full speed. Costs a one-time elevated setup and a logon task, because `wsl --mount` needs
  Administrator and does not survive a reboot.
- **🚫 The datasets stay. Do not delete `~/data/Libri2Mix`, `~/data/Libri3Mix` or
  `~/data/voxceleb2`.** More training is planned (the `c4` attempt of 2026-09-13 may be retried),
  and VoxCeleb2 may be unrecoverable because its credentials are still outstanding. This removes
  160 GB of easy reclaim from the table — deliberately. **Find space elsewhere; do not revisit
  this.**
- **`D:` is therefore rescued without deleting anything**, via `fstrim` plus
  `wsl --manage VisioVox --set-sparse true`. The vhdx file is 360.6 GB while the filesystem holds
  281 GB, so roughly 80 GB is dead slack that a dynamic vhdx never returns on its own. This needs
  the distro stopped, so it is scheduled rather than done opportunistically.

### 2026-09-16 — W0 · implementation
- **The headroom check measures the configured media path, not `/`.** `statvfs("/")` reports the
  vhdx's virtual ceiling here and is useless. The check resolves the configured media directory and
  **refuses to run if that directory shares a filesystem with `/`**, because that silently
  reintroduces the original bug rather than failing loudly.
- **`docs/17-infrastructure-deployment.md` is now aspirational.** It predates the zero-cost
  constraint and specifies managed services and Kubernetes. W9 supersedes it. Flagged in the
  document itself rather than deleted, because its threat model and topology reasoning are still
  sound.

### 2026-09-23 — identity moved to Firebase (supersedes W4)

Asked for directly, after the auth UI was already built and working. The
self-hosted auth backend is **gone**, not wrapped: no `/v1/auth/*` routes, no
`sessions` or `refresh_tokens` tables, no access-token minting, no refresh
cookie. `security.py` survives only as Argon2 password hashing for share links.

- **What this buys.** Google and GitHub sign-in, password reset and email
  verification stop being ours to build, host and get right. Reset and
  verification email delivery in particular were unbuilt and had no free path.
- **What it costs, recorded in ADR-0016 rather than discovered later.**
  Refresh-token reuse detection is no longer a property we store and can prove;
  deleting a person now takes two systems, and neither alone is complete;
  identity is a third-party directory we do not control.
- **`users.firebase_uid` is nullable on purpose.** Rows predating the migration
  are adopted by email on first sign-in, so existing projects keep their owner
  instead of being orphaned behind a new row.
- **Token persistence is `browserSessionPersistence`.** Firebase defaults to
  IndexedDB, which survives tab close and is readable by any script on the
  origin — the same exposure the W4 httpOnly-cookie work existed to avoid. This
  is the closest equivalent Firebase offers; it is weaker, and that is the
  trade.
- **`FIREBASE_CHECK_REVOKED=true`.** Costs a round trip per request. Without it
  "sign out everywhere" is advisory for up to an hour, which is not what the
  phrase means to a user.

Tests: `tests/test_api_auth.py` deleted; `tests/test_firebase_identity.py`
replaces it, covering the part that is still ours — the mapping from verified
claims to a local row. `tests/authstub.py` signs requests by patching
verification at the boundary.

**Caution learned here:** `sign_in()` first resolved its identity *inside* the
lambda, so every request in a test arrived as a different person. It surfaced as
a 404 on a project the test had just created — nowhere near the cause. Resolve
once, before the closure.

### 2026-09-28 — W9 · first real deployment: visiovox.dpdns.org

W9 was marked done on "builds, starts and serves", but the first deploy to a
real hostname found three gaps that no local run exercises. Each would have
produced a healthy-looking stack that failed on the first upload.

- **API calls had no route.** The web bundle calls `NEXT_PUBLIC_API_URL`, which
  is the public hostname, and the tunnel pointed at `web:3000` only. Fixed with
  an **edge proxy** (Caddy, `infra/docker/Caddyfile`): `/v1/*` → API,
  `/visiovox-media/*` → MinIO, rest → web. The tunnel now targets `edge:8080`.
  Chosen over path rules in the Cloudflare dashboard because it lives in the
  repo, is reviewable, and `--local` exercises the same routing.
- **Presigned URLs pointed at `minio:9000`.** Unreachable from a browser, and
  SigV4 signs the Host header, so they cannot be rewritten afterwards. New
  setting `S3_PUBLIC_ENDPOINT_URL`: the API signs browser-facing URLs for the
  public origin; server-side calls keep the internal endpoint. Verified end to
  end: a 1 MiB multipart PUT and a signed GET through the edge succeed, and the
  same URL under another Host is refused (403).
- **The host GPU worker could not reach the production data services** - all
  were `expose:` only, so it would have fallen back to `localhost:6379` and
  joined the *development* queue. They are now on loopback-only ports
  (15432/16379/19000), and `deploy.sh` derives `.env.worker` every run.

Also: production config moved to **`.env.production`** (the dev tools read
`.env`); production data moved to `/srv/media/prod-data/` (the dev MinIO
already owned `/srv/media/minio`, and two servers on one data directory corrupt
it); a `minio-init` one-shot creates the private bucket; the tunnel token lives
only in `secrets/cloudflare-tunnel-token.txt` and reaches cloudflared as
`TUNNEL_TOKEN`; `deploy.sh`'s disk check measures the drive behind the sparse
media image (it read 196 GB free on a drive with 43 GB).

**Domain:** DigitalPlat FreeDomain, `visiovox.dpdns.org` - free with no
renewal fee, unlike the Student Pack `.me` (one year). Renewal window opens
~2027-05-31. **Known issue:** BMSCE's FortiGuard filter blocks the new domain on
campus; it is reachable from everywhere else (verified from DE and PT).

### 2026-09-29 — serving extractor is now AV-MossFormer2; three production bugs the first real upload found

**Extractor.** Asked for directly: the serving model is now AV-MossFormer2
(ClearerVoice-Studio, `alibabasglab/AV_MossFormer2_TSE_16K`), replacing the
trained SEAVE checkpoint c3. It is conditioned on each speaker's **lips**, so
a speaker is now a face track rather than a diarized voice, and the stages
that only existed to feed c3 a voice reference (S2a diarization, S3 binding,
S4 enrolment) are gone from the serving path. The pipeline is S0 ingest ->
S5 extract (runner) -> S3 fuse -> S7 captions -> S9 package.

- **Own interpreter.** It runs as a subprocess under ClearerVoice's venv
  (`services/worker-gpu/av_tse/run_av_tse.py`, `AV_TSE_PYTHON`): its
  dependency set is unrelated to ours, and a separate process returns its GPU
  memory before Whisper loads. `HF_HUB_OFFLINE=1` - a job never waits on the hub.
- **Timeline.** Each output covers only its face track's frames. The runner
  takes S0's analysis wav as its soundtrack (S0's video has none), so the
  tracks land on the timeline every later stage uses; a cross-correlation
  check measures any residual offset (0 samples in practice, 69 ms when the
  audio came out of an AAC mp4).
- **Faces into people.** Silent faces (listeners) are dropped (-30 dB against
  the mixture over the same span); time-disjoint tracks whose ECAPA voices
  match (cosine > 0.6) are merged, so a camera cut does not split a person.
  Speakers are numbered left to right.
- **Consequences, stated.** An upload with no video, or no visible face, is
  refused with a message saying why; there is no audio-only route. The model
  has no confidence output, so `mean_confidence` is the share of the recording
  the face was visible - the honest ceiling on how complete the track is.
  Overlap ratio is measured on the separated tracks, so no diarizer is needed.
- **Not yet shown:** that it beats c3 on the product condition. Measured so
  far: +9.43 dB SI-SDRi on three VoxCeleb2 side-by-side mixtures at 0 dB -
  VoxCeleb2 is its training domain. A same-clip comparison against c3 and an
  AMI test are the open items.

**Three production bugs**, all invisible in development because the workspace
installs every package into one environment and nothing there runs as another uid:

1. The API container ran as uid 10001; the Firebase key is mode 600 for the
   host user. Every signed-in request answered 500 - while anonymous ones
   answered 401, which is what the first deploy checked. The API and migrate
   containers now run as the host uid, which also lets the API write the media
   volume it shares with the host worker. `deploy.sh` now checks both from
   inside the container.
2. The API imported `worker_gpu.tasks` to enqueue and `worker_cpu.tasks` for
   progress; the API image contains neither. Every upload was accepted and
   never ran, and every progress stream crashed. `visiovox_api.jobqueue` now
   sends by task name and reads progress from Redis directly.
3. Exports imported `pipeline.s10_render` for the quality ladder. The API
   keeps its own copy (`visiovox_api.renditions`).

`tests/test_api_packaging.py` now fails if the API imports any worker or ML
package, if task names drift from the workers', or if the two ladders disagree.

**Also:** uvicorn's access log wrote the progress stream's `?access_token=`
in full, contradicting the client's own comment. `visiovox_api.logredact`
redacts it.

**Found by the first end-to-end run through the browser** (sign in -> upload ->
process -> play -> captions, Playwright against the production stack), after
the three above - none reachable by any test that stops at the API:

4. **GPU silently not used under systemd.** ClearVoice picks its GPU via
   `nvidia-smi`, which under WSL is in `/usr/lib/wsl/lib` - not on a unit's
   PATH - and it then falls back to the CPU while its face detector stays on
   CUDA, dying on a device mismatch. The worker adds that directory to the
   runner's PATH, and the runner moves the model to CUDA when CUDA exists.
5. **The real pipeline's manifest never played.** The player requires
   `manifest.mixed` (the "Original mix" track) and plays video only from
   `manifest.video`; S9 produced neither - only the mock did, so the player had
   only ever met mock output. S9 now takes the mixture and the worker ships
   S0's re-encode as `video.mp4`. S0 now encodes it `yuv420p` + faststart.
6. **Captions were written in a shape the player ignores.** S9 wrote the
   transcript JSON as a bare list; `parseTranscript` reads `{segments: ...}`
   and returns nothing otherwise. Transcribed, shipped, fetched, never shown.
7. **`project.height` was never set**, so every project offered audio-only
   downloads. Set from the manifest's video when a job finishes.

Verified end to end on 2026-09-29: a 33 s two-speaker video (faces appear at
3.5 s) processed in ~70 s; both speakers play in sync with word-highlighted
captions of their own speech; every artifact loads through presigned URLs.

**Open, found on the way, not fixed:**
- **Deletion deletes nothing.** `DELETE /v1/auth/me` and project deletion set
  `deleted_at` and return "deletion_scheduled", but no job ever purges the
  rows, the uploads or the results - the docstring's "cascades" does not
  happen. Stored faces and voices of a deleted account remain indefinitely.
- **HLS is not wired for the real pipeline.** S9 writes `hls.master`; the
  player starts HLS only on `master_playlist` and wants per-track `hls`
  entries. Over 10 minutes the player falls back to Web Audio, which works
  but holds every track in memory.
- The failure view shows raw stage names and "Processing - N%" beside a
  `failed` badge instead of the pipeline's human-readable message.
- Tested from the campus network through a local TLS route onto the same edge
  proxy, because BMSCE's FortiGuard filter intercepts the public hostname;
  the Cloudflare hop itself is verified by external checks.
