/**
 * Sprite loading and pixel drawing, shared by the pet window and the walk
 * overlay.
 *
 * A classic script rather than a module, deliberately: an ES module is blocked
 * under file:// and would force a bundler or a dev server on a contributor who
 * only wants to look at the art. Both pages get `window.ArgosSprites`.
 */
window.ArgosSprites = (() => {
  const PALETTE = {
    k: "#12233f",
    w: "#f7f7f7",
    a: "#f39f19",
    b: "#1857d2",
    d: "#1b49a0",
    r: "#e90101",
  };

  const PIXELS = 16;

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
    for (let y = 0; y < rows.length; y += 1) {
      const row = rows[y];
      for (let x = 0; x < row.length; x += 1) {
        let key = row[x];
        if (key === ".") {
          continue;
        }
        if (key === "b" && style.collar) {
          key = style.collar;
        }
        const colour = PALETTE[key];
        if (!colour) {
          continue;
        }
        ctx.fillStyle = style.dim ? dimmed(colour) : colour;
        ctx.fillRect(originX + x * scale, originY + y * scale, scale, scale);
      }
    }
    ctx.globalAlpha = 1;
  }

  /** Fetch and parse the character file that ships next to these scripts. */
  async function load() {
    const response = await fetch("sprites/argos.txt");
    return parseSprites(await response.text());
  }

  return { PALETTE, PIXELS, parseSprites, dimmed, drawFrame, load };
})();
