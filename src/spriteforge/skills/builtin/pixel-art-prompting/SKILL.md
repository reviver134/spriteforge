---
name: pixel-art-prompting
description: Prompt Muse Image for readable chunky sprites matching an approved palette and style anchor.
---
Use the approved style.json and anchor. Request one centered full-body subject on
a flat solid magenta field, with chunky pixel clusters, consistent top-left light,
and no text, watermark, gradients or anti-aliasing. A 48x48 character canvas should
contain about 40x40 logical pixels of artwork. Requested provider dimensions are
not guaranteed: process the decoded image and inspect the detected grid and logical
size. Preserve accepted assets; use an edit reference for approved pose changes.
Inspect at native size and nearest-neighbor enlargement. Report palette, outline,
background-spill and silhouette failures instead of hiding them in the prompt.
