/**
 * Argos, walking the reader down the documentation page.
 *
 * One dog for the whole page. He stands on a fixed strip along the bottom of
 * the window, and every time the reader arrives at a new section he walks to a
 * new place on that strip, drops into a new pose, and says a new line in a
 * pixel bubble. The earlier draft put a separate canvas between every pair of
 * sections, which meant nine dogs, eight of them talking to a reader who had
 * already scrolled past.
 *
 * The art is still not authored here. It is generated into `site/pixels.js`
 * from `argos/ui/sprites/argos.txt` and `argos/ui/sprites.js`, the files the
 * desktop window and the README animation read, and `tests/test_site.py` fails
 * if the generated copy and the originals disagree. So a redrawn leg still
 * lands here without anybody remembering that this page exists.
 *
 * It is inlined rather than fetched because the deployment is served with
 * `site/` as its root: `../argos/` is outside it, the fetch 404s, and the page
 * renders perfectly with no dog on it, which is how it shipped that way
 *. Inlining also means the page needs no server at all now, so opening
 * `index.html` from disk works.
 *
 * The bubble text is drawn on the canvas rather than laid out in HTML on
 * purpose. A web font would be one more thing to ship and would still not be
 * the same pixel grid as the dog; a 3x5 glyph table drawn as rects at the
 * sprite's own scale is text made of the same pixels the character is made of.
 */
(() => {
  /**
   * A five-row pixel font, one row-major bitmap string per glyph.
   *
   * Three columns wide, except M and W, which get four. Three is enough for
   * every other letter and it is not enough for those two: at three columns a W
   * is an H with a heavy bottom and an M is an H with a heavy top, so "WENT"
   * reads as "HENT" and the whole bubble looks like a typo. The width is read
   * off the string length rather than declared, so a redrawn glyph only has to
   * be the right shape.
   *
   * Uppercase only, because at this size a lowercase 'e' and a 'c' are the same
   * shape. Anything not in here is skipped rather than drawn as a missing-glyph
   * box: a bubble is decoration and must never look broken.
   */
  const FONT = {
    A: "010101111101101", B: "110101110101110", C: "011100100100011",
    D: "110101101101110", E: "111100110100111", F: "111100110100100",
    G: "011100101101011", H: "101101111101101", I: "111010010010111",
    J: "001001001101010", K: "101101110101101", L: "100100100100111",
    M: "10011111111110011001", N: "110101101101101", O: "010101101101010",
    P: "110101110100100", Q: "010101101110011", R: "110101110101101",
    S: "011100010001110", T: "111010010010010", U: "101101101101111",
    V: "101101101101010", W: "10011001111111110110", X: "101101010101101",
    Y: "101101010010010", Z: "111001010100111",
    0: "111101101101111", 1: "010110010010111", 2: "110001010100111",
    3: "110001010001110", 4: "101101111001001", 5: "111100110001110",
    6: "011100110101010", 7: "111001010010010", 8: "010101010101010",
    9: "010101011001110",
    " ": "000000000000000", ".": "000000000000010", ",": "000000000010100",
    "!": "010010010000010", "?": "110001010000010", "'": "010010000000000",
    ":": "000010000010000", "-": "000000111000000", "/": "001001010100100",
    "(": "001010010010001", ")": "100010010010100", "+": "000010111010000",
    _: "000000000000111",
  };

  const GLYPH_H = 5;
  //: One blank column between glyphs, two blank rows between lines.
  const SPACING = 1;
  const LINE_STEP = GLYPH_H + 2;

  /** How many columns a glyph occupies, read off its own bitmap. */
  function glyphWidth(glyph) {
    return glyph.length / GLYPH_H;
  }

  /** A string's width in font cells, spacing included, excluding the trailing gap. */
  function textWidth(text) {
    let cells = 0;
    for (const character of text.toUpperCase()) {
      const glyph = FONT[character];
      if (glyph) {
        cells += glyphWidth(glyph) + SPACING;
      }
    }
    return Math.max(0, cells - SPACING);
  }

  /**
   * The bubble, on paper.
   *
   * Ivory with a caramel hairline, drawn from the same values `style.css`
   * holds. Canvas takes no custom properties, so these are the one place the
   * palette is written twice; they are the page's own ink and rule rather than
   * a scheme of the bubble's own, which is what keeps the dog looking drawn
   * onto the document instead of pasted over it.
   */
  const COLOURS = {
    bubble: "#faf5ea",
    bubbleEdge: "#c08a4a",
    bubbleText: "#2b1d13",
  };

  /** Frames each pose cycles through, in order. */
  const POSES = {
    idle: ["idle_a", "idle_b", "idle_a", "blink"],
    sniff: ["sniff_a", "sniff_b"],
    alert: ["alert_a", "alert_b"],
    tilt: ["tilt_a", "tilt_b"],
    wag: ["wag_a", "wag_b"],
    scribble: ["scribble_a", "scribble_b"],
    search: ["search_a", "search_b"],
    tug: ["tug_a", "tug_b"],
    sleep: ["sleep_a", "sleep_b"],
    sit: ["sit"],
  };

  const POSE_FRAME_MS = 420;
  const TYPE_MS = 24;       // per character
  const SETTLE_MS = 180;    // pause between arriving at a section and speaking
  const SPRITE = 32;        // the art is 32x32
  const LEDGE = 16;         // px: the little step he stands on, drawn below
  const PAD = 10;           // px: breathing room inside the corner box

  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /** Draw one line of pixel text, returning nothing: the caller owns layout. */
  function drawText(ctx, text, x, y, scale, colour) {
    ctx.fillStyle = colour;
    let cursor = 0;
    for (const character of text.toUpperCase()) {
      const glyph = FONT[character];
      if (!glyph) {
        continue;
      }
      const width = glyphWidth(glyph);
      for (let row = 0; row < GLYPH_H; row += 1) {
        for (let col = 0; col < width; col += 1) {
          if (glyph[row * width + col] === "1") {
            ctx.fillRect(
              x + (cursor + col) * scale,
              y + row * scale,
              scale,
              scale,
            );
          }
        }
      }
      cursor += width + SPACING;
    }
  }

  /** Greedy word wrap at a budget measured in font cells, not characters. */
  function wrap(text, columns) {
    const lines = [];
    let line = "";
    for (const word of text.split(/\s+/)) {
      const candidate = line ? `${line} ${word}` : word;
      if (textWidth(candidate) > columns && line) {
        lines.push(line);
        line = word;
      } else {
        line = candidate;
      }
    }
    if (line) {
      lines.push(line);
    }
    return lines;
  }

  /**
   * A speech bubble with a stepped pixel edge and a tail pointing at the head.
   *
   * `revealed` is how many characters of the whole message are on screen, so
   * the same routine draws the typing and the finished bubble.
   */
  function drawBubble(ctx, lines, box, scale, tailX, revealed) {
    const edge = scale;
    ctx.fillStyle = COLOURS.bubbleEdge;
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.fillStyle = COLOURS.bubble;
    ctx.fillRect(box.x + edge, box.y + edge, box.w - edge * 2, box.h - edge * 2);

    // The tail is three stacked rects rather than a triangle: a diagonal drawn
    // at this scale is the only anti-aliased thing on the page and it shows.
    for (let step = 0; step < 3; step += 1) {
      const width = (3 - step) * scale * 2;
      ctx.fillStyle = COLOURS.bubbleEdge;
      ctx.fillRect(tailX, box.y + box.h + step * scale, width, scale);
      if (step < 2) {
        ctx.fillStyle = COLOURS.bubble;
        ctx.fillRect(tailX, box.y + box.h + step * scale - scale, width - scale * 2, scale);
      }
    }

    let budget = revealed;
    for (let i = 0; i < lines.length && budget > 0; i += 1) {
      const shown = lines[i].slice(0, budget);
      budget -= lines[i].length + 1;
      drawText(
        ctx,
        shown,
        box.x + edge * 3,
        box.y + edge * 3 + i * LINE_STEP * scale,
        scale,
        COLOURS.bubbleText,
      );
    }
  }

  /** The ornament palette, for the one ornament this file draws itself. */
  const ORNAMENTS = window.ArgosSprites?.ORNAMENT_PALETTE || {};

  /** The masonry tile, parsed once and remembered. */
  let ledgeRows = null;
  function ledge() {
    if (ledgeRows === null) {
      const art = window.ArgosSprites?.ORNAMENTS;
      ledgeRows = art ? ArgosSprites.parseSprites(art).stylobate || false : false;
    }
    return ledgeRows;
  }

  /**
   * The one dog on the page, parked in the corner and reporting on the section
   * being read.
   *
   * A stop is any element carrying `data-say`. It contributes the line, the
   * pose and the collar. It used to contribute a position too, and he walked
   * between them across the foot of the window; that put his speech bubble
   * wherever he happened to stop, and an opaque bubble over a paragraph is
   * worse than no bubble. He stays put now and the sections do the moving.
   */
  class Companion {
    constructor(canvas, frames) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.frames = frames;
      this.stop = null;
      this.pose = POSES.idle;
      this.collar = null;
      this.lines = [];
      this.message = "";
      this.settled = 0;
      this.clock = 0;
      this.measure();
    }

    /**
     * Match the backing store to the element's CSS size and the display's DPR.
     *
     * Without the DPR step the sprite's own pixels land on half a device pixel
     * on a retina screen and the whole point of pixel art is gone.
     */
    measure() {
      const rect = this.canvas.getBoundingClientRect();
      // Hidden by the narrow-screen media query, or measured before layout.
      this.live = rect.width > 0 && rect.height > 0;
      if (!this.live) {
        return;
      }
      const dpr = window.devicePixelRatio || 1;
      this.width = rect.width;
      this.height = rect.height;
      this.canvas.width = Math.round(rect.width * dpr);
      this.canvas.height = Math.round(rect.height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.ctx.imageSmoothingEnabled = false;

      // Big enough to read as a character, small enough to leave the bubble
      // above him room to be more than two words wide.
      this.scale = this.width < 240 ? 3 : 4;
      this.sprite = SPRITE * this.scale;

      // Parked, so this is the whole of his placement. He stands at the right
      // of the box with his feet a few pixels into the ledge, which is what
      // reads as contact rather than as hovering over it.
      this.x = this.width - this.sprite - PAD;
      this.spriteY = this.height - LEDGE - this.sprite + 6;
      this.ledgeY = this.height - LEDGE;

      if (this.stop) {
        this.layout();
      }
    }

    /** Take a new section: what to do, and what to say about it. */
    goTo(element) {
      if (this.stop === element) {
        return;
      }
      this.stop = element;
      this.pose = POSES[element.dataset.pose] || POSES.idle;
      this.collar = element.dataset.collar || null;
      this.message = element.dataset.say || "";
      this.settled = 0;
      if (this.live) {
        this.layout();
      }
    }

    /**
     * Size the bubble that sits above him.
     *
     * It is always in the same place, because he is: across the top of the
     * corner box, right aligned over his head. The text is wrapped to that
     * width, and if the result is too tall to stand above him the pixels get
     * smaller rather than the sentence getting cut off.
     */
    layout() {
      const room = this.width - PAD * 2;
      const ceiling = this.spriteY - 10;

      // Try the biggest text first and step down until the bubble, plus the
      // three pixels of tail hanging under it, clears his head. The smallest
      // size is a floor rather than a failure: more lines is always better than
      // a sentence cut off at the top of the canvas.
      for (let scale = this.scale - 1; scale >= 1; scale -= 1) {
        const columns = Math.max(18, Math.floor(room / scale) - 6);
        const lines = wrap(this.message, columns);
        const longest = lines.reduce((most, line) => Math.max(most, textWidth(line)), 0);
        const w = Math.min(room, longest * scale + scale * 6);
        const h = lines.length * LINE_STEP * scale + scale * 5;
        const tail = scale * 3;

        this.textScale = scale;
        this.lines = lines;
        this.box = { x: this.width - PAD - w, y: Math.max(4, ceiling - tail - h), w, h };
        if (this.box.y + h + tail <= ceiling) {
          break;
        }
      }
      this.chars = this.message.length;
    }

    /**
     * The little step he stands on.
     *
     * A short run of the same masonry the ornaments are drawn from, only as
     * wide as he is. It used to be a course across the entire window, which is
     * a lot of furniture to justify for one dog standing on one end of it.
     */
    drawLedge() {
      const rows = ledge();
      if (!rows) {
        return;
      }
      const scale = 2;
      const wide = rows[0].length * scale;
      const from = this.x - 14;
      const until = this.x + this.sprite + 14;
      for (let x = from; x < until; x += wide) {
        for (let row = 0; row < rows.length; row += 1) {
          for (let col = 0; col < rows[row].length; col += 1) {
            const colour = ORNAMENTS[rows[row][col]];
            if (!colour) {
              continue;
            }
            this.ctx.fillStyle = colour;
            this.ctx.fillRect(x + col * scale, this.ledgeY + row * scale, scale, scale);
          }
        }
      }
    }

    /** Advance by `dt` milliseconds and paint one frame. */
    render(dt) {
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.width, this.height);
      this.clock += dt;
      this.settled += dt;

      const frame = Math.floor(this.clock / POSE_FRAME_MS);
      const rows = this.frames[this.pose[frame % this.pose.length]];

      this.drawLedge();
      ArgosSprites.drawFrame(ctx, rows, this.scale, {
        x: this.x,
        y: this.spriteY,
        // Facing into the page rather than out of it: he is on the right, and a
        // companion looking off the edge of the window reads as turned away.
        flip: true,
        collar: this.collar,
        // A resting dog still breathes. Half a sprite pixel, every other frame.
        bob: frame % 2 ? 0.5 : 0,
      });

      if (!this.message || !this.box) {
        return;
      }
      const speaking = this.settled - SETTLE_MS;
      if (speaking < 0) {
        return;
      }
      const revealed = still ? this.chars : Math.min(this.chars, Math.floor(speaking / TYPE_MS));
      if (revealed <= 0) {
        return;
      }
      // The tail points down at his head, clamped so it stays under the bubble.
      const head = this.x + 8 * this.scale;
      const tailX = Math.min(
        Math.max(head, this.box.x + this.textScale * 2),
        this.box.x + this.box.w - this.textScale * 8,
      );
      drawBubble(ctx, this.lines, this.box, this.textScale, tailX, revealed);
    }
  }

  function start() {
    // `#argos-dog`, not `#argos`: the section documenting Argos owns that id
    // for the contents to link to, and a duplicate id meant querySelector
    // handed back the section instead of the canvas, so the dog never drew
    // at all, on any host.
    const canvas = document.querySelector("#argos-dog");
    const stops = Array.from(document.querySelectorAll("[data-say]"));
    if (!canvas || !stops.length || !window.ArgosSprites?.ART) {
      // Nothing worth breaking the page over: the documentation reads fine
      // without the dog in it.
      return;
    }
    const frames = ArgosSprites.parseSprites(ArgosSprites.ART);
    const argos = new Companion(canvas, frames);
    argos.goTo(stops[0]);

    /*
     * Which stop is he on? Whichever one is highest on screen but past the top
     * of the viewport, which is where a reader's attention is on a long
     * document. An IntersectionObserver alone answers "is it visible", and with
     * sections this tall two are visible at once and the answer flickers
     * between them; the observer here only decides which elements are worth
     * measuring, and the top-most of those wins.
     */
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
        // The lowest section whose top has still passed the trigger line: that
        // is the one being read, not the one being left behind above it.
        let best = null;
        for (const element of visible) {
          const top = element.getBoundingClientRect().top;
          if (top < window.innerHeight * 0.55 && (!best || top > best.top)) {
            best = { element, top };
          }
        }
        if (best) {
          argos.goTo(best.element);
        }
      },
      { rootMargin: "0px 0px -35% 0px" },
    );
    for (const stop of stops) {
      seen.observe(stop);
    }

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => argos.measure(), 150);
    });

    let last = null;
    const tick = (now) => {
      // Cap the delta: a backgrounded tab hands back one enormous frame, and an
      // uncapped one teleports him across the strip mid-stride.
      const dt = last === null ? 16 : Math.min(64, now - last);
      last = now;
      if (argos.live) {
        argos.render(dt);
      }
      window.requestAnimationFrame(tick);
    };
    window.requestAnimationFrame(tick);
  }

  /**
   * A copy button on every code block.
   *
   * Added by script rather than typed into the markup: there are thirty-odd
   * blocks and a button in each one is thirty chances to paste the wrong
   * label. The button is the last child, so it never lands inside the text a
   * reader is selecting by hand.
   */
  function addCopyButtons() {
    for (const pre of document.querySelectorAll("pre")) {
      const code = pre.querySelector("code");
      if (!code) {
        continue;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "copy";
      button.textContent = "Copy";
      button.addEventListener("click", () => {
        // Not available over plain http on a remote host, and there is no
        // sensible fallback worth the code: say nothing rather than lie.
        navigator.clipboard?.writeText(code.innerText).then(
          () => {
            button.textContent = "Copied";
            button.classList.add("done");
            window.setTimeout(() => {
              button.textContent = "Copy";
              button.classList.remove("done");
            }, 1400);
          },
          () => {},
        );
      });
      pre.appendChild(button);
    }
  }

  /**
   * Mark the sidebar link for whichever section is currently being read.
   *
   * `rootMargin` pins the trigger line near the top of the viewport rather than
   * its middle, which is where a reader's attention is on a long document: with
   * the default the highlight lags a whole screen behind the heading on screen.
   */
  function addScrollSpy() {
    const links = new Map();
    for (const link of document.querySelectorAll(".toc a[href^='#']")) {
      links.set(link.getAttribute("href").slice(1), link);
    }
    const sections = [...document.querySelectorAll("section[id]")].filter((s) => links.has(s.id));
    if (!sections.length) {
      return;
    }
    const seen = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) {
            continue;
          }
          for (const link of links.values()) {
            link.classList.remove("here");
          }
          links.get(entry.target.id)?.classList.add("here");
        }
      },
      { rootMargin: "-80px 0px -70% 0px" },
    );
    for (const section of sections) {
      seen.observe(section);
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    start();
    addCopyButtons();
    addScrollSpy();
  });
})();
