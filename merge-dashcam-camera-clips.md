---
name: merge-dashcam-camera-clips
description: Merge segmented multi-camera dashcam footage (Xforce, Vietmap, and similar brands) into one continuous video per camera. Use when the user has a folder of short dashcam video segments (e.g. 1-minute clips) from front/rear/side cameras and wants them combined into a single timeline per camera, in the correct chronological order.
---

# Merge dashcam camera clips

## Trigger

User has a folder of dashcam video segments (typically 1 minute each) recorded by a
multi-channel dashcam (Xforce, Vietmap, 70mai, etc.) and wants the clips from each
camera (front / rear / interior) combined into one continuous video per camera, in
the correct time order. Also trigger if they ask to "merge", "combine", "stitch",
or "concat" dashcam/car camera footage.

## Steps

1. **Locate the folder.** If it's on the user's computer, request folder access via
   `device_request_folder_access` (or confirm it's already connected), then list its
   contents recursively.

2. **Identify the per-camera marker in filenames.** Dashcam filenames usually encode a
   timestamp, a sequence number, and a camera indicator, e.g.:
   - Xforce: `20260831152516_0015340F.MP4` — trailing `F`/`R` before `.MP4` = Front/Rear.
   - Vietmap and other brands may instead use `_F`/`_R`, `_A`/`_B`, `FRONT`/`REAR`,
     `CAM1`/`CAM2`, or a folder-per-camera layout. Inspect a handful of filenames first;
     if the pattern isn't obvious, ask the user which camera each suffix/folder maps to
     rather than guessing.
   - Group all files by camera using this marker.

3. **Determine chronological order per camera group.** Parse the embedded date/time
   and/or sequence number from the filename (don't just trust OS listing order).
   Zero-padded fixed-width timestamps sort correctly lexicographically — verify this
   holds by spot-checking the first and last few filenames. If the naming scheme has no
   reliable embedded order, fall back to file mtime, but flag this assumption to the user.

4. **Sanity-check codecs before stream-copy.** Run `ffprobe` on the first and last file
   of each camera group (codec_name, width, height, r_frame_rate for video;
   codec_name for audio). If codec/resolution is consistent within a group, a fast
   lossless `-c copy` concat is safe. If it changes mid-group (e.g. the user swapped
   camera settings mid-trip), warn the user that lossless concat isn't possible for the
   mismatched segments and re-encoding (slower, not lossless) would be required instead.

5. **Build a concat list per camera** with **absolute paths** (relative paths resolve
   against the list file's own directory, not the working directory — a common mistake):
   ```
   file '/absolute/path/clip1.mp4'
   file '/absolute/path/clip2.mp4'
   ```

6. **Run ffmpeg with stream copy** (fast, lossless, no re-encode):
   ```
   ffmpeg -y -f concat -safe 0 -i list.txt -c copy output.mp4
   ```

7. **Respect the remote-device shell's hard time cap when running via `device_bash`.**
   Each `device_bash` call is capped at roughly 120 seconds server-side regardless of the
   `timeout_ms` requested, AND backgrounding a process with `nohup ... & disown` does
   NOT survive past the call returning — the whole process tree is torn down when the
   call ends. This means:
   - Never rely on background/nohup jobs surviving between `device_bash` calls.
   - Before committing to one giant concat, estimate whether it will fit: empirically,
     stream-copy throughput on a mounted folder has run around 35-45MB/s in past runs,
     but varies by codec (HEVC/4K remux was noticeably slower than 1080p H.264 in one
     measured case: ~30-35MB/s vs ~44MB/s). As a rule of thumb, keep any single ffmpeg
     invocation's total input size under roughly 2.5-3GB to comfortably finish in one
     call; time a small group first if the total is much larger, and extrapolate.
   - If a camera's total footage is too large for one call, split its file list into N
     roughly-equal chunks (by file count) so each chunk's data volume fits comfortably,
     and produce multiple part files (e.g. `Front_part1.mp4`, `Front_part2.mp4`) rather
     than letting the job get killed mid-write. A killed/truncated ffmpeg run leaves a
     corrupt file (ffprobe reports "moov atom not found") — if you see that, don't retry
     the same full-size job; split it instead.
   - This constraint is specific to the sandboxed `device_bash`/remote-devices shell, not
     to a normal local terminal — if running directly in a full local shell without that
     wrapper, a single long-running ffmpeg call is fine.

8. **Verify every output** with `ffprobe -show_entries format=duration,size` — the
   duration should equal the sum of the source clips going into it. Don't tell the user
   a merge succeeded without this check.

9. **Never delete or overwrite source clips.** Write merged outputs into a new
   subfolder (e.g. `merged/`) beside the originals. If an earlier attempt produced a
   corrupt/incomplete file, don't delete it either unless the user explicitly says to —
   rename it clearly (e.g. prefix `INCOMPLETE_DO_NOT_USE_`) so it's obviously not a
   deliverable, and leave actual deletion to the user or to an explicit follow-up request.

10. **If the user wants this done via a cloud video editor (Descript, etc.) instead of
    local ffmpeg:** check network egress to the editor's upload/storage host first with a
    quick `curl -I` before building out the whole import plan — sandboxed environments
    are often blocked by org network policy from PUTing directly to third-party storage
    hosts (e.g. `storage.googleapis.com`), and inlining large binaries through MCP tools
    (e.g. base64 into a Drive upload) isn't viable for files over a few MB. If blocked,
    tell the user plainly and offer the choice: they manually import into the editor's
    app, you drive browser automation to upload (slow, fragile for many large files), or
    you fall back to a local ffmpeg merge with the same end result.

## Verification

- Every output file's `ffprobe` duration matches the expected sum of its source clips.
- No "moov atom not found" or similar corruption errors.
- Source clips are all still present and unmodified.
- Report to the user: final file list with sizes/durations, how many parts per camera
  (and why, if split), and the folder they're saved in.
