---
name: sprite-animation-frames
description: Plan consistent pixel sprite animation frames, registration points, timing and atlas keys.
---
Keep canvas, palette, light direction and foot pivot fixed across frames. Plan key
poses before in-betweens: contact, passing, recoil and anticipation must be readable
at native resolution. Reuse the accepted character as the reference for pose edits.
Inspect the animation loop for foot sliding, silhouette jitter, accidental size
changes and duplicate holds. Specify durations and stable keys such as hero/walk/0.
Validate atlas metadata against actual packed keys. A contact sheet supplements,
but does not replace, playback. Never regenerate unrelated accepted frames.
