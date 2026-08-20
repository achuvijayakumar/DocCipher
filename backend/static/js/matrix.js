/* Matrix rain background. Pauses when the tab is hidden and respects
   prefers-reduced-motion. Purely decorative. */
(function () {
  const canvas = document.getElementById('matrix');
  if (!canvas) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const ctx = canvas.getContext('2d');
  const GLYPHS = '01アイウエオカキクケコサシスセソタチツテトナニヌネノ$#%&@'.split('');
  const FONT_SIZE = 14;
  let drops = [];
  let raf = null;
  let last = 0;

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const columns = Math.ceil(canvas.width / FONT_SIZE);
    drops = Array.from({ length: columns }, () => Math.random() * -50);
    ctx.font = FONT_SIZE + 'px monospace';
  }

  function frame(now) {
    raf = requestAnimationFrame(frame);
    if (now - last < 55) return;   // ~18fps is plenty for rain
    last = now;

    ctx.fillStyle = 'rgba(5, 7, 10, 0.08)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.font = FONT_SIZE + 'px monospace';

    for (let i = 0; i < drops.length; i++) {
      const glyph = GLYPHS[(Math.random() * GLYPHS.length) | 0];
      const y = drops[i] * FONT_SIZE;
      // Leading character is brighter, giving the trail a head.
      ctx.fillStyle = Math.random() > 0.97 ? '#ccffdd' : '#00ff41';
      ctx.fillText(glyph, i * FONT_SIZE, y);

      if (y > canvas.height && Math.random() > 0.975) drops[i] = 0;
      drops[i]++;
    }
  }

  function start() { if (raf === null) raf = requestAnimationFrame(frame); }
  function stop() { if (raf !== null) { cancelAnimationFrame(raf); raf = null; } }

  window.addEventListener('resize', resize);
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));

  resize();
  start();
})();
