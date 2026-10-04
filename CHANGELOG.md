# Changelog

## 0.9.0

- All animations share a frame timer with drawing-time compensation.
- Frequent progress/hover updates continue from the visible value instead of snapping an interrupted animation to its old target.
- Quicker progress easing and gentle page transitions retain the Reduce motion option.
- Source scans show activity; queues summarize total source size; clearing the queue clears stale progress and time estimates.
- README highlights the three main features, with Windows executable and Python source included in the public package.

## 0.8.3

- Current-file and whole-queue time remaining appear below conversion status.
- Estimates update from observed speed; queue estimates weight pending work by source file size and omit existing outputs.
- Multi-pass progress accounts for every pass. CPU retries reset their speed estimate.
- Original-quality copies also show time remaining; finalizing, transferring and cancellation use clear status text.

## 0.8.2

- Custom bitrate immediately reveals a kbps number field, just like Custom quality reveals RF/CQ.
- Custom bitrate stays editable even when its number matches a Basic/High preset.
- Loading a built-in preset returns to its standard choice; saved custom bitrate profiles remain editable.

## 0.8.1

- Replaced the long main preset list with Format / resolution, then Bitrate / quality.
- Relevant Basic/High bitrate choices per resolution, plus direct custom bitrate and RF/CQ entry.
- Automatic source-based settings and lossless Original MKV remain available.
- Removed the separate DVD shortcut button; DVD 480p remains in the format menu.
- Saved profiles and audio/encoder controls remain in advanced settings.
- Added interaction checks that verify selected values reach the actual encoding settings.

## 0.8.0

- Automatic per-codec GPU selection for NVIDIA, AMD, Intel and Apple, with CPU fallback.
- Safe one-time CPU retry for GPU startup failure; failed encoders avoided for the rest of the batch.
- Responsive CPU mode, encoder availability check and explicit engine selection.
- One DVD 480p profile; removed DVD 576p and redundant DVD choices, with legacy migration.
- Clear source-size/output-profile labels and runtime encoder details in the queue.
- First-run tool setup, native OS paths/fonts/file pickers/folder opening, smaller-screen sizing and Linux/macOS scrolling.
- Exportable troubleshooting logs and startup-error reporting.
- Clean MIT source package, app icons, README, unit tests, build script and GitHub Actions builds.
- Existing year lookup, extras, surround audio, original quality, CD ripping, portable export, embedded menus, themes and smooth animations preserved.
