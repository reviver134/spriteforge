---
name: tileset-design
description: Design coherent 16x16 terrain tiles with readable collision edges and repeatable seams.
---
Start from the approved palette, perspective and lighting. Enumerate floor, edge,
inner corner, outer corner and transition tiles before drawing variations. Keep
walkable ground quieter than actors. Use a tile adjacency table and assemble a
3x3 repeated preview to check seams in both axes. Collision belongs to explicit
tile metadata, not guessed pixel colors. Avoid unique features on every tile;
place rare props separately. Preserve tile IDs when revising a targeted seam.
