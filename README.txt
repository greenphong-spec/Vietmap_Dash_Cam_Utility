DashCam Merger 1.0.0
====================

What it does
------------
Your dashcam records in short clips - usually one file per minute, per camera.
DashCam Merger joins them back into ONE video per camera (Front, Rear,
Interior), in the right order, without re-encoding. A two-hour trip becomes two
files instead of two hundred.

It never deletes, moves, renames or changes your original clips. The merged
files are written into a new "merged" folder beside them. If anything goes
wrong, the half-written file is kept under a name starting with
INCOMPLETE_DO_NOT_USE_ so you can never mistake it for a finished video.

No internet connection is needed. No administrator rights are needed.


Using it (the 4-step way)
-------------------------
1. Double-click DashCamMerger.exe.
2. Click "Browse..." and pick the folder with your dashcam clips
   (for example the Movie or DCIM folder on the memory card).
3. Click "Scan". You will see a row per camera: how many files, how big,
   the first and last clip, and the video format. If the program cannot work
   out which camera is which, it asks you - pick Front, Rear, Interior or Skip
   for each group.
4. Click "Merge" and wait. Each camera gets its own progress bar with a speed
   and a time estimate. When it finishes, click "Open output folder".

Every finished file is checked before you are told it worked: its length must
match the total length of the clips that went into it. A file that fails the
check is never presented as a good result.


Using it from a command line
----------------------------
    DashCamMerger-cli.exe --input "D:\DCIM\Movie" --yes

Useful options:
    --output FOLDER        write somewhere else (default: <input>\merged)
    --dry-run              show the plan and the exact ffmpeg commands, write nothing
    --camera-map "F=Front,R=Rear"    say which suffix is which camera
    --split-on-gap         start a new file for each trip
    --max-part-gb 4        split outputs at 4 GB (handy for FAT32 USB sticks)
    --reencode             re-encode when clip formats differ (slow, not lossless)
    --overwrite            replace previous outputs (the old one is renamed, not deleted)
    --no-recursive         do not look in subfolders
    --parallel 2           run two merges at once
    --keep-lists           keep the ffmpeg concat lists next to the outputs
    --verbose              detailed logging
    --config PATH          use a different config.json

Exit codes: 0 all good, 1 something failed verification, 2 wrong usage or
missing ffmpeg, 3 no video files found, 130 cancelled.


Where things end up
-------------------
    <your folder>\merged\Front_20260831152516_to_20260831171203.mp4
    <your folder>\merged\Rear_20260831152516_to_20260831171203.mp4
    <your folder>\merged\merge_report.txt     what was merged, with the checks
    <program folder>\logs\merge_<date>.log    full technical log of the run

Logs older than 30 days are tidied up automatically. That is the only file the
program ever deletes.


Cameras it recognises automatically
-----------------------------------
    Xforce           20260831152516_0015340F.MP4   (F = front, R = rear)
    Suffix letter    clip_F.mp4, clip_R.mp4, clip_I.mp4
    Words            FRONT_001.mp4, REAR_001.mp4, CABIN_001.mp4
    CAM numbers      CAM1_001.mp4, CAM2_001.mp4, CAM3_001.mp4
    70mai            NO20260831-152516-000001F.MP4
    Folders          Normal\, Front\, Rear\, Interior\

For anything else, the program asks you which camera each group belongs to. You
can also teach it your own pattern permanently - open config.json (File menu ->
Open config.json) and add an entry to "camera_profiles":

    "camera_profiles": [
      {"name": "MyBrand", "regex": "_(?P<cam>CH[12])\\.mp4$",
       "map": {"CH1": "Front", "CH2": "Rear"}}
    ]

The regex must contain a group written (?P<cam>...) - that is the part that
says which camera the file belongs to.


If something goes wrong
-----------------------
"ffmpeg not found at ..."
    The ffmpeg folder must sit beside the exe with ffmpeg.exe and ffprobe.exe
    inside it. Unzip the whole download, do not move the exe out on its own.

A file named INCOMPLETE_DO_NOT_USE_... appeared
    That merge did not pass its check - most often the disk filled up, or you
    cancelled. Your original clips are untouched; just run it again. The
    incomplete file is yours to delete when you want.

"Order based on file modification time, please spot-check"
    The filenames carried no date and no sequence number, so the order came
    from the file dates. Have a quick look at the result before relying on it.

One camera produced several files (_part1, _part2)
    The clips in that group are not all the same format or resolution - they
    cannot be joined losslessly. Use "Re-encode if formats differ" if you
    really need a single file; it is much slower and not lossless.

Anything else
    Send the newest file from the logs folder - it contains every command that
    ran and what it said.


Bundled ffmpeg
--------------
This build ships ffmpeg and ffprobe 6.1.1 (gyan.dev essentials build).
Help -> About shows the exact version in your copy.
ffmpeg is free software; see https://ffmpeg.org for its source and licence.
