/**
 * Argos: the state machine, the interactions, and the transport.
 *
 * Everything drawn here depicts an event some producer sent. There is no timer
 * that invents activity to look busy, which is docs/plan/08 section 3 and root
 * CLAUDE.md rule 4 applied to pixels.
 *
 * Two kinds of motion are exempt from that rule, and the boundary matters:
 *
 * 1. The frame cycle *within* the state the producer already put us in. A dog
 *    that breathes and blinks while patrolling claims nothing about the graph.
 * 2. Roaming, and only while patrolling. A patrol is a beat somebody walks, so
 *    a dog that paces its strip, stops, looks around and turns back is
 *    depicting the state it was actually put in rather than inventing a new
 *    one. Every other state stands still on purpose: a barking dog that
 *    wandered off mid-finding, or a sleeping one that strolled, would be the
 *    sprite contradicting the event.
 *
 * Reacting to the *user* is likewise not an invention. A cursor to follow and a
 * hand to be petted by are real events; they are just not events about DataHub,
 * and nothing in these reactions touches what the bubble or the collar say.
 *
 * Two transports, one renderer: inside Tauri the events arrive from the
 * producer over stdin; in a plain browser they are replayed from fixture.jsonl,
 * which is what makes every state reproducible without DataHub or Rust.
 */

/**
 * States, and how each one is drawn.
 *
 * A timeline of `[frame, milliseconds]` rather than a frame rate, because the
 * life is in the uneven timing: a two-second hold and a 130ms blink is a dog,
 * four frames at 3fps is a flipbook.
 *
 * `dim` and `alpha` are the two health modifiers, `collar` repaints the collar,
 * and `shake` rattles the whole sprite. Red is state, never decoration: only a
 * live finding sets it. `roams` is the one state allowed to move (see above).
 */
const STATES = {
  patrolling: {
    timeline: [
      ["idle_a", 2600],
      ["blink", 130],
      ["idle_a", 1500],
      ["idle_b", 1900],
      ["blink", 120],
    ],
    roams: true,
  },
  sniffing: {
    timeline: [
      ["sniff_a", 320],
      ["sniff_b", 300],
    ],
  },
  narrating: {
    timeline: [
      ["tilt_a", 900],
      ["tilt_b", 950],
    ],
  },
  barking: {
    timeline: [
      ["alert_a", 150],
      ["alert_b", 170],
    ],
    collar: "r",
    shake: true,
  },
  scribbling: {
    timeline: [
      ["scribble_a", 260],
      ["scribble_b", 280],
    ],
  },
  tugging: {
    timeline: [
      ["tug_a", 220],
      ["tug_b", 240],
    ],
  },
  asleep: {
    timeline: [
      ["sleep_a", 1500],
      ["sleep_b", 1600],
    ],
  },
  recovered: {
    timeline: [
      ["wag_a", 150],
      ["wag_b", 160],
    ],
  },
  unchecked: {
    timeline: [
      ["search_a", 750],
      ["search_b", 800],
    ],
  },
  muted: {
    timeline: [
      ["sit", 2400],
      ["blink", 140],
    ],
    dim: true,
  },
  sick: {
    timeline: [
      ["idle_a", 1800],
      ["blink", 220],
    ],
    dim: true,
  },
  ghost: {
    timeline: [
      ["idle_a", 1200],
      ["idle_b", 1200],
    ],
    alpha: 0.35,
  },
};

const DEFAULT_STATE = "patrolling";

/** The four-frame gait, shared with the blast-radius overlay. */
const WALK_CYCLE = [
  ["walk_a", 110],
  ["walk_b", 110],
  ["walk_c", 110],
  ["walk_d", 110],
];

/** Frames the dog wags with when somebody pets it. */
const PET_CYCLE = [
  ["wag_a", 120],
  ["wag_b", 130],
];

/** How long a bubble stays up before it gets out of the way. */
const BUBBLE_MS = 9000;

/** How long a petting reaction lasts before the patrol resumes. */
const PET_MS = 1800;

/** Desktop pixels per second while roaming. A trot, not a sprint. */
const ROAM_SPEED = 26;

/** Send one command line to the producer, if there is one listening. */
function sendCommand(cmd, args = {}) {
  const line = JSON.stringify({ cmd, args });
  if (window.__TAURI__) {
    window.__TAURI__.core.invoke("send_command", { line }).catch(() => {});
  } else {
    // The browser demo has no producer. Saying so beats a silent no-op when
    // somebody is clicking around the hosted page.
    console.info("argos: no producer attached, dropped", line);
  }
}

/**
 * Subscribe to the event stream and call `onEvent` for every event.
 *
 * Both transports keep running for the life of the page. The fixture loops, so
 * the browser demo cycles through every state on its own.
 */
async function connect(onEvent) {
  if (window.__TAURI__) {
    await window.__TAURI__.event.listen("argos://event", (message) => {
      try {
        onEvent(JSON.parse(message.payload));
      } catch (error) {
        console.warn("argos: unparseable event", error);
      }
    });
    return;
  }
  const response = await fetch("fixture.jsonl");
  const lines = (await response.text())
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("//"));
  let index = 0;
  const step = () => {
    onEvent(JSON.parse(lines[index % lines.length]));
    index += 1;
  };
  step();
  window.setInterval(step, 4200);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

/** A number in [min, max). */
function between(min, max) {
  return min + Math.random() * (max - min);
}

class Argos {
  constructor(canvas, frames) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.frames = frames;
    this.size = canvas.width;
    this.scale = this.size / window.ArgosSprites.PIXELS;
    this.state = DEFAULT_STATE;
    this.event = { v: 1, state: DEFAULT_STATE };
    this.step = 0;
    this.stepAt = 0;
    this.enteredAt = 0;
    this.bubble = document.getElementById("bubble");
    this.bubbleText = this.bubble.querySelector(".text");
    this.trust = document.getElementById("trust");
    this.menu = document.getElementById("menu");
    this.floor = document.getElementById("floor");
    this.hideAt = 0;

    // Where the dog is standing, and which way it is looking. `x` is the
    // canvas's left edge within the floor strip, so the roam is a real position
    // rather than a transform the drawing has to undo.
    this.x = 0;
    this.facing = 1;
    this.target = null;
    this.restUntil = 0;
    this.gait = 0;
    this.gaitAt = 0;

    // Interaction state, none of which says anything about the graph.
    this.pointer = null;
    this.petUntil = 0;
    this.pets = 0;

    for (let index = 0; index < 10; index += 1) {
      this.trust.appendChild(document.createElement("i"));
    }
    this.layout();
    // Start somewhere along the strip rather than dead centre every launch.
    this.x = between(0, Math.max(1, this.span));
  }

  /** Re-read the strip's width, which changes when the window is resized. */
  layout() {
    this.span = Math.max(0, this.floor.clientWidth - this.size);
  }

  /** Apply an event: the state it names is the only thing that moves us. */
  apply(event) {
    this.event = event;
    // An unknown state renders as patrolling rather than throwing: a newer
    // producer must never break an older window (docs/plan/08 section 6).
    const next = STATES[event.state] ? event.state : DEFAULT_STATE;
    if (next !== this.state) {
      this.state = next;
      this.step = 0;
      this.stepAt = 0;
      // A state that is not a patrol stands still, so a walk in progress is
      // abandoned here rather than finishing under the wrong sprite.
      this.target = null;
      this.restUntil = 0;
      // The squash-and-stretch on entry is what makes a state change feel like
      // the dog reacted rather than the sprite being swapped.
      this.enteredAt = performance.now();
    }
    this.showBubble();
  }

  showBubble() {
    const event = this.event;
    if (!event.title) {
      return;
    }
    const severity = (event.severity || "").toLowerCase();
    const chip = severity ? `<span class="chip ${severity}">${severity}</span>` : "";
    this.bubbleText.innerHTML = `${chip}${escapeHtml(event.title)}`;

    // The colour comes from the band the detector decided, never from a
    // threshold applied to the score again here. A model can score 70 and still
    // be on watch, because a critical finding caps its band, and a meter that
    // re-derived the band would paint that model healthy while the catalogue
    // calls it watch.
    const score = typeof event.trust === "number" ? event.trust : null;
    const band = (event.band || "").toLowerCase();
    this.trust.className =
      score === null
        ? ""
        : band === "at-risk"
          ? "shown risk"
          : band === "watch"
            ? "shown watch"
            : "shown";
    if (score !== null) {
      const lit = Math.round(score / 10);
      [...this.trust.children].forEach((segment, index) => {
        segment.className = index < lit ? "on" : "";
      });
    }

    this.bubble.classList.add("shown");
    // A bubble that never leaves is a sticker. It comes back on the next event,
    // and on a click.
    this.hideAt = performance.now() + BUBBLE_MS;
  }

  toggleBubble() {
    if (this.bubble.classList.contains("shown")) {
      this.bubble.classList.remove("shown");
      this.hideAt = 0;
    } else {
      this.showBubble();
    }
  }

  /**
   * Somebody clicked the dog.
   *
   * The wag is a reaction to a hand, not a claim about the catalogue, so it
   * runs on top of whatever state the producer last set and expires back into
   * it. It deliberately does not touch the collar or the bubble.
   */
  pet() {
    this.petUntil = performance.now() + PET_MS;
    this.pets += 1;
    // Being petted interrupts the walk, the way it would interrupt a real dog.
    this.target = null;
  }

  /** True while a petting reaction is playing. */
  petting(now) {
    return now < this.petUntil;
  }

  /**
   * Move the dog along its strip.
   *
   * Alternates between standing somewhere and walking to a new spot. Only ever
   * called while patrolling; every other state returns early and the dog holds
   * its ground.
   */
  roam(now, delta) {
    if (this.target === null) {
      if (now < this.restUntil) {
        return false;
      }
      // Pick somewhere else to be, far enough away that the walk is legible
      // rather than a twitch.
      const span = this.span;
      if (span < 8) {
        return false;
      }
      let next = between(0, span);
      if (Math.abs(next - this.x) < span * 0.25) {
        next = this.x < span / 2 ? between(span * 0.55, span) : between(0, span * 0.45);
      }
      this.target = next;
      this.facing = next > this.x ? 1 : -1;
      return false;
    }

    const remaining = this.target - this.x;
    const stride = ROAM_SPEED * delta;
    if (Math.abs(remaining) <= stride) {
      this.x = this.target;
      this.target = null;
      // Stand a while before the next leg. The range is wide on purpose: an
      // even cadence reads as a machine pacing, not an animal.
      this.restUntil = now + between(1400, 5200);
      return false;
    }
    this.x += Math.sign(remaining) * stride;
    return true;
  }

  /** Turn to look at the cursor, when standing still and free to notice it. */
  watchPointer() {
    if (this.pointer === null || this.target !== null) {
      return;
    }
    const centre = this.x + this.size / 2;
    // A dead band, so a cursor hovering near the nose does not flip the sprite
    // back and forth every frame.
    if (Math.abs(this.pointer - centre) > this.size * 0.22) {
      this.facing = this.pointer > centre ? 1 : -1;
    }
  }

  /** Advance the timeline and draw one frame. */
  tick(now) {
    const delta = this.lastTick ? Math.min((now - this.lastTick) / 1000, 0.05) : 0;
    this.lastTick = now;

    const spec = STATES[this.state];
    const petting = this.petting(now);
    const moving = spec.roams && !petting && this.roam(now, delta);
    if (!moving) {
      this.watchPointer();
    }

    // Which timeline is playing: the walk while a leg is in progress, the wag
    // while a hand is on the dog, otherwise the state's own.
    const timeline = moving ? WALK_CYCLE : petting ? PET_CYCLE : spec.timeline;
    if (timeline !== this.timeline) {
      this.timeline = timeline;
      this.step = 0;
      this.stepAt = now;
    }
    const [, hold] = timeline[this.step % timeline.length];
    if (now - this.stepAt >= hold) {
      this.stepAt = now;
      this.step = (this.step + 1) % timeline.length;
    }

    if (this.hideAt && now > this.hideAt) {
      this.bubble.classList.remove("shown");
      this.hideAt = 0;
    }

    // The canvas is moved rather than the sprite drawn at an offset, so the
    // shadow, the rim and the squash all travel with the dog for free.
    this.canvas.style.transform = `translateX(${Math.round(this.x)}px)`;
    // The bubble's tail follows the head. Without this it stayed at 50% and
    // pointed into empty space the moment the dog walked off centre.
    this.bubble.style.setProperty("--tail", `${Math.round(this.x + this.size / 2)}px`);

    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.size, this.size);
    this.drawShadow(spec, now, moving);

    // Entry squash: 180ms of a flattened, wider sprite settling back to square.
    const since = now - this.enteredAt;
    const squash = since < 180 ? Math.sin((since / 180) * Math.PI) * 0.09 : 0;
    const shake = spec.shake ? Math.round(Math.sin(now / 45) * 1.2) : 0;
    // A walking dog rises and falls on its own stride; a petted one bounces a
    // little harder. Both are a fraction of a sprite pixel, not a hop.
    const bob = moving
      ? Math.abs(Math.sin(now / 110)) * -0.35
      : petting
        ? Math.abs(Math.sin(now / 90)) * -0.5
        : 0;

    ctx.save();
    ctx.translate(this.size / 2 + shake, this.size);
    ctx.scale(1 + squash, 1 - squash);
    ctx.translate(-this.size / 2, -this.size);
    window.ArgosSprites.drawFrame(ctx, this.frames[timeline[this.step][0]], this.scale, {
      alpha: spec.alpha,
      dim: spec.dim,
      collar: spec.collar,
      flip: this.facing === -1,
      bob,
    });
    ctx.restore();
  }

  /** The ellipse that puts the dog on the desktop instead of above it. */
  drawShadow(spec, now, moving) {
    const ctx = this.ctx;
    // Breathing while still, stride while walking: the shadow is the cheapest
    // place to show weight shifting.
    const pulse = moving ? 1 + Math.sin(now / 110) * 0.06 : 1 + Math.sin(now / 900) * 0.03;
    ctx.save();
    ctx.globalAlpha = (spec.alpha === undefined ? 1 : spec.alpha) * 0.22;
    ctx.fillStyle = "#000";
    ctx.beginPath();
    ctx.ellipse(
      this.size / 2,
      this.size - this.scale * 1.6,
      this.size * 0.3 * pulse,
      this.scale * 0.9,
      0,
      0,
      Math.PI * 2,
    );
    ctx.fill();
    ctx.restore();
  }

  /** Walk the blast radius across the desktop, if this event carries one. */
  walk() {
    const path = this.event.path;
    if (!Array.isArray(path) || path.length < 2) {
      return;
    }
    if (window.__TAURI__) {
      window.__TAURI__.core
        .invoke("set_walk_overlay", { visible: true })
        .then(() => window.__TAURI__.event.emit("argos://walk", this.event))
        .catch(() => {});
    } else {
      window.location.href = `walk.html#${encodeURIComponent(JSON.stringify(this.event))}`;
    }
  }
}

/** Wire the interactions from docs/plan/08 section 4. */
function bindInteractions(app) {
  const canvas = document.getElementById("dog");

  canvas.addEventListener("click", () => {
    app.pet();
    app.toggleBubble();
  });
  canvas.addEventListener("dblclick", () => app.walk());

  // Following the cursor is what makes the window feel occupied rather than
  // decorated. Tracked on the whole page so the dog notices a hand approaching
  // before it arrives, not only once it is on top of the sprite.
  document.addEventListener("mousemove", (event) => {
    app.pointer = event.clientX - app.floor.getBoundingClientRect().left;
  });
  document.addEventListener("mouseleave", () => {
    app.pointer = null;
  });

  window.addEventListener("resize", () => {
    app.layout();
    app.x = Math.min(app.x, app.span);
  });

  document.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    app.menu.classList.add("shown");
  });

  app.menu.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) {
      return;
    }
    app.menu.classList.remove("shown");
    sendCommand(button.dataset.cmd, { entity: app.event.entity || null });
  });

  document.addEventListener("click", (event) => {
    if (!app.menu.contains(event.target)) {
      app.menu.classList.remove("shown");
    }
  });

  // Dragging a frameless window is a window-manager operation, not a CSS one.
  canvas.addEventListener("mousedown", (event) => {
    if (event.button !== 0 || !window.__TAURI__) {
      return;
    }
    window.__TAURI__.window
      .getCurrentWindow()
      .startDragging()
      .catch(() => {});
  });

  // A dropped file is a path, and only the window API knows it: an HTML5 File
  // object deliberately does not expose one. Wrapped because this is the API
  // most likely to move between Tauri versions, and a pet that cannot accept a
  // drop must still run.
  if (window.__TAURI__) {
    try {
      window.__TAURI__.webviewWindow.getCurrentWebviewWindow().onDragDropEvent((event) => {
        const payload = event.payload || {};
        if (payload.type === "drop" && payload.paths && payload.paths.length) {
          sendCommand("drop", { path: payload.paths[0] });
        }
      });
    } catch (error) {
      console.warn("argos: drag and drop unavailable", error);
    }
  }
}

async function main() {
  const frames = await window.ArgosSprites.load();
  const app = new Argos(document.getElementById("dog"), frames);
  bindInteractions(app);
  await connect((event) => app.apply(event));
  const loop = (now) => {
    app.tick(now);
    window.requestAnimationFrame(loop);
  };
  window.requestAnimationFrame(loop);
}

main();
