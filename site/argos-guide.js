/**
 * Argos, walking the reader down the documentation page.
 *
 * Every beat on the page is one `<canvas class="beat">`: Argos walks in from
 * the edge, stops, drops into a pose, and says one line in a pixel bubble. The
 * art is not copied here. It is read from `argos/ui/sprites/argos.txt` through
 * `argos/ui/sprites.js`, the same two files the desktop window and the README
 * animation read, so a redraw of a leg lands here without anybody remembering
 * that this page exists.
 *
 * That sharing is why the page needs a server: `fetch` of a local file is
 * blocked under `file://`. Serve the repository root (`python -m http.server`)
 * and open `/site/`.
 *
 * The bubble text is drawn on the canvas rather than laid out in HTML on
 * purpose. A web font would be one more thing to ship and would still not be
 * the same pixel grid as the dog; a 3x5 glyph table drawn as rects at the
 * sprite's own scale is text made of the same pixels the character is made of.
 */
(() => {
  const SPRITE_FILE = "../argos/ui/sprites/argos.txt";

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

  const COLOURS = {
    bubble: "#f3e3cb",
    bubbleEdge: "#160f0a",
    bubbleText: "#2a1a10",
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

  const WALK = ["walk_a", "walk_b", "walk_c", "walk_d"];

  const WALK_SPEED = 130;   // css pixels per second
  const WALK_FRAME_MS = 110;
  const POSE_FRAME_MS = 420;
  const TYPE_MS = 26;       // per character
  const SETTLE_MS = 220;    // pause between arriving and speaking

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

  /** One beat: a canvas, its message, and where Argos is in walking it. */
  class Beat {
    constructor(canvas, frames) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.frames = frames;
      this.pose = POSES[canvas.dataset.pose] || POSES.idle;
      this.message = canvas.dataset.say || "";
      this.fromRight = canvas.dataset.from === "right";
      this.collar = canvas.dataset.collar || null;
      this.scale = Number(canvas.dataset.scale || 4);
      this.textScale = Number(canvas.dataset.textScale || 3);
      this.started = null;
      this.active = false;
      this.resize();
    }

    /**
     * Match the backing store to the element's CSS size and the display's DPR.
     *
     * Without the DPR step the sprite's own pixels land on half a device pixel
     * on a retina screen and the whole point of pixel art is gone.
     */
    resize() {
      const rect = this.canvas.getBoundingClientRect();
      if (!rect.width) {
        return;
      }
      const dpr = window.devicePixelRatio || 1;
      this.width = rect.width;
      this.height = rect.height;
      this.canvas.width = Math.round(rect.width * dpr);
      this.canvas.height = Math.round(rect.height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.ctx.imageSmoothingEnabled = false;

      const sprite = 32 * this.scale;
      this.restX = this.fromRight ? this.width - sprite - 12 : 12;
      this.startX = this.fromRight ? this.width + 8 : -sprite - 8;
      this.spriteY = this.height - sprite - 4;

      // The bubble takes whatever the dog leaves, and the text is wrapped to
      // that, so a narrow phone gets more lines rather than a clipped sentence.
      // More lines is a taller bubble, though, and the strip has a fixed height,
      // so a phone that runs out of room gets smaller pixels rather than a
      // sentence cut off at the top of the canvas.
      const gap = 14;
      const room = this.width - sprite - gap - 24;
      const wanted = Number(this.canvas.dataset.textScale || 3);
      for (this.textScale = wanted; this.textScale > 1; this.textScale -= 1) {
        const columns = Math.max(40, Math.floor(room / this.textScale) - 6);
        this.lines = wrap(this.message, columns);
        const longest = this.lines.reduce((most, line) => Math.max(most, textWidth(line)), 0);
        this.box = {
          x: this.fromRight ? 12 : sprite + gap + 12,
          y: 6,
          w: longest * this.textScale + this.textScale * 6,
          h: this.lines.length * LINE_STEP * this.textScale + this.textScale * 5,
        };
        // The tail hangs three of its own pixels below the box.
        if (this.box.y + this.box.h + this.textScale * 3 <= this.height) {
          break;
        }
      }
      this.chars = this.message.length;
    }

    /** Draw the beat at `elapsed` milliseconds since it came into view. */
    render(elapsed) {
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.width, this.height);

      const travel = Math.abs(this.restX - this.startX);
      const walkMs = still ? 0 : (travel / WALK_SPEED) * 1000;
      const walking = elapsed < walkMs;
      const progress = walkMs ? Math.min(1, elapsed / walkMs) : 1;
      const x = this.startX + (this.restX - this.startX) * progress;

      const frames = walking
        ? WALK
        : this.pose;
      const step = walking
        ? Math.floor(elapsed / WALK_FRAME_MS)
        : Math.floor((elapsed - walkMs) / POSE_FRAME_MS);
      const rows = this.frames[frames[step % frames.length]];

      ArgosSprites.drawFrame(ctx, rows, this.scale, {
        x,
        y: this.spriteY,
        flip: this.fromRight,
        collar: this.collar,
        // A resting dog still breathes. Half a sprite pixel, every other frame.
        bob: !walking && step % 2 ? 0.5 : 0,
      });

      if (!this.message) {
        return;
      }
      const speaking = elapsed - walkMs - SETTLE_MS;
      if (speaking < 0) {
        return;
      }
      const revealed = still ? this.chars : Math.min(this.chars, Math.floor(speaking / TYPE_MS));
      if (revealed <= 0) {
        return;
      }
      const head = this.fromRight ? x + 8 * this.scale : x + 18 * this.scale;
      const tailX = Math.min(
        Math.max(head, this.box.x + this.textScale * 2),
        this.box.x + this.box.w - this.textScale * 8,
      );
      drawBubble(this.ctx, this.lines, this.box, this.textScale, tailX, revealed);
    }
  }

  async function start() {
    const canvases = Array.from(document.querySelectorAll("canvas.beat"));
    if (!canvases.length) {
      return;
    }
    const response = await fetch(SPRITE_FILE);
    if (!response.ok) {
      // Nothing to fall back to and nothing worth breaking the page over: the
      // documentation reads fine without the dog in it.
      return;
    }
    const frames = ArgosSprites.parseSprites(await response.text());
    const beats = canvases.map((canvas) => new Beat(canvas, frames));

    // Only beats on screen animate, and each one restarts its walk when the
    // reader comes back to it, so the page is a sequence rather than a room
    // full of dogs that all finished talking while nobody was looking.
    const seen = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const beat = beats[canvases.indexOf(entry.target)];
          beat.active = entry.isIntersecting;
          if (entry.isIntersecting && beat.started === null) {
            beat.started = performance.now();
          } else if (!entry.isIntersecting) {
            beat.started = null;
          }
        }
      },
      { threshold: 0.35 },
    );
    for (const canvas of canvases) {
      seen.observe(canvas);
    }

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => beats.forEach((beat) => beat.resize()), 150);
    });

    const tick = (now) => {
      for (const beat of beats) {
        if (beat.active && beat.started !== null) {
          beat.render(now - beat.started);
        }
      }
      window.requestAnimationFrame(tick);
    };
    window.requestAnimationFrame(tick);
  }

  window.addEventListener("DOMContentLoaded", start);
})();
