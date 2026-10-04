// The PDF window (pdf_viewer.py): PDF.js's viewer in a frame, the side panel next to it.
// Its configuration: window.vrConfig (url, texts, sideMin), set by the page.

// Dragging the splitter between the PDF and the side column resizes the latter (the iframe
// is covered meanwhile, else it swallows the mouse events); its width is sent on release.
document.addEventListener('mousedown', (ev) => {
  const bar = ev.target.closest && ev.target.closest('#vr-pdf-splitter');
  if (!bar) return;
  ev.preventDefault();
  const side = document.getElementById('vr-pdf-side'), row = side.parentElement;
  const cover = document.createElement('div');
  cover.style.cssText = 'position:fixed;inset:0;z-index:9999;cursor:col-resize';
  document.body.appendChild(cover);
  const width = (x) => Math.round(Math.max(vrConfig.sideMin,
    Math.min(row.getBoundingClientRect().right - x, row.clientWidth * 0.7)));
  const move = (e) => { side.style.width = width(e.clientX) + 'px'; };
  const up = (e) => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    cover.remove();
    emitEvent('vr-pdf-side-width', width(e.clientX));
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
});

// Saves the edits back (Ctrl+S, the viewer's save button, and every few seconds), and turns
// on the highlight button of a text selection; where the reader is (bookmarks), and what is
// selected (to find its paper).
window.vrPdf = {
  url: vrConfig.url, texts: vrConfig.texts, saving: false, saved: null,
  win() {
    const f = document.getElementById('vr-pdf-frame');
    return f && f.contentWindow;
  },
  app() {
    const w = this.win();
    return w && w.PDFViewerApplication;
  },
  dirty() { return this.saved !== null && this.hash() !== this.saved; },
  status(text) { const e = document.getElementById('vr-pdf-status'); if (e) e.textContent = text; },
  hash() {
    const a = this.app();
    return a && a.pdfDocument ? a.pdfDocument.annotationStorage.serializable.hash : null;
  },
  async save(now) {
    const a = this.app();
    if (!a || !a.pdfDocument || this.saving) return;
    if (now) a.pdfViewer._layerProperties?.annotationEditorUIManager?.endCurrentEditing();
    const hash = this.hash();
    if (!now && (this.saved === null || hash === this.saved)) return;
    this.saving = true; this.status(this.texts.saving);
    try {
      const data = await a.pdfDocument.saveDocument();
      const res = await fetch(this.url, {
        method: 'PUT', body: data, headers: {'Content-Type': 'application/pdf'},
      });
      if (!res.ok) throw new Error(await res.text());
      this.saved = hash;
      if (this.hash() === hash) a.pdfDocument.annotationStorage.resetModified();
      this.status(this.texts.saved.replace('{time}', new Date().toLocaleTimeString()));
    } catch (e) {
      this.status(this.texts.failed.replace('{error}', e.message));
    } finally { this.saving = false; }
  },
  // Back and forward (the viewer's history): the arrows, disabled when there is nowhere to go.
  // (A link followed after going back drops the steps forward: the last pushed is the last.)
  maxUid: 0,
  nav() {
    const w = this.win(), s = w && w.history.state;
    const uid = s && Number.isInteger(s.uid) ? s.uid : null;
    if (uid !== null) this.maxUid = Math.max(this.maxUid, uid);
    for (const [step, ok] of [['back', uid > 0], ['forward', uid !== null && uid < this.maxUid]]) {
      const b = document.getElementById('vr-pdf-' + step);
      if (b) { b.disabled = !ok; b.classList.toggle('disabled', !ok); }
    }
  },
  // Where the reader is (the selection, else the top of the view): page, y (PDF units).
  location() {
    const a = this.app();
    if (!a || !a.pdfViewer) return null;
    const loc = a.pdfViewer._location;
    const sel = this.selection();
    if (sel && sel.p && sel.rects.length) {  // (at the selection)
      const ys = sel.rects.filter(r => (r[4] ?? sel.p) === sel.p).map(r => r[3]);
      return {p: sel.p, y: Math.max(...ys) + 20, text: sel.text};
    }
    return {p: loc ? loc.pageNumber : a.page, y: loc ? loc.top : null, text: ''};
  },
  // Where the reader is (page, zoom, scroll), kept once they stop moving: the PDF opens there
  // again (see documents.last_place). (PDF.js keeps it too, in the browser, but by the file's
  // fingerprint: lost when the file is saved.)
  at: null, atTimer: null,
  moved(loc) {
    if (!loc || !this.app()?.isInitialViewSet) return;  // (not the first page, while loading)
    this.at = {p: loc.pageNumber, zoom: String(loc.scale), left: Math.round(loc.left),
               top: Math.round(loc.top)};
    clearTimeout(this.atTimer);
    this.atTimer = setTimeout(() => this.keepAt(), 1000);
  },
  keepAt() {
    clearTimeout(this.atTimer);
    if (this.at) emitEvent('vr-pdf-at', this.at);
    this.at = null;
  },
  // The text to quote in a note: selected in the PDF (or a highlight clicked), else on the
  // page (but in a note's editor).
  quoted() {
    const sel = this.selection() || this.highlighted();
    if (sel && sel.text) return sel;
    const s = window.getSelection(), text = s && !s.isCollapsed ? s.toString().trim() : '';
    const n = s && s.anchorNode, at = n && (n.nodeType === 1 ? n : n.parentElement);
    return text && !(at && at.closest('.cm-editor')) ? {text, p: null} : null;
  },
  // Through the link service, as a link: a step back and forward (the viewer's history).
  go(p, y) {
    const a = this.app();
    if (!a) return;
    if (y === null || y === undefined) { a.pdfLinkService.goToPage(p); return; }
    a.pdfLinkService.goToDestination([p - 1, {name: 'XYZ'}, null, y, null]);
  },
  // The selected text, its page (where it starts) and its rectangles (one per line, PDF
  // units, the fifth number their page): those of the text only (never a whole page, as
  // the range's own rectangles over two pages).
  // Without one: the areas drawn (see areas), if any: their texts, in order, one per line.
  selection() {
    const w = this.win(), a = this.app();
    if (!w || !a) return null;
    const sel = w.getSelection();
    const text = sel && !sel.isCollapsed && sel.rangeCount ? sel.toString().trim() : '';
    if (!text) {
      const xs = this.areas;
      return xs.length ? {text: this.areaText(), p: xs[0].p, rects: xs.map(x => [...x.rect, x.p]),
                          area: true} : null;
    }
    const range = sel.getRangeAt(0), doc = range.startContainer.ownerDocument;
    let root = range.commonAncestorContainer;
    if (root.nodeType !== 1) root = root.parentElement;
    const rects = [];
    const walker = doc.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
    for (let n = walker.currentNode.nodeType === 3 ? walker.currentNode : walker.nextNode();
         n; n = walker.nextNode()) {
      if (!range.intersectsNode(n)) continue;
      const layer = n.parentElement && n.parentElement.closest('.textLayer');
      const pageDiv = layer && layer.closest('.page');
      if (!pageDiv) continue;
      const p = +pageDiv.dataset.pageNumber, pv = a.pdfViewer.getPageView(p - 1);
      const sub = doc.createRange();
      sub.setStart(n, n === range.startContainer ? range.startOffset : 0);
      sub.setEnd(n, n === range.endContainer ? range.endOffset : n.length);
      const box = layer.getBoundingClientRect();
      for (const r of sub.getClientRects()) {
        if (r.width < 1 || r.height < 1) continue;
        const [x0, y0] = pv.viewport.convertToPdfPoint(r.left - box.left, r.bottom - box.top);
        const [x1, y1] = pv.viewport.convertToPdfPoint(r.right - box.left, r.top - box.top);
        const q = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1), p];
        const last = rects[rects.length - 1];
        if (last && last[4] === p && Math.abs(last[1] - q[1]) < 2 && Math.abs(last[3] - q[3]) < 2) {
          last[0] = Math.min(last[0], q[0]); last[2] = Math.max(last[2], q[2]);
        } else rects.push(q);
      }
    }
    const start = range.startContainer.nodeType === 1
      ? range.startContainer : range.startContainer.parentElement;
    const pageDiv = start && start.closest('.page');
    const p = rects.length ? rects[0][4] : (pageDiv ? +pageDiv.dataset.pageNumber : null);
    const round = (v) => Math.round(v * 10) / 10;
    return {text, p, rects: rects.map(r => [...r.slice(0, 4).map(round), r[4]])};
  },
  // The text of a page within rectangles (PDF units): from the text layer, its characters
  // whose middle is in one of them, a line per line (e.g. a list's items: see the tagging).
  textIn(pv, rects) {
    const layer = pv && pv.div.querySelector('.textLayer');
    if (!layer) return '';
    const box = layer.getBoundingClientRect();
    const screen = rects.map(r => {  // (on the screen)
      const [x0, y0] = pv.viewport.convertToViewportPoint(r[0], r[1]);
      const [x1, y1] = pv.viewport.convertToViewportPoint(r[2], r[3]);
      return [box.left + Math.min(x0, x1), box.top + Math.min(y0, y1),
              box.left + Math.max(x0, x1), box.top + Math.max(y0, y1)];
    });
    const meets = (r) => screen.some(
      s => r.right >= s[0] && r.left <= s[2] && r.bottom >= s[1] && r.top <= s[3]);
    const inside = (x, y) => screen.some(s => x >= s[0] && x <= s[2] && y >= s[1] && y <= s[3]);
    const doc = layer.ownerDocument, range = doc.createRange();
    const walker = doc.createTreeWalker(layer, 4 /* NodeFilter.SHOW_TEXT */);
    let text = '', top = null;
    for (let node; (node = walker.nextNode());) {
      const r = node.parentElement.getBoundingClientRect();
      if (!meets(r)) continue;
      let part = '';
      for (let i = 0; i < node.length; i++) {
        range.setStart(node, i); range.setEnd(node, i + 1);
        const c = range.getBoundingClientRect();
        if (inside(c.left + c.width / 2, c.top + c.height / 2)) part += node.data[i];
      }
      if (!part) continue;
      if (top !== null && Math.abs(r.top - top) > r.height / 2) text += '\n';  // (a new line)
      text += part; top = r.top;
    }
    return text.trim();  // (its spaces: normalized when filed)
  },
  // The highlight selected (clicked) in the viewer, as selection(): the text under it (PDF.js
  // keeps a highlight's text to itself, and has none for one loaded from the file), its page
  // and rectangles.
  highlighted() {
    const a = this.app();
    const ed = a?.pdfViewer?._layerProperties?.annotationEditorUIManager?.firstSelectedEditor;
    if (!ed || ed.editorType !== 'highlight' || !ed._drawOutlines) return null;
    const q = ed._drawOutlines.serializeQuadPoints(ed.pageTranslation, ed.pageDimensions);
    if (!q || !q.length) return null;
    const rects = [];
    for (let i = 0; i < q.length; i += 8) {
      rects.push([Math.min(q[i], q[i + 2]), Math.min(q[i + 1], q[i + 5]),
                  Math.max(q[i], q[i + 2]), Math.max(q[i + 1], q[i + 5])]);
    }
    const p = ed.pageIndex + 1;
    const text = this.textIn(a.pdfViewer.getPageView(p - 1), rects);
    const round = (v) => Math.round(v * 10) / 10;
    return text ? {text, p, rects: rects.map(r => [...r.map(round), p])} : null;
  },
  // Areas of the pages (rectangles drawn with the mouse: in the area mode, or with Alt; with
  // Shift, one more), as a selection: the texts within (in the order drawn), the page of the
  // first, their rectangles (PDF units). Kept until a click elsewhere, a text selection, or
  // Escape.
  areas: [], areaOn: false, drag: null,
  areaMode(on) {
    this.areaOn = on === undefined ? !this.areaOn : on;
    const a = this.app();
    if (this.areaOn && a && a.pdfViewer && a.pdfViewer.annotationEditorMode > 0) this.mode(0);
    this.win()?.document.body.classList.toggle('vr-area-mode', this.areaOn);
    const b = document.getElementById('vr-pdf-area');
    if (b) b.style.background = this.areaOn ? 'rgba(255, 255, 255, 0.3)' : '';
  },
  areaText() { return this.areas.map(x => x.text).filter(t => t).join('\n'); },
  clearArea() {
    if (!this.areas.length) return;
    const pages = new Set(this.areas.map(x => x.p));
    this.areas = [];
    for (const p of pages) this.drawArea(this.app()?.pdfViewer?.getPageView(p - 1));
    this.actions();
  },
  // (the box of a page's viewport, on the screen)
  pageBox(pv) {
    const layer = pv.div.querySelector('.textLayer');
    if (layer) return layer.getBoundingClientRect();
    const r = pv.div.getBoundingClientRect();
    return {left: r.left + pv.div.clientLeft, top: r.top + pv.div.clientTop};
  },
  // The areas of a page, and ``rect`` (being drawn).
  drawArea(pv, rect) {
    if (!pv || !pv.div || !pv.viewport) return;
    pv.div.querySelectorAll('.vr-area').forEach(e => e.remove());
    const rects = this.areas.filter(x => x.p === pv.id).map(x => x.rect);
    if (rect) rects.push(rect);
    for (const r of rects) {
      const [x0, y0] = pv.viewport.convertToViewportPoint(r[0], r[1]);
      const [x1, y1] = pv.viewport.convertToViewportPoint(r[2], r[3]);
      const mark = pv.div.ownerDocument.createElement('div');
      mark.className = 'vr-area';
      Object.assign(mark.style, {
        position: 'absolute', left: Math.min(x0, x1) + 'px', top: Math.min(y0, y1) + 'px',
        width: Math.abs(x1 - x0) + 'px', height: Math.abs(y1 - y0) + 'px', zIndex: 5,
        pointerEvents: 'none', userSelect: 'none', border: '1px dashed #1976d2',
        background: 'rgba(25, 118, 210, 0.12)',
      });
      pv.div.appendChild(mark);
    }
  },
  // (the rectangle from where the drag started to the mouse, within the page: PDF units)
  dragRect(ev) {
    const d = this.drag, box = this.pageBox(d.pv);
    const [w, h] = [d.pv.viewport.width, d.pv.viewport.height];
    const clamp = (v, max) => Math.max(0, Math.min(max, v));
    const [x0, y0] = d.pv.viewport.convertToPdfPoint(d.x, d.y);
    const [x1, y1] = d.pv.viewport.convertToPdfPoint(
      clamp(ev.clientX - box.left, w), clamp(ev.clientY - box.top, h));
    return [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
  },
  areaDown(ev) {
    const a = this.app(), w = this.win();
    if (!a || !a.pdfViewer || ev.button !== 0) return;
    const pageDiv = ev.target.closest && ev.target.closest('.page');
    if (!pageDiv) return;  // (the viewer's toolbars: the areas kept)
    const editing = a.pdfViewer.annotationEditorMode > 0;
    const more = ev.shiftKey && this.areas.length > 0 && !editing;  // (one more area)
    if (!(this.areaOn || more || (ev.altKey && !editing))) { this.clearArea(); return; }
    ev.preventDefault(); ev.stopPropagation();
    w.getSelection().removeAllRanges();
    if (!ev.shiftKey) this.clearArea();
    const pv = a.pdfViewer.getPageView(+pageDiv.dataset.pageNumber - 1), box = this.pageBox(pv);
    this.drag = {pv, x: ev.clientX - box.left, y: ev.clientY - box.top};
  },
  areaMove(ev) {
    if (!this.drag) return;
    ev.preventDefault(); ev.stopPropagation();
    this.drawArea(this.drag.pv, this.dragRect(ev));
  },
  areaUp(ev) {
    if (!this.drag) return;
    ev.preventDefault(); ev.stopPropagation();
    const pv = this.drag.pv, rect = this.dragRect(ev);
    this.drag = null;
    if (rect[2] - rect[0] < 2 || rect[3] - rect[1] < 2) { this.drawArea(pv); return; }  // (a click)
    const round = (v) => Math.round(v * 10) / 10;
    this.areas.push({p: pv.id, rect: rect.map(round), text: this.textIn(pv, [rect])});
    this.drawArea(pv);
    this.actions();
  },
  // Copying (⌘C, Ctrl+C) the text selected in the PDF: cleaned (see pdftext.py: its lines
  // joined, hyphens and ligatures undone…); without a text selection (but in a field): the
  // areas' text, cleaned too. (The copy event, and the clipboard from the key: a browser may
  // fire no copy event then.)
  copyArea(ev) {
    const t = ev.target, doc = t && (t.ownerDocument || t);
    const w = (doc && doc.defaultView) || window;
    if (vrKeys.typing(t)) return false;
    let text = '';
    const pdf = this.win(), sel = pdf && pdf.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim()) {
      if (ev.type !== 'copy' || w !== pdf) return false;  // (copied in the PDF, at its event)
      text = vrCleanPdfText(sel.toString());
    } else {
      const own = window.getSelection();
      if (own && !own.isCollapsed && own.toString().trim()) return false;
      text = this.areas.length ? vrCleanPdfText(this.areaText()) : '';
    }
    if (!text) return false;
    if (ev.type === 'copy') {
      ev.clipboardData.setData('text/plain', text);
      ev.preventDefault(); ev.stopImmediatePropagation();
    } else (w || window).navigator.clipboard?.writeText(text).catch(() => {});
    return true;
  },
  // The actions on the selected text, greyed out without one (adding an excerpt: also with a
  // highlight selected).
  actions() {
    const sel = this.win()?.getSelection();
    const text = !!((sel && !sel.isCollapsed && sel.toString().trim()) || this.areaText());
    const ed = this.app()?.pdfViewer?._layerProperties?.annotationEditorUIManager
      ?.firstSelectedEditor;
    const hl = !!(ed && ed.editorType === 'highlight');
    for (const [id, ok] of [['vr-pdf-find', text], ['vr-pdf-tag-list', text],
                            ['vr-pdf-excerpt', text || hl]]) {
      const b = document.getElementById(id);
      if (b) { b.disabled = !ok; b.classList.toggle('disabled', !ok); }
    }
  },
  // Toggle an editing mode of the viewer (highlight, text, drawing, image, comments).
  mode(m) {
    const a = this.app();
    if (!a || !a.pdfViewer) return;
    const now = a.pdfViewer.annotationEditorMode;
    a.eventBus.dispatch('switchannotationeditormode', {source: this, mode: now === m ? 0 : m});
  },
  // What an excerpt is made of: the selected text, else the highlight clicked.
  excerpted() { return this.selection() || this.highlighted(); },
  // The highlights of PDF.js under an excerpt just filed (its rectangles, PDF units, the fifth
  // number their page, else ``p``): removed, its tint in their place (not both). A highlight
  // is under it when the middle of one of its lines is in one of its rectangles, or the other
  // way round (a highlight within a line).
  async unhighlight(rects, p) {
    const a = this.app(), ui = a?.pdfViewer?._layerProperties?.annotationEditorUIManager;
    if (!ui || !rects || !rects.length) return;
    const mid = (r) => [(r[0] + r[2]) / 2, (r[1] + r[3]) / 2];
    const inside = (pt, r) => pt[0] >= r[0] && pt[0] <= r[2] && pt[1] >= r[1] && pt[1] <= r[3];
    const lines = (q) => {  // (quadrilaterals: their boxes)
      const out = [];
      for (let i = 0; i + 7 < q.length; i += 8) {
        out.push([Math.min(q[i], q[i + 2]), Math.min(q[i + 1], q[i + 5]),
                  Math.max(q[i], q[i + 2]), Math.max(q[i + 1], q[i + 5])]);
      }
      return out;
    };
    const under = (q, page) => {
      const own = rects.filter(r => (r[4] ?? p) === page);
      return !!q && lines(q).some(l => own.some(r => inside(mid(l), r) || inside(mid(r), l)));
    };
    for (const page of new Set(rects.map(r => r[4] ?? p))) {
      if (!page) continue;
      // (highlights being edited, or added: PDF.js's editors)
      for (const ed of [...ui.getEditors(page - 1)]) {
        if (ed.editorType !== 'highlight' || !ed._drawOutlines) continue;
        if (under(ed._drawOutlines.serializeQuadPoints(ed.pageTranslation, ed.pageDimensions),
                  page)) ed.remove();
      }
      // (those of the file, not edited yet: turned into editors, removed, as PDF.js does)
      const pv = a.pdfViewer.getPageView(page - 1);
      const layer = pv?.annotationEditorLayer?.annotationEditorLayer;
      const found = pv?.annotationLayer?.annotationLayer?.getEditableAnnotations?.() || [];
      for (const el of [...found]) {
        if (!layer || el.data.annotationType !== 9 /* highlight */) continue;
        if (ui.isDeletedAnnotationElement(el.data.id) || !under(el.data.quadPoints, page)) continue;
        const ed = await layer.deserialize(el);
        if (!ed) continue;
        layer.addOrRebuild(ed);
        ed.remove();
        el.hide();
      }
    }
    this.actions();
  },
  // Highlight the selected text (else: the highlighting mode, on or off).
  highlight() {
    const ui = this.app()?.pdfViewer?._layerProperties?.annotationEditorUIManager;
    const sel = this.win()?.getSelection();
    if (ui && sel && !sel.isCollapsed && sel.toString().trim()) ui.highlightSelection('keyboard');
    else this.mode(9);
  },
  // Shortcuts (single keys, but when typing): see KEYS. (H: rather than PDF.js's hand tool.)
  keys: {
    h: () => vrPdf.highlight(),
    t: () => vrPdf.mode(3),  // (free text)
    d: () => vrPdf.mode(15),  // (drawing)
    i: () => vrPdf.mode(13),  // (an image)
    c: () => vrPdf.mode(16),  // (the comments)
    a: () => vrPdf.areaMode(),
    // (and those on the selection: vrKeys.selection)
  },
  key(ev) {
    if (ev.key === 'Escape' && (this.areas.length || this.areaOn)) {
      this.clearArea(); this.areaMode(false);
      return;
    }
    if (ev.key === 'c' && (ev.metaKey || ev.ctrlKey) && !ev.altKey && !ev.shiftKey) {
      this.copyArea(ev);  // (the copy event still fired)
      return;
    }
    const name = vrKeys.event(ev.key);
    const act = this.keys[ev.key] || (name && (() => vrPane.emit(name)));
    if (!act || !vrKeys.plain(ev) || vrKeys.typing(ev.target)) return;
    ev.preventDefault(); ev.stopPropagation();
    act();
  },
};
document.addEventListener('keydown', (ev) => vrPdf.key(ev));
document.addEventListener('copy', (ev) => vrPdf.copyArea(ev), true);
// The side panel in another window (the pane, e.g. on another screen): the two windows talk
// through a channel of the browser, named after a token of this one (kept when it is
// reloaded). This one answers the pane's questions (the selection, where the reader is), goes
// where it says and draws what it sends (vrDoc); its shortcuts and links act in the pane.
window.vrPane = {
  token: null, channel: null, away: false,  // (away: the side panel in the pane)
  start() {
    const key = 'vr-pane:' + location.pathname;
    try { this.token = sessionStorage.getItem(key); } catch (e) {}
    if (!this.token) {
      this.token = Math.random().toString(36).slice(2) + Date.now().toString(36);
      try { sessionStorage.setItem(key, this.token); } catch (e) {}
    }
    if (!window.BroadcastChannel || this.channel) return;
    this.channel = new BroadcastChannel('vr-pane-' + this.token);
    this.channel.onmessage = (ev) => this.got(ev.data || {});
    this.post({kind: 'hello'});  // (a pane still open, after a reload: the panel stays there)
    window.addEventListener('pagehide', () => this.post({kind: 'bye'}));
  },
  post(m) { if (this.channel) this.channel.postMessage({...m, from: 'pdf'}); },
  got(m) {
    if (m.from !== 'pane') return;
    if (m.kind === 'hello' || m.kind === 'here') {
      if (m.kind === 'hello') this.post({kind: 'here'});
      this.leave();
    } else if (m.kind === 'bye') this.back();
    else if (m.kind === 'go') vrPdf.go(m.p, m.y);
    else if (m.kind === 'area') vrPdf.areaMode();
    else if (m.kind === 'unhighlight') vrPdf.unhighlight(m.rects, m.p);
    else if (m.kind === 'ask') {
      const asked = ['selection', 'highlighted', 'excerpted', 'location', 'quoted'];
      let result = null;
      try { if (asked.includes(m.what)) result = vrPdf[m.what](); } catch (e) {}
      this.post({kind: 'answer', id: m.id, result});
    } else if (m.kind === 'doc' && ['mark', 'show', 'select'].includes(m.what)) {
      vrDoc[m.what](m.arg);
    }
  },
  // The header's button (the window opened from the click, else blocked as a pop-up): the
  // side panel in the pane, or back here.
  toggle() {
    if (this.away) { this.post({kind: 'close'}); this.back(); return; }
    if (!this.channel) return;
    const u = new URL(location.href);
    u.hash = ''; u.searchParams.delete('page'); u.searchParams.set('pane', this.token);
    const side = document.getElementById('vr-pdf-side');
    const width = Math.max(480, Math.round(1.2 * (side ? side.offsetWidth : 0)));
    const features = `popup,width=${width},height=${Math.round(screen.availHeight * 0.9)}`;
    if (window.open(u.href, 'vr-pane-' + this.token, features)) this.leave();
  },
  leave() { if (!this.away) { this.away = true; emitEvent('vr-pane', true); } },
  back() { if (this.away) { this.away = false; emitEvent('vr-pane', false); } },
  // An event of the side panel (a shortcut, a link clicked): to the pane while it has the
  // panel (``both``: here too).
  emit(name, args, both) {
    if (this.away) this.post({kind: 'emit', name, args});
    if (!this.away || both) emitEvent(name, args);
  },
};
// The papers found in a document (links), and the excerpts filed in categories: drawn on
// the pages; the one selected (clicked, here or in the side panel) stands out.
window.vrDoc = {
  id: null, needText: false, links: [], excerpts: [], selected: null,
  show(links) { this.links = links; this.redraw(); },
  mark(excerpts) { this.excerpts = excerpts; this.redraw(); },
  select(id) {
    if (id === this.selected) return;
    this.selected = id;
    this.redraw();
  },
  // A click on a page (not a text selected, nor an area drawn, nor while editing): the
  // excerpt tinted there selected (the next one where several overlap), its entry in the
  // side panel too; elsewhere, none. (The tints let the mouse through: text still selected.)
  clicked(ev) {
    const a = vrPdf.app(), w = vrPdf.win();
    if (!a || !a.pdfViewer || ev.button !== 0 || ev.shiftKey || ev.altKey || ev.ctrlKey
        || ev.metaKey || vrPdf.areaOn || a.pdfViewer.annotationEditorMode > 0) return;
    const sel = w && w.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim()) return;
    const t = ev.target, pageDiv = t.closest && t.closest('.page');
    if (!pageDiv || t.closest('.vr-doc, .annotationLayer a, .annotationEditorLayer > *')) return;
    const pv = a.pdfViewer.getPageView(+pageDiv.dataset.pageNumber - 1), box = vrPdf.pageBox(pv);
    const [x, y] = pv.viewport.convertToPdfPoint(ev.clientX - box.left, ev.clientY - box.top);
    const hits = this.excerpts.filter(e => e.rects.some(r => (r[4] ?? e.page) === pv.id
      && x >= r[0] - 1 && x <= r[2] + 1 && y >= r[1] - 1 && y <= r[3] + 1)).map(e => e.id);
    const id = hits.length ? hits[(hits.indexOf(this.selected) + 1) % hits.length] : null;
    if (id === this.selected) return;
    this.select(id);
    vrPane.emit('vr-doc-excerpt', {id});
  },
  redraw() {
    const a = vrPdf.app();
    if (!a || !a.pdfViewer) return;
    for (let i = 0; i < a.pdfViewer.pagesCount; i++) this.draw(a.pdfViewer.getPageView(i));
  },
  draw(pv) {
    if (!pv || !pv.div || !pv.viewport) return;
    const doc = pv.div.ownerDocument;
    pv.div.querySelectorAll('.vr-doc').forEach(e => e.remove());
    const box = (r) => {
      const [x0, y0] = pv.viewport.convertToViewportPoint(r[0], r[1]);
      const [x1, y1] = pv.viewport.convertToViewportPoint(r[2], r[3]);
      return [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)];
    };
    for (const x of this.excerpts) {  // (filed in a category: tinted, in its colour)
      for (const r of x.rects) {
        if ((r[4] ?? x.page) !== pv.id) continue;
        const [left, top, w, h] = box(r);
        const mark = doc.createElement('div');
        mark.className = 'vr-doc';
        mark.style.userSelect = 'none';  // (never copied with the text)
        mark.title = x.category;
        const on = x.id === this.selected, colour = x.colour || '#ffc800';
        Object.assign(mark.style, {
          position: 'absolute', left: left + 'px', top: top + 'px', width: w + 'px',
          height: h + 'px', pointerEvents: 'none', zIndex: 4,
          background: colour + (on ? '80' : '40'),
          boxShadow: on ? '0 0 0 2px ' + colour : '',
        });
        pv.div.appendChild(mark);
      }
    }
    for (const l of this.links) {
      l.rects.forEach((r, k) => {
        if ((r[4] ?? l.page) !== pv.id) return;
        const [left, top, w, h] = box(r);
        // A paper (its reference, or a citation): a light dotted underline, itself the link.
        const mark = doc.createElement('a');
        mark.className = 'vr-doc';
        mark.style.userSelect = 'none';  // (never copied with the text)
        mark.href = '#';
        mark.title = l.title;
        Object.assign(mark.style, {
          position: 'absolute', left: left + 'px', top: top + 'px', width: w + 'px',
          height: h + 'px', zIndex: 30, borderBottom: '1px dotted ' + l.colour, opacity: 0.8,
          cursor: 'pointer',
        });
        mark.addEventListener('click', (e) => {
          e.preventDefault(); e.stopPropagation();
          vrPane.emit('vr-doc-paper', {i: l.i, cite: e.shiftKey});
        });
        pv.div.appendChild(mark);
      });
    }
  },
};
document.addEventListener('webviewerloaded', (e) => {
  const w = e.detail.source;
  w.PDFViewerApplicationOptions.set('disablePreferences', true);
  w.PDFViewerApplicationOptions.set('enableHighlightFloatingButton', true);
  // Comments (notes) on the annotations, e.g. a highlight's (its toolbar), and their list.
  w.PDFViewerApplicationOptions.set('enableComment', true);
  // Its history (back and forward after following a link): not made for an embedded viewer.
  w.PDFViewerApplication.isViewerEmbedded = false;
  w.PDFViewerApplicationOptions.set('externalLinkTarget', 4);  // (TOP, as when embedded)
  const h = w.history, push = h.pushState.bind(h), replace = h.replaceState.bind(h);
  w.history.pushState = (s, ...rest) => {
    push(s, ...rest);
    if (s && Number.isInteger(s.uid)) vrPdf.maxUid = s.uid;
    vrPdf.nav();
  };
  w.history.replaceState = (...args) => { replace(...args); vrPdf.nav(); };
  w.addEventListener('popstate', () => vrPdf.nav());
  w.addEventListener('keydown', (ev) => vrPdf.key(ev), true);
  w.document.addEventListener('copy', (ev) => vrPdf.copyArea(ev), true);
  w.document.addEventListener('selectionchange', () => {
    const sel = w.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim()) vrPdf.clearArea();
    vrPdf.actions();
  });
  // An area of a page, drawn (before PDF.js sees the mouse: no text selected then).
  w.addEventListener('mousedown', (ev) => vrPdf.areaDown(ev), true);
  w.addEventListener('mousemove', (ev) => vrPdf.areaMove(ev), true);
  w.addEventListener('mouseup', (ev) => vrPdf.areaUp(ev), true);
  w.addEventListener('click', (ev) => vrDoc.clicked(ev));
  vrPdf.nav();
  // The viewer's actions not wanted here: files (the app's header has them), signatures…
  const style = w.document.createElement('style');
  style.textContent = `#secondaryOpenFile, #secondaryPrint, #printButton, #downloadButton,
    #secondaryDownload, #viewBookmark, #viewBookmarkSeparator, #editorSignature,
    #imageAltTextSettings, #imageAltTextSettingsSeparator, #documentProperties
    { display: none !important; }
    .vr-area-mode .page, .vr-area-mode .page * { cursor: crosshair !important; }`;
  w.document.head.appendChild(style);
  w.PDFViewerApplication.initializedPromise.then(() => {
    const a = w.PDFViewerApplication;
    // (the shortcuts in the editing buttons' tooltips)
    for (const [id, k] of [['editorHighlightButton', 'H'], ['editorFreeTextButton', 'T'],
                           ['editorInkButton', 'D'], ['editorStampButton', 'I'],
                           ['editorCommentButton', 'C']]) {
      const b = w.document.getElementById(id);
      if (b) b.addEventListener('mouseenter', () => {
        if (b.title && !b.title.endsWith(`(${k})`)) b.title += ` (${k})`;
      });
    }
    a.save = () => vrPdf.save(true);
    // Changes: those not saved yet (the viewer counts every annotation of the document).
    a._hasChanges = () => vrPdf.dirty();
    a.eventBus.on('documentloaded', () => {
      vrPdf.saved = vrPdf.hash();
      if (vrDoc.id && vrDoc.needText) vrDoc.sendText();
    });
    a.eventBus.on('pagerendered', (ev) => {
      vrDoc.draw && vrDoc.draw(ev.source);
      vrPdf.drawArea(ev.source);
    });
    a.eventBus.on('annotationeditormodechanged', (ev) => {
      if (ev.mode > 0) vrPdf.areaMode(false);  // (an editing mode: not both)
    });
    a.eventBus.on('annotationeditorstateschanged', () => vrPdf.actions());
    a.eventBus.on('updateviewarea', (ev) => vrPdf.moved(ev.location));
    vrPdf.actions();
  });
});
setInterval(() => vrPdf.save(false), 4000);
window.addEventListener('beforeunload', (e) => {
  vrPdf.keepAt();
  if (vrPdf.dirty()) {
    vrPdf.save(true);
    e.preventDefault();
  }
});
