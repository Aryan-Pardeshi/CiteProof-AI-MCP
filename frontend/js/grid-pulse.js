/**
 * A fine grid in the /mcp hero that takes colour where the pointer passes and
 * lets it go a moment later, with a few cells lighting on their own.
 * Decoration only: one canvas that sleeps when nothing is lit, paused off
 * screen, and still for readers who ask for reduced motion.
 */
(function () {
  const el = document.querySelector('.mcp-grid-pulse');
  const paper = el && el.querySelector('canvas');
  const ctx = paper && paper.getContext('2d');
  if (!ctx || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const CELL = 24;
  const REACH = 2.6;
  const AMBIENT = 3;
  const MAX_LIT = 180;
  const PEAK = 0.5;
  const FADE_IN = 160;
  const FADE_OUT = 750;
  // Cells behind words go faint rather than dark: a hole reads as a fault.
  const FAINT = 0.13;
  const FADE_CELLS = 2.2;
  const PAD = 5;
  const AVOID = '.docs-breadcrumbs, .mcp-hero h1, .mcp-lede, .mcp-hero-checks, .mcp-docs-home-link';

  const cells = new Map();
  let cols = 1;
  let rows = 1;
  let width = 0;
  let height = 0;
  let clear = [];
  let blue = '';
  let red = '';
  let frame = 0;

  const easeOut = (t) => 1 - (1 - t) ** 2;
  const easeIn = (t) => t * t;

  // Protect the lines of text, not their boxes: a short last line leaves room.
  function measureText() {
    const bounds = el.getBoundingClientRect();
    clear = Array.from(el.parentElement.querySelectorAll(AVOID)).flatMap((node) => {
      const range = document.createRange();
      range.selectNodeContents(node);
      const lines = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
      return (lines.length ? lines : [node.getBoundingClientRect()]).map((r) => ({
        left: r.left - bounds.left - PAD,
        top: r.top - bounds.top - PAD,
        right: r.right - bounds.left + PAD,
        bottom: r.bottom - bounds.top + PAD
      }));
    });
  }

  function measure() {
    width = el.clientWidth;
    height = el.clientHeight;
    cols = Math.max(1, Math.ceil(width / CELL));
    rows = Math.max(1, Math.ceil(height / CELL));
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    paper.width = Math.round(width * dpr);
    paper.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const styles = getComputedStyle(document.documentElement);
    blue = styles.getPropertyValue('--cp-link').trim();
    red = styles.getPropertyValue('--cp-error').trim();
    measureText();
    wake();
  }

  function brightness(col, row) {
    const x = col * CELL + CELL / 2;
    const y = row * CELL + CELL / 2;
    let nearest = Infinity;
    for (const r of clear) {
      const dx = Math.max(r.left - x, 0, x - r.right);
      const dy = Math.max(r.top - y, 0, y - r.bottom);
      nearest = Math.min(nearest, Math.hypot(dx, dy));
      if (nearest === 0) break;
    }
    if (nearest === Infinity) return 1;
    return FAINT + (1 - FAINT) * Math.min(1, nearest / (FADE_CELLS * CELL));
  }

  // Each cell is one brand colour, never a mix: blue up top, red further down.
  const ink = (row) => (Math.random() < row / Math.max(1, rows - 1) ? red : blue);

  function draw(now) {
    frame = 0;
    ctx.clearRect(0, 0, width, height);
    cells.forEach((c, key) => {
      let alpha;
      if (now < c.until) {
        alpha = easeOut(Math.min(1, Math.max(0, now - c.born) / FADE_IN));
      } else {
        const t = (now - c.until) / FADE_OUT;
        if (t >= 1) {
          cells.delete(key);
          return;
        }
        alpha = 1 - easeIn(t);
      }
      ctx.globalAlpha = alpha * c.dim * PEAK;
      ctx.fillStyle = c.colour;
      // Inset by the hairline, so the grid still shows between lit cells.
      ctx.fillRect(c.col * CELL + 1, c.row * CELL + 1, CELL - 1, CELL - 1);
    });
    ctx.globalAlpha = 1;
    if (cells.size) frame = requestAnimationFrame(draw);
  }

  function wake() {
    if (!frame) frame = requestAnimationFrame(draw);
  }

  function light(col, row, hold) {
    if (col < 0 || row < 0 || col >= cols || row >= rows || cells.size >= MAX_LIT) return;
    const key = col + ',' + row;
    const now = performance.now();
    const lit = cells.get(key);
    if (lit && now < lit.until) return;
    // A cell caught again while fading picks up from where it had got to.
    let born = now;
    if (lit) {
      const faded = 1 - easeIn(Math.min(1, (now - lit.until) / FADE_OUT));
      born = now - (1 - Math.sqrt(1 - faded)) * FADE_IN;
    }
    cells.set(key, { col, row, colour: lit ? lit.colour : ink(row), dim: brightness(col, row), born, until: now + hold });
    wake();
  }

  // Cells further from the pointer catch less often, so the trail's edge breaks up.
  let pending = 0;
  let at = null;
  function paint() {
    pending = 0;
    if (!at) return;
    const cx = Math.floor(at.x / CELL);
    const cy = Math.floor(at.y / CELL);
    const span = Math.ceil(REACH);
    for (let dy = -span; dy <= span; dy++) {
      for (let dx = -span; dx <= span; dx++) {
        const away = Math.hypot(dx, dy);
        if (away > REACH || Math.random() > 1 - away / (REACH + 0.6)) continue;
        light(cx + dx, cy + dy, 260 + Math.random() * 900);
      }
    }
  }

  // On the window, because the grid sits under the content and never gets the pointer.
  window.addEventListener('pointermove', (event) => {
    const bounds = el.getBoundingClientRect();
    at = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
    if (!pending) pending = requestAnimationFrame(paint);
  }, { passive: true });

  let visible = true;
  (function drift() {
    setTimeout(drift, 1400 + Math.random() * 1800);
    if (!visible || document.hidden) return;
    for (let i = 0; i < AMBIENT; i++) {
      light(Math.floor(Math.random() * cols), Math.floor(Math.random() * rows), 900 + Math.random() * 1600);
    }
  })();

  new IntersectionObserver((entries) => {
    visible = entries[0].isIntersecting;
  }).observe(el);
  new ResizeObserver(measure).observe(el);
  measure();
  // Text settles once the web fonts land and the hero entrance has finished.
  if (document.fonts) document.fonts.ready.then(measureText).catch(() => {});
  setTimeout(measureText, 1500);
})();
