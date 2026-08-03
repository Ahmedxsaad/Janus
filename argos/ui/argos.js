/**
 * Argos: the state machine, the interactions, and the transport.
 *
 * Everything drawn here depicts an event some producer sent. There is no timer
 * that invents activity to look busy, which is docs/plan/08 section 3 and root
 * CLAUDE.md rule 4 applied to pixels. The only motion without an event is the
 * frame cycle *within* the state the producer already put us in.
 *
 * Two transports, one renderer: inside Tauri the events arrive from the
 * producer over stdin; in a plain browser they are replayed from fixture.jsonl,
 * which is what makes every state reproducible without DataHub or Rust.
 */

/**
 * States, and how each one is drawn.
 *
 * `frames` names sprites from sprites/argos.txt, `fps` is how fast they cycle,
 * `alpha` and `dim` are the two health modifiers, and `collar` repaints the
 * blue collar. Red is state, never decoration: only a live finding sets it.
 */
const STATES = {
  patrolling: { frames: ["idle_a", "idle_b"], fps: 1.2 },
  sniffing: { frames: ["sniff", "idle_a"], fps: 3 },
  narrating: { frames: ["tilt", "idle_a"], fps: 1 },
  barking: { frames: ["alert_a", "alert_b"], fps: 6, collar: "r" },
  scribbling: { frames: ["scribble"], fps: 2, bob: true },
  tugging: { frames: ["tug", "idle_a"], fps: 4 },
  asleep: { frames: ["sleep"], fps: 0.5, bob: true },
  sick: { frames: ["idle_a", "idle_b"], fps: 0.6, dim: true },
  ghost: { frames: ["idle_a"], fps: 0.5, alpha: 0.35 },
};

const DEFAULT_STATE = "patrolling";

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
  window.setInterval(step, 3200);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

class Argos {
  constructor(canvas, frames) {
    this.ctx = canvas.getContext("2d");
    this.frames = frames;
    this.scale = canvas.width / window.ArgosSprites.PIXELS;
    this.size = canvas.width;
    this.state = DEFAULT_STATE;
    this.event = { v: 1, state: DEFAULT_STATE };
    this.frame = 0;
    this.last = 0;
    this.bubble = document.getElementById("bubble");
    this.menu = document.getElementById("menu");
  }

  /** Apply an event: the state it names is the only thing that moves us. */
  apply(event) {
    this.event = event;
    // An unknown state renders as patrolling rather than throwing: a newer
    // producer must never break an older window (docs/plan/08 section 6).
    const next = STATES[event.state] ? event.state : DEFAULT_STATE;
    if (next !== this.state) {
      this.state = next;
      this.frame = 0;
    }
    if (event.title) {
      this.showBubble();
    }
  }

  showBubble() {
    const event = this.event;
    if (!event.title) {
      return;
    }
    const severity = (event.severity || "").toLowerCase();
    const tag = severity ? `<span class="sev ${severity}">${severity}</span> ` : "";
    this.bubble.innerHTML = `${tag}${escapeHtml(event.title)}`;
    this.bubble.style.display = "block";
  }

  toggleBubble() {
    const shown = this.bubble.style.display === "block";
    this.bubble.style.display = "none";
    if (!shown) {
      this.showBubble();
    }
  }

  tick(now) {
    const spec = STATES[this.state];
    if (now - this.last >= 1000 / spec.fps) {
      this.last = now;
      this.frame = (this.frame + 1) % spec.frames.length;
    }
    this.ctx.clearRect(0, 0, this.size, this.size);
    window.ArgosSprites.drawFrame(
      this.ctx,
      this.frames[spec.frames[this.frame]] || this.frames.idle_a,
      this.scale,
      {
        alpha: spec.alpha,
        dim: spec.dim,
        collar: spec.collar,
        bob: spec.bob ? (Math.floor(now / 700) % 2) * 0.5 : 0,
      },
    );
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
      window.location.href = `walk.html#${encodeURIComponent(
        JSON.stringify(this.event),
      )}`;
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
    app.menu.style.display = "block";
  });

  app.menu.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) {
      return;
    }
    app.menu.style.display = "none";
    sendCommand(button.dataset.cmd, { entity: app.event.entity || null });
  });

  document.addEventListener("click", (event) => {
    if (!app.menu.contains(event.target)) {
      app.menu.style.display = "none";
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
