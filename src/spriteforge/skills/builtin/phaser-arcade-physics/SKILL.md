---
name: phaser-arcade-physics
description: Implement Phaser 3 Arcade Physics movement, collision and lifecycle for small pixel games.
---
Use Phaser 3 browser scripts with no build step. Enable pixelArt and roundPixels,
and keep logical screen size separate from CSS scaling. Use Arcade bodies for
simple axis-aligned collision: top-down actors need a small foot collider rather
than their entire 48x48 canvas. Normalize diagonal input and set velocity rather
than accumulating per-frame displacement. Separate overlaps for damage from solid
colliders. Register input and colliders once per scene and clean up on shutdown.
Check walls, corners, simultaneous contacts, pause, restart and missing atlas keys.
A sprite added to a physics group (`group.add`, `group.create`) takes the group's body
defaults: its gravity, velocity, bounce and world-bounds settings are reset. Create the
group with its defaults (`this.physics.add.group({gravityY: G})`) or set body properties
after adding it. Side-view actors that walk on the ground need gravity and a collider with
the floor; spawn them with their feet on it, never hanging at a fixed height.
Use the Phaser 3 documentation: https://docs.phaser.io/phaser/concepts/physics/arcade
