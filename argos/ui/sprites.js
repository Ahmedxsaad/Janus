/**
 * Sprite loading and pixel drawing, shared by the pet window and the walk
 * overlay.
 *
 * A classic script rather than a module, deliberately: an ES module is blocked
 * under file:// and would force a bundler or a dev server on a contributor who
 * only wants to look at the art. Both pages get `window.ArgosSprites`.
 */
window.ArgosSprites = (() => {
  /**
   * The character's colours.
   *
   * Warmer and deeper than the first pass, which read flat and slightly grey on
   * a dark desktop. The coat keeps a hint of blue so it sits in DataHub's family
   * rather than looking like unpainted white, the outline is a navy rather than
   * a black so the silhouette has some colour in it, and the amber is pushed
   * toward gold because the previous one went muddy the moment `dim` halved it.
   *
   * Adding a key here is not enough to use it: the art indexes these letters, so
   * a new colour means editing frames, and tests/test_argos.py holds the palette
   * to exactly this key set.
   */
  const PALETTE = {
    k: "#160f0a",
    w: "#d99347",
    g: "#a96b2c",
    a: "#2a2119",
    o: "#15100c",
    b: "#2668e8",
    d: "#16408f",
    r: "#f22525",
  };

  /**
   * A soft top-down light, applied per pixel as the sprite is drawn.
   *
   * The art has one flat tone per material, so a pet this small reads as a
   * sticker however good the colours are. Lifting the top rows a little and
   * dropping the bottom rows gives it a body without asking the art to carry a
   * second shade of every colour, which at this size it has no room for.
   */
  const LIGHT_TOP = 1.03;
  const LIGHT_BOTTOM = 0.76;

  // Frames are square and every frame in a file is the same size, so the art
  // decides this rather than the code: a contributor's redraw at a different
  // resolution needs no edit here, only this one number to match it.
  const PIXELS = 32;

  /**
   * Parse a character file into named 16x16 frames.
   *
   * A `# name` line opens a frame and every following non-blank line is a row.
   * Any other `#` line is a comment, which is how the file carries its own
   * palette legend.
   */
  function parseSprites(text) {
    const frames = {};
    let name = null;
    let rows = [];
    const close = () => {
      if (name && rows.length) {
        frames[name] = rows;
      }
    };
    for (const raw of text.split("\n")) {
      const line = raw.trimEnd();
      if (line.startsWith("#")) {
        close();
        const match = /^#\s*([a-z_]+)\s*$/.exec(line);
        name = match ? match[1] : null;
        rows = [];
      } else if (line.trim() !== "" && name) {
        rows.push(line);
      }
    }
    close();
    return frames;
  }

  /** Darken a hex colour, for the dropped-trust-band look. */
  function dimmed(hex) {
    const value = parseInt(hex.slice(1), 16);
    const channel = (shift) => Math.round(((value >> shift) & 255) * 0.5);
    return `rgb(${channel(16)},${channel(8)},${channel(0)})`;
  }

  /**
   * Scale a hex colour's brightness, clamped, for the top-down light.
   *
   * The outline is exempt at the call site: lightening it turns the silhouette
   * to mush, and the silhouette is the only reason the character reads at all
   * against a wallpaper it did not choose.
   */
  function shaded(hex, factor) {
    const value = parseInt(hex.slice(1), 16);
    const channel = (shift) => {
      const raw = Math.round(((value >> shift) & 255) * factor);
      return raw > 255 ? 255 : raw;
    };
    return `rgb(${channel(16)},${channel(8)},${channel(0)})`;
  }

  /**
   * Draw one frame into a 2d context at the given scale.
   *
   * `style` carries the state modifiers: `alpha` for the disconnected ghost,
   * `dim` for a dropped trust band, `collar` to repaint the blue collar (red
   * only ever means a live finding), and `bob` to nudge the whole sprite down
   * by a fraction of a pixel so a static frame still breathes.
   */
  function drawFrame(ctx, rows, scale, style = {}) {
    if (!rows) {
      return;
    }
    const originX = style.x || 0;
    const originY = (style.y || 0) + (style.bob || 0) * scale;
    ctx.globalAlpha = style.alpha === undefined ? 1 : style.alpha;

    // Facing left is the same art mirrored. Doing it as a canvas transform
    // rather than by reversing the row strings keeps the rim, which reads the
    // same rows, in step with the body automatically.
    const mirrored = Boolean(style.flip);
    if (mirrored) {
      ctx.save();
      ctx.translate(originX * 2 + rows[0].length * scale, 0);
      ctx.scale(-1, 1);
    }

    if (style.rim !== false) {
      drawRim(ctx, rows, scale, originX, originY);
    }
    const tall = rows.length - 1;
    for (let y = 0; y < rows.length; y += 1) {
      const row = rows[y];
      // Top rows catch the light, bottom rows fall into the body's own shadow.
      const light = LIGHT_TOP + (LIGHT_BOTTOM - LIGHT_TOP) * (y / tall);
      for (let x = 0; x < row.length; x += 1) {
        let key = row[x];
        if (key === ".") {
          continue;
        }
        // Both rows of the collar, not just the lit one. Repainting only 'b'
        // left the shade row blue underneath, so a live finding showed as a
        // red-over-blue stripe rather than as a red collar. The two rows still
        // differ, because the top-down light below already separates them.
        if ((key === "b" || key === "d") && style.collar) {
          key = style.collar;
        }
        const colour = PALETTE[key];
        if (!colour) {
          continue;
        }
        // The outline is never lit: it is the silhouette, and a lit silhouette
        // stops separating the dog from the wallpaper behind it.
        ctx.fillStyle = style.dim
          ? dimmed(colour)
          : key === "k"
            ? colour
            : shaded(colour, light);
        ctx.fillRect(originX + x * scale, originY + y * scale, scale, scale);
      }
    }

    if (mirrored) {
      ctx.restore();
    }
    ctx.globalAlpha = 1;
  }

  /**
   * A one-pixel light rim just outside the sprite's own dark outline.
   *
   * The outline is DataHub's near-black, which disappears against a dark
   * wallpaper and takes the silhouette with it: the tail stops looking attached
   * and the dog stops looking like one shape. A desktop pet cannot choose its
   * background, so it carries its own separation. Against a light background
   * the rim is nearly invisible and the dark outline does the work instead.
   */
  function drawRim(ctx, rows, scale, originX, originY) {
    const filled = (x, y) => rows[y] !== undefined && rows[y][x] !== undefined && rows[y][x] !== ".";
    ctx.save();
    // Faint on purpose. At full strength it haloes a near-white coat into a
    // blob, which costs more silhouette than the dark wallpaper ever did.
    ctx.globalAlpha *= 0.3;
    ctx.fillStyle = "#e8edf5";
    for (let y = 0; y < rows.length; y += 1) {
      for (let x = 0; x < rows[y].length; x += 1) {
        if (filled(x, y)) {
          continue;
        }
        if (filled(x - 1, y) || filled(x + 1, y) || filled(x, y - 1) || filled(x, y + 1)) {
          ctx.fillRect(originX + x * scale, originY + y * scale, scale, scale);
        }
      }
    }
    ctx.restore();
  }

  /** Fetch and parse the character file that ships next to these scripts. */
  async function load() {
    const response = await fetch("sprites/argos.txt");
    return parseSprites(await response.text());
  }

  return { PALETTE, PIXELS, parseSprites, dimmed, shaded, drawFrame, load };
})();
