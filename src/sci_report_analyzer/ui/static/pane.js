// The side panel in its own window (the pane): what its tabs ask of the PDF (vrPdf, vrDoc) goes
// to the PDF window, through the channel of the browser named after its token (see vrPane
// there); the actions of the PDF window's shortcuts and links come from it.
// Its configuration: window.vrConfig (token, texts), set by the page.
window.vrPane = {
  token: vrConfig.token, texts: vrConfig.texts, channel: null, asked: 0, waiting: {}, done: false,
  start() {
    if (!window.BroadcastChannel) { this.status(this.texts.closed); return; }
    if (this.channel) return;
    this.channel = new BroadcastChannel('vr-pane-' + this.token);
    this.channel.onmessage = (ev) => this.got(ev.data || {});
    this.post({kind: 'hello'});
    window.addEventListener('pagehide', () => this.post({kind: 'bye'}));
  },
  post(m) { if (this.channel) this.channel.postMessage({...m, from: 'pane'}); },
  got(m) {
    if (m.from !== 'pdf' || this.done) return;
    if (m.kind === 'hello') { this.post({kind: 'here'}); this.status(''); }
    else if (m.kind === 'here') this.status('');
    else if (m.kind === 'bye') this.status(this.texts.closed);
    else if (m.kind === 'close') this.back();
    else if (m.kind === 'emit') emitEvent(m.name, m.args);
    else if (m.kind === 'answer' && this.waiting[m.id]) this.waiting[m.id](m.result);
  },
  // A question to the PDF window (one of vrPdf's: the selection…): its answer, else null.
  ask(what) {
    const id = ++this.asked;
    return new Promise((resolve) => {
      const done = (result) => { delete this.waiting[id]; resolve(result); };
      this.waiting[id] = done;
      setTimeout(() => this.waiting[id] && done(null), 700);
      this.post({kind: 'ask', id, what});
    });
  },
  // The side panel back in the PDF window (this one closed, if the browser lets it).
  back() {
    this.post({kind: 'bye'});
    this.done = true;
    this.status(this.texts.back);
    window.close();
  },
  status(text) {
    const e = document.getElementById('vr-pane-status');
    if (e) e.textContent = text;
  },
};
window.vrPdf = {
  go(p, y) { vrPane.post({kind: 'go', p, y}); },
  areaMode() { vrPane.post({kind: 'area'}); },
  unhighlight(rects, p) { vrPane.post({kind: 'unhighlight', rects, p}); },
  selection() { return vrPane.ask('selection'); },
  highlighted() { return vrPane.ask('highlighted'); },
  excerpted() { return vrPane.ask('excerpted'); },
  location() { return vrPane.ask('location'); },
  // Selected in the PDF, else on this page (but in a note's editor).
  async quoted() {
    const sel = await vrPane.ask('quoted');
    if (sel && sel.text) return sel;
    const s = window.getSelection(), text = s && !s.isCollapsed ? s.toString().trim() : '';
    const n = s && s.anchorNode, at = n && (n.nodeType === 1 ? n : n.parentElement);
    return text && !(at && at.closest('.cm-editor')) ? {text, p: null} : null;
  },
};
window.vrDoc = {
  mark(x) { vrPane.post({kind: 'doc', what: 'mark', arg: x}); },
  show(x) { vrPane.post({kind: 'doc', what: 'show', arg: x}); },
  select(x) { vrPane.post({kind: 'doc', what: 'select', arg: x}); },
};
// A link (e.g. a quote's, in the notes) to the PDF shown: there, in the PDF window; to another
// page: in a new one (this window kept for the side panel).
document.addEventListener('click', (ev) => {
  const a = ev.target.closest && ev.target.closest('a[href]');
  if (!a || ev.defaultPrevented || a.target === '_blank') return;
  const u = new URL(a.href, location.href);
  if (u.origin !== location.origin || u.hash === '#') return;
  ev.preventDefault();
  const page = parseInt(u.searchParams.get('page'));
  if (u.pathname === location.pathname && page) vrPdf.go(page, null);
  else window.open(u.href, '_blank');
}, true);
// The PDF window's shortcuts on the selection, here too (but when typing).
document.addEventListener('keydown', (ev) => {
  const name = vrKeys.event(ev.key);
  if (!name || !vrKeys.plain(ev) || vrKeys.typing(ev.target)) return;
  ev.preventDefault();
  emitEvent(name);
});
