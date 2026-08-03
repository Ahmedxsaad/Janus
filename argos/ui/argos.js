/**
 * Argos: the state machine, the interactions, and the transport.
 *
 * Everything drawn here depicts an event some producer sent. There is no timer
 * that invents activity to look busy, which is docs/plan/08 section 3 and root
 * CLAUDE.md rule 4 applied to pixels. The only motion without an event is the
 * frame cycle *within* the state the producer already put us in: a dog that
 * breathes and blinks while patrolling is not claiming anything about the graph.
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
 * and `shadow` is how firmly the dog is planted. Red is state, never
 * decoration: only a live finding sets it.
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
  },
  sniffing: { timeline: [["sniff_a", 320], ["sniff_b", 300]] },
  narrating: { timeline: [["tilt_a", 900], ["tilt_b", 950]] },
  barking: {
    timeline: [["alert_a", 150], ["alert_b", 170]],
    collar: "r",
    shake: true,
  },
  scribbling: { timeline: [["scribble_a", 260], ["scribble_b", 280]] },
  tugging: { timeline: [["tug_a", 220], ["tug_b", 240]] },
  asleep: { timeline: [["sleep_a", 1500], ["sleep_b", 1600]] },
  recovered: { timeline: [["wag_a", 150], ["wag_b", 160]] },
  unchecked: { timeline: [["search_a", 750], ["search_b", 800]] },
  muted: { timeline: [["sit", 2400], ["blink", 140]], dim: true },
  sick: { timeline: [["idle_a", 1800], ["blink", 220]], dim: true },
  ghost: { timeline: [["idle_a", 1200], ["idle_b", 1200]], alpha: 0.35 },
};

const DEFAULT_STATE = "patrolling";

/** How long a bubble stays up before it gets out of the way. */
const BUBBLE_MS = 9000;

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
    this.hideAt = 0;
    for (let index = 0; index < 10; index += 1) {
      this.trust.appendChild(document.createElement("i"));
    }
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
      score === null ? "" : band === "at-risk" ? "shown risk" : band === "watch" ? "shown watch" : "shown";
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

  /** Advance the timeline and draw one frame. */
  tick(now) {
    const spec = STATES[this.state];
    const [, hold] = spec.timeline[this.step];
    if (now - this.stepAt >= hold) {
      this.stepAt = now;
      this.step = (this.step + 1) % spec.timeline.length;
    }
    if (this.hideAt && now > this.hideAt) {
      this.bubble.classList.remove("shown");
      this.hideAt = 0;
    }

    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.size, this.size);
    this.drawShadow(spec, now);

    // Entry squash: 120ms of a flattened, wider sprite settling back to square.
    const since = now - this.enteredAt;
    const squash = since < 180 ? Math.sin((since / 180) * Math.PI) * 0.09 : 0;
    const shake = spec.shake ? Math.round(Math.sin(now / 45) * 1.2) : 0;

    ctx.save();
    ctx.translate(this.size / 2 + shake, this.size);
    ctx.scale(1 + squash, 1 - squash);
    ctx.translate(-this.size / 2, -this.size);
    window.ArgosSprites.drawFrame(ctx, this.frames[spec.timeline[this.step][0]], this.scale, {
      alpha: spec.alpha,
      dim: spec.dim,
      collar: spec.collar,
    });
    ctx.restore();
  }

  /** The ellipse that puts the dog on the desktop instead of above it. */
  drawShadow(spec, now) {
    const ctx = this.ctx;
    const breathe = 1 + Math.sin(now / 900) * 0.03;
    ctx.save();
    ctx.globalAlpha = (spec.alpha === undefined ? 1 : spec.alpha) * 0.22;
    ctx.fillStyle = "#000";
    ctx.beginPath();
    ctx.ellipse(
      this.size / 2,
      this.size - this.scale * 1.6,
      this.size * 0.3 * breathe,
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

  canvas.addEventListener("click", () => app.toggleBubble());
  canvas.addEventListener("dblclick", () => app.walk());

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
    window.__TAURI__.window.getCurrentWindow().startDragging().catch(() => {});
  });

  // A dropped file is a path, and only the window API knows it: an HTML5 File
  // object deliberately does not expose one. Wrapped because this is the API
  // most likely to move between Tauri versions, and a pet that cannot accept a
  // drop must still run.
  if (window.__TAURI__) {
    try {
      window.__TAURI__.webviewWindow
        .getCurrentWebviewWindow()
        .onDragDropEvent((event) => {
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
