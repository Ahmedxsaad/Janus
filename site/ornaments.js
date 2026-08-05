/**
 * The Roman ornaments, painted onto the page.
 *
 * Janus is the god of doors, gates and beginnings, so the page is decorated as
 * the building he would have been kept in: an Ionic column, an arch, a temple
 * front, a wreath, a tripod burning, and a Greek key running along the rules.
 * The art is pixels for the same reason the dog is, and it is drawn from the
 * same bundle, in the page's own stone and caramel rather than a palette of its
 * own (site/art/make_ornaments.py).
 *
 * Three kinds of placement, and they exist to solve three different empty
 * spaces rather than to be decorative in general:
 *
 *   .relic      the rail beside the prose. Body text is set to a 33rem measure
 *               and the column it sits in is wider than that, so every section
 *               left a vertical band of nothing. One ornament stands there and
 *               changes with the section, the same way the dog does.
 *   .frieze     a horizontal band of the running key, for the rules between
 *               regions of the page, where a hairline used to do the work.
 *   .colonnade  the outer margins on a wide screen, which no content reaches.
 *
 * Everything here is decoration and is marked `aria-hidden` in the markup: a
 * screen reader is told about the document, never about the furniture.
 */
(() => {
  const palette = window.ArgosSprites.ORNAMENT_PALETTE;
  const art = window.ArgosSprites.parseSprites(window.ArgosSprites.ORNAMENTS);

  /**
   * Paint one piece at `scale`, with its top left at (x, y).
   *
   * No rim and no top-down light, unlike the character: an ornament's light is
   * baked into the art by the generator, which can see the whole shape, and a
   * second pass over it here would flatten what that one carved.
   */
  function paint(ctx, rows, scale, x, y) {
    for (let row = 0; row < rows.length; row += 1) {
      for (let col = 0; col < rows[row].length; col += 1) {
        const colour = palette[rows[row][col]];
        if (!colour) {
          continue;
        }
        ctx.fillStyle = colour;
        ctx.fillRect(x + col * scale, y + row * scale, scale, scale);
      }
    }
  }

  /** Match the backing store to the CSS box and the display, and clear it. */
  function prepare(canvas) {
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) {
      return null;
    }
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, rect.width, rect.height);
    return { ctx, width: rect.width, height: rect.height };
  }

  /**
   * A single piece, centred in its canvas at the largest whole scale that fits.
   *
   * Whole numbers only. A pixel drawn at 2.5 device pixels is a pixel with a
   * soft edge on one side, and one soft edge is enough to lose the whole look.
   */
  function paintPiece(canvas, name) {
    const rows = art[name];
    const box = prepare(canvas);
    if (!rows || !box) {
      return;
    }
    const wide = rows[0].length;
    const tall = rows.length;
    const scale = Math.max(1, Math.floor(Math.min(box.width / wide, box.height / tall)));
    paint(
      box.ctx,
      rows,
      scale,
      Math.round((box.width - wide * scale) / 2),
      Math.round((box.height - tall * scale) / 2),
    );
  }

  /**
   * A piece repeated across the canvas, for the tiling two: the running key and
   * the masonry course.
   *
   * `align` decides which edge a partial tile is allowed to fall off, so a band
   * that meets a corner is not cut in the middle of a turn.
   */
  function paintTiled(canvas, name, { scale = 2, vertical = false } = {}) {
    const rows = art[name];
    const box = prepare(canvas);
    if (!rows || !box) {
      return;
    }
    const wide = rows[0].length * scale;
    const tall = rows.length * scale;
    if (vertical) {
      const x = Math.round((box.width - wide) / 2);
      for (let y = 0; y < box.height; y += tall) {
        paint(box.ctx, rows, scale, x, y);
      }
    } else {
      const y = Math.round((box.height - tall) / 2);
      for (let x = 0; x < box.width; x += wide) {
        paint(box.ctx, rows, scale, x, y);
      }
    }
  }

  /** Repaint everything that is declared in the markup. */
  function paintAll() {
    for (const canvas of document.querySelectorAll("canvas[data-piece]")) {
      const name = canvas.dataset.piece;
      const scale = Number(canvas.dataset.scale) || 2;
      if (canvas.dataset.tile === "x") {
        paintTiled(canvas, name, { scale });
      } else if (canvas.dataset.tile === "y") {
        paintTiled(canvas, name, { scale, vertical: true });
      } else {
        paintPiece(canvas, name);
      }
    }
  }

  /**
   * The rail beside the prose, which follows the reader.
   *
   * Each section names the ornament that stands next to it, so the piece
   * changes as the document does. The same observer shape the dog uses: the
   * lowest section whose top has passed the trigger line is the one being read,
   * and with sections this tall a plain "is it visible" test flickers between
   * two of them.
   */
  function followSections(canvas) {
    const sections = Array.from(document.querySelectorAll("[data-relic]"));
    if (!sections.length) {
      return () => {};
    }
    let current = null;
    const show = (name) => {
      if (name === current) {
        return;
      }
      current = name;
      canvas.dataset.piece = name;
      paintPiece(canvas, name);
      // Restart the reveal, so an ornament arrives rather than appearing.
      canvas.classList.remove("arrive");
      void canvas.offsetWidth;
      canvas.classList.add("arrive");
    };
    show(sections[0].dataset.relic);

    const visible = new Set();
    const seen = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            visible.add(entry.target);
          } else {
            visible.delete(entry.target);
          }
        }
        let best = null;
        for (const element of visible) {
          const top = element.getBoundingClientRect().top;
          if (top < window.innerHeight * 0.55 && (!best || top > best.top)) {
            best = { element, top };
          }
        }
        if (best) {
          show(best.element.dataset.relic);
        }
      },
      { rootMargin: "0px 0px -35% 0px" },
    );
    for (const section of sections) {
      seen.observe(section);
    }
    return () => paintPiece(canvas, current);
  }

  window.addEventListener("DOMContentLoaded", () => {
    paintAll();
    const relic = document.querySelector("#relic");
    const repaintRelic = relic ? followSections(relic) : () => {};

    let timer = null;
    window.addEventListener("resize", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        paintAll();
        repaintRelic();
      }, 150);
    });
  });
})();
