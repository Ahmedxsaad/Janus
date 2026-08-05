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
 * (D-139). Inlining also means the page needs no server at all now, so opening
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

  const WALK = ["walk_a", "walk_b", "walk_c", "walk_d"];

  const WALK_SPEED = 190;   // css pixels per second
  const WALK_FRAME_MS = 110;
  const POSE_FRAME_MS = 420;
  const TYPE_MS = 24;       // per character
  const SETTLE_MS = 180;    // pause between arriving and speaking
  const SPRITE = 32;        // the art is 32x32
  const ARRIVED = 1.5;      // px: closer than this counts as standing still
  const FLOOR = 42;         // px: the drawn step, matching .companion .floor

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

  /**
   * The one dog on the page, and where he is in walking to his current stop.
   *
   * A stop is any element carrying `data-say`. It contributes the line, the
   * pose, the collar, and `data-x`, which is where along the strip he stands
   * for it, as a fraction of the width. Moving him is the point: a companion
   * who says a new thing from the same spot every time reads as a caption.
   */
  class Companion {
    constructor(canvas, frames) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.frames = frames;
      this.stop = null;
      this.x = null;
      this.targetX = 0;
      this.facing = 1;
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

      // Big enough to read as a character, small enough that he and a bubble
      // both fit across a laptop window.
      this.scale = this.width < 760 ? 3 : 4;
      this.sprite = SPRITE * this.scale;
      // He stands on the masonry course, not in front of it: his feet land a
      // few pixels into the top of the step, which is what reads as contact.
      // FLOOR is the drawn step's height, and has to match `.companion .floor`
      // in the stylesheet; there is no way to ask canvas for a CSS length.
      this.spriteY = this.height - FLOOR - this.sprite + 8;

      if (this.stop) {
        this.layout();
      }
    }

    /** Take a new stop: where to stand, what to do there, and what to say. */
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
     * Place the dog and size his bubble for the current stop.
     *
     * The bubble takes whichever side of him has more room, and the text is
     * wrapped to that, so a narrow window gets more lines rather than a clipped
     * sentence. More lines is a taller bubble and the strip has a fixed height,
     * so a window that runs out of room gets smaller pixels rather than a
     * sentence cut off at the top of the canvas.
     */
    layout() {
      const margin = 24;
      const room = this.width - this.sprite - margin * 2;
      // data-x is a fraction of the strip he can stand across, so a stop can
      // put him anywhere without knowing the viewport width.
      const where = Number(this.stop.dataset.x);
      const fraction = Number.isFinite(where) ? Math.min(1, Math.max(0, where)) : 0;
      this.targetX = margin + room * fraction;
      if (this.x === null || still) {
        this.x = this.targetX;
      }

      // Speak into the open half of the strip, and face what you are saying.
      this.bubbleRight = this.targetX < this.width / 2;
      this.facing = this.bubbleRight ? 1 : -1;

      const gap = 14;
      const budget = this.bubbleRight
        ? this.width - (this.targetX + this.sprite) - gap - margin
        : this.targetX - gap - margin;

      for (this.textScale = this.scale - 1; this.textScale > 1; this.textScale -= 1) {
        const columns = Math.max(30, Math.floor(budget / this.textScale) - 6);
        this.lines = wrap(this.message, columns);
        const longest = this.lines.reduce((most, line) => Math.max(most, textWidth(line)), 0);
        const w = longest * this.textScale + this.textScale * 6;
        this.box = {
          x: this.bubbleRight ? this.targetX + this.sprite + gap : this.targetX - gap - w,
          y: 6,
          w,
          h: this.lines.length * LINE_STEP * this.textScale + this.textScale * 5,
        };
        // The tail hangs three of its own pixels below the box, and the box
        // must not push past the edge it grew towards.
        const fits = this.box.y + this.box.h + this.textScale * 3 <= this.height;
        if (fits && this.box.x >= margin && this.box.x + w <= this.width - margin) {
          break;
        }
      }
      this.chars = this.message.length;
    }

    /** Advance by `dt` milliseconds and paint one frame. */
    render(dt) {
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.width, this.height);
      this.clock += dt;

      const remaining = this.targetX - this.x;
      const walking = Math.abs(remaining) > ARRIVED;
      if (walking) {
        const step = Math.min(Math.abs(remaining), (WALK_SPEED * dt) / 1000);
        this.x += Math.sign(remaining) * step;
        // Face the way you are going, whatever you will face on arrival.
        this.facing = Math.sign(remaining);
        // Nothing is said mid-walk: the line belongs to the place.
        this.settled = 0;
      } else {
        this.x = this.targetX;
        this.settled += dt;
      }

      const frames = walking ? WALK : this.pose;
      const frame = Math.floor(this.clock / (walking ? WALK_FRAME_MS : POSE_FRAME_MS));
      const rows = this.frames[frames[frame % frames.length]];

      ArgosSprites.drawFrame(ctx, rows, this.scale, {
        x: this.x,
        y: this.spriteY,
        flip: this.facing < 0,
        collar: this.collar,
        // A resting dog still breathes. Half a sprite pixel, every other frame.
        bob: !walking && frame % 2 ? 0.5 : 0,
      });

      if (walking || !this.message || !this.box) {
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
      // The tail points at his head, clamped so it stays under the bubble.
      const head = this.facing > 0 ? this.x + 18 * this.scale : this.x + 8 * this.scale;
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
    // at all, on any host (D-139).
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
