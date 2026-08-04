/**
 * The blast-radius walk: one screen-hop per graph-hop, across the whole desktop.
 *
 * This is the interaction the project is worth building for (docs/plan/08
 * section 4). The path is not computed here and never could be: it arrives on
 * the event as `path`, straight out of detect/blast_radius.py, which is the
 * column-level traversal the benchmarks measure. This file only animates it.
 *
 * It runs in its own maximised, click-through window rather than in the pet
 * window, because repositioning a 176px always-on-top window once per frame is
 * jank that some window managers rate-limit.
 */

const HOP_MS = 900;
const PAUSE_MS = 700;
const SCALE = 5;
const SPRITE_PX = window.ArgosSprites.PIXELS * SCALE;

/** Four frames, so a hop looks like running rather than like sliding. */
const WALK_CYCLE = ["walk_a", "walk_b", "walk_c", "walk_d"];

/** Shorten a URN to the part a human reads: the entity name. */
function entityLabel(urn) {
  if (!urn) {
    return "";
  }
  const inner = urn.slice(urn.indexOf("(") + 1, urn.lastIndexOf(")"));
  const parts = (inner || urn).split(",");
  const name = parts.length > 1 ? parts[1] : parts[0];
  return name.split(".").pop() || name;
}

class Walk {
  constructor(canvas, frames) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.frames = frames;
    this.path = [];
    this.title = "";
    this.startedAt = 0;
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  resize() {
    this.canvas.width = window.innerWidth;
    this.canvas.height = window.innerHeight;
  }

  /** Begin the walk for one event. Ignores events with nothing to walk. */
  start(event) {
    const path = Array.isArray(event.path) ? event.path : [];
    if (path.length < 2) {
      this.finish();
      return;
    }
    this.path = path;
    this.title = event.title || "";
    this.startedAt = performance.now();
  }

  /** Where a hop's node sits on screen: evenly spaced, on a gentle arc. */
  nodeAt(index) {
    const width = this.canvas.width;
    const height = this.canvas.height;
    const span = width - 240;
    const x = 120 + (span * index) / Math.max(1, this.path.length - 1);
    const wave = Math.sin((index / Math.max(1, this.path.length - 1)) * Math.PI);
    return { x, y: height * 0.55 - wave * height * 0.12 };
  }

  /** The dashed line the walk follows, drawn behind everything else. */
  drawTrail(upTo) {
    const ctx = this.ctx;
    ctx.save();
    ctx.setLineDash([6, 8]);
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(24, 87, 210, 0.55)";
    ctx.beginPath();
    for (let index = 0; index <= upTo; index += 1) {
      const { x, y } = this.nodeAt(index);
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
    ctx.restore();
  }

  /** The finding this walk belongs to, once, along the top. */
  drawBanner() {
    if (!this.title) {
      return;
    }
    const ctx = this.ctx;
    ctx.font = "15px ui-monospace, monospace";
    ctx.textAlign = "center";
    const width = ctx.measureText(this.title).width + 34;
    const x = this.canvas.width / 2;
    ctx.fillStyle = "#12233f";
    ctx.fillRect(x - width / 2, 34, width, 30);
    ctx.fillStyle = "#e90101";
    ctx.fillRect(x - width / 2, 34, 5, 30);
    ctx.fillStyle = "#f7f7f7";
    ctx.fillText(this.title, x + 2, 54);
  }

  drawNode(index) {
    const { x, y } = this.nodeAt(index);
    const hop = this.path[index];
    const ctx = this.ctx;
    const name = entityLabel(hop.urn);
    const column = hop.column || "";
    const top = y + SPRITE_PX * 0.55;

    ctx.font = "13px ui-monospace, monospace";
    ctx.textAlign = "center";
    // Sized from the text rather than a guessed width: a clipped entity name is
    // the one thing on this screen a person needs to read.
    const width = Math.max(ctx.measureText(name).width, ctx.measureText(column).width) + 20;
    const height = column ? 36 : 24;
    // A dot on the line, then the card under it: the dot is what ties the label
    // to the path when the cards sit at different heights.
    ctx.fillStyle = "#1857d2";
    ctx.fillRect(x - 4, y - 4, 8, 8);
    ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
    ctx.fillRect(x - width / 2 + 3, top + 3, width, height);
    ctx.fillStyle = "#12233f";
    ctx.fillRect(x - width / 2, top, width, height);
    ctx.fillStyle = "#f7f7f7";
    ctx.fillText(name, x, top + 16);
    if (column) {
      ctx.fillStyle = "#f39f19";
      ctx.fillText(column, x, top + 30);
    }
  }

  tick(now) {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    if (!this.path.length) {
      return;
    }

    const elapsed = now - this.startedAt;
    const hops = this.path.length - 1;
    const cycle = HOP_MS + PAUSE_MS;
    const hop = Math.min(hops, Math.floor(elapsed / cycle));
    const within = Math.min(1, (elapsed % cycle) / HOP_MS);

    const reached = Math.min(hop + 1, hops);
    this.drawTrail(reached);
    this.drawBanner();
    for (let index = 0; index <= reached; index += 1) {
      this.drawNode(index);
    }

    const from = this.nodeAt(hop);
    const to = this.nodeAt(Math.min(hop + 1, hops));
    const x = from.x + (to.x - from.x) * within;
    // A hop is a hop: the dog leaves the ground between two nodes.
    const y = from.y + (to.y - from.y) * within - Math.sin(within * Math.PI) * 60;
    const moving = within > 0 && within < 1 && hop < hops;

    window.ArgosSprites.drawFrame(
      this.ctx,
      this.frames[moving ? WALK_CYCLE[Math.floor(now / 110) % WALK_CYCLE.length] : "alert_b"],
      SCALE,
      { x: x - SPRITE_PX / 2, y: y - SPRITE_PX / 2, collar: "r" },
    );

    if (elapsed > hops * cycle + PAUSE_MS * 2) {
      this.finish();
    }
  }

  /** Clear the path and put the overlay away. */
  finish() {
    this.path = [];
    if (window.__TAURI__) {
      window.__TAURI__.core
        .invoke("set_walk_overlay", { visible: false })
        .catch(() => {});
    }
  }
}

async function main() {
  const frames = await window.ArgosSprites.load();
  const walk = new Walk(document.getElementById("stage"), frames);

  if (window.__TAURI__) {
    await window.__TAURI__.event.listen("argos://walk", (message) => {
      walk.start(message.payload);
    });
  } else if (window.location.hash.length > 1) {
    // The browser demo hands the event over in the fragment, which keeps this
    // page working with no producer and no Tauri.
    walk.start(JSON.parse(decodeURIComponent(window.location.hash.slice(1))));
  }

  const loop = (now) => {
    walk.tick(now);
    window.requestAnimationFrame(loop);
  };
  window.requestAnimationFrame(loop);
}

main();
