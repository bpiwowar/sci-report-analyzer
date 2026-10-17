// A person's page (person.py): the PDFs open beside it (pdf_dock.py), each in a frame, one
// shown. What the page's notes and excerpts ask of a PDF (vrPdf, vrDoc) goes to the one shown;
// what its frame sends (its shortcuts, its excerpts clicked) comes here while its own side
// column is hidden; a link to a PDF (a document's, a paper's) opens it here.
window.vrDock = {
  shown: null,  // (the key of the PDF shown: "d3", a document; "p12", a paper)
  // The events of a PDF's frame taken here (see vrPane.emit there): its shortcuts (Q, E, F,
  // L, B) and its excerpts clicked.
  takes: ['vr-pdf-quote', 'vr-pdf-quote-inline', 'vr-pdf-excerpt', 'vr-doc-excerpt',
          'vr-pdf-find', 'vr-pdf-tag-list', 'vr-pdf-bookmark'],
  win() {
    const f = this.shown && document.querySelector(`iframe[data-vr-dock="${this.shown}"]`);
    return f && f.contentWindow;
  },
  emit(name, args) { emitEvent(name, args); },
  // The PDF of a URL of this site (/doc/3, /pdf/12?page=4), opened here: whether it is one.
  openUrl(href) {
    const u = new URL(href, location.href);
    const m = u.origin === location.origin && u.pathname.match(/^\/(doc|pdf)\/(\d+)$/);
    if (!m || u.searchParams.get('fetch') || u.searchParams.get('pane')) return false;
    const key = (m[1] === 'doc' ? 'd' : 'p') + m[2], page = parseInt(u.searchParams.get('page'));
    if (key === this.shown && page) {
      const w = this.win();
      if (w && w.vrPdf) { w.vrPdf.go(page, null); return true; }
    }
    emitEvent('vr-dock-open', {key, page: page || null});
    return true;
  },
  // A click on a link to a PDF (but with a modifier: as usual, e.g. a new tab).
  click(ev) {
    if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey
        || ev.altKey) return;
    const a = ev.target.closest && ev.target.closest('a[href]');
    if (!a || a.getAttribute('href').startsWith('#') || !this.openUrl(a.href)) return;
    ev.preventDefault();
    ev.stopPropagation();
  },
};
document.addEventListener('click', (ev) => vrDock.click(ev), true);
// (the PDF shown: its functions, else nothing)
const vrDockProxy = (name) => new Proxy({}, {
  get: (_t, k) => (...args) => {
    const w = vrDock.win(), o = w && w[name];
    return o && typeof o[k] === 'function' ? o[k](...args) : null;
  },
});
window.vrPdf = vrDockProxy('vrPdf');
window.vrDoc = vrDockProxy('vrDoc');

// Dragging the inner edge of the PDFs (the left one; the right one, on the left) resizes them (the frames covered meanwhile, else they
// swallow the mouse events); the width is sent on release.
document.addEventListener('mousedown', (ev) => {
  const bar = ev.target.closest && ev.target.closest('.vr-dock-splitter');
  if (!bar) return;
  ev.preventDefault();
  const cover = document.createElement('div');
  cover.style.cssText = 'position:fixed;inset:0;z-index:9999;cursor:col-resize';
  document.body.appendChild(cover);
  const left = vrConfig.dockLeft;
  const width = (x) => Math.round(Math.max(vrConfig.dockMin, Math.min(left ? x : innerWidth - x,
    innerWidth * 0.8)));
  const line = document.createElement('div');
  line.style.cssText = 'position:fixed;top:0;bottom:0;width:3px;z-index:10000;'
    + 'background:var(--q-primary);pointer-events:none';
  document.body.appendChild(line);
  const move = (e) => {
    const w = width(e.clientX);
    line.style.left = (left ? w : innerWidth - w) + 'px';
  };
  move(ev);
  const up = (e) => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    cover.remove(); line.remove();
    emitEvent('vr-dock-width', width(e.clientX));
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
});
