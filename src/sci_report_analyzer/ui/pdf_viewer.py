"""The PDF of a paper, read and annotated in the browser with PDF.js (highlight, text,
drawing, images): its edits are saved back into the data directory. Also what the viewer
pages share (the documents' too, see documents.py): bookmarks, the paper of a selection,
and a paper's details next to the PDF."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import urlencode
from weakref import WeakSet

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from nicegui import Client, app, background_tasks, ui

from .. import annotations, documents, pdfs
from ..db.models import Publication
from ..db.session import session_scope
from ..i18n import _, ngettext
from .theme import MARKDOWN_CSS

if TYPE_CHECKING:
    from ..pubview import PubStat
    from .panel import PublicationsPanel
    from .viewer_side import Side

NO_CONFIRM = "ui.pdf.no_confirm"  # AppSetting: download without asking first
_no_confirm: bool | None = None  # (cached NO_CONFIRM)

_TYPES = {
    ".mjs": "text/javascript",
    ".js": "text/javascript",
    ".ftl": "text/plain; charset=utf-8",
    ".wasm": "application/wasm",
    ".bcmap": "application/octet-stream",
    ".pfb": "application/octet-stream",
    ".icc": "application/octet-stream",
}

# Saves the edits back (Ctrl+S, the viewer's save button, and every few seconds), and turns
# on the highlight button of a text selection; where the reader is (bookmarks), and what is
# selected (to find its paper).
_SCRIPT = """
<script>
window.vrPdf = {
  url: %(url)s, texts: %(texts)s, saving: false, saved: null,
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
  // Without one: the area drawn (see area), if any.
  selection() {
    const w = this.win(), a = this.app();
    if (!w || !a) return null;
    const sel = w.getSelection();
    const text = sel && !sel.isCollapsed && sel.rangeCount ? sel.toString().trim() : '';
    if (!text) {
      const x = this.area;
      return x ? {text: x.text, p: x.p, rects: [[...x.rect, x.p]], area: true} : null;
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
  // whose middle is in one of them (a new line: a space).
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
      if (top !== null && Math.abs(r.top - top) > r.height / 2) text += ' ';  // (a new line)
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
  // An area of a page (a rectangle drawn with the mouse: in the area mode, or with Alt), as a
  // selection: the text within, its page and its rectangle (PDF units). Kept until a click
  // elsewhere, a text selection, or Escape.
  area: null, areaOn: false, drag: null,
  areaMode(on) {
    this.areaOn = on === undefined ? !this.areaOn : on;
    const a = this.app();
    if (this.areaOn && a && a.pdfViewer && a.pdfViewer.annotationEditorMode > 0) this.mode(0);
    this.win()?.document.body.classList.toggle('vr-area-mode', this.areaOn);
    const b = document.getElementById('vr-pdf-area');
    if (b) b.style.background = this.areaOn ? 'rgba(255, 255, 255, 0.3)' : '';
  },
  clearArea() {
    if (!this.area) return;
    const pv = this.app()?.pdfViewer?.getPageView(this.area.p - 1);
    this.area = null;
    if (pv) this.drawArea(pv);
    this.actions();
  },
  // (the box of a page's viewport, on the screen)
  pageBox(pv) {
    const layer = pv.div.querySelector('.textLayer');
    if (layer) return layer.getBoundingClientRect();
    const r = pv.div.getBoundingClientRect();
    return {left: r.left + pv.div.clientLeft, top: r.top + pv.div.clientTop};
  },
  drawArea(pv, rect) {
    if (!pv || !pv.div || !pv.viewport) return;
    pv.div.querySelectorAll('.vr-area').forEach(e => e.remove());
    rect = rect || (this.area && this.area.p === pv.id ? this.area.rect : null);
    if (!rect) return;
    const [x0, y0] = pv.viewport.convertToViewportPoint(rect[0], rect[1]);
    const [x1, y1] = pv.viewport.convertToViewportPoint(rect[2], rect[3]);
    const mark = pv.div.ownerDocument.createElement('div');
    mark.className = 'vr-area';
    Object.assign(mark.style, {
      position: 'absolute', left: Math.min(x0, x1) + 'px', top: Math.min(y0, y1) + 'px',
      width: Math.abs(x1 - x0) + 'px', height: Math.abs(y1 - y0) + 'px', zIndex: 5,
      pointerEvents: 'none', userSelect: 'none', border: '1px dashed #1976d2',
      background: 'rgba(25, 118, 210, 0.12)',
    });
    pv.div.appendChild(mark);
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
    if (!pageDiv) return;  // (the viewer's toolbars: the area kept)
    const editing = a.pdfViewer.annotationEditorMode > 0;
    if (!(this.areaOn || (ev.altKey && !editing))) { this.clearArea(); return; }
    ev.preventDefault(); ev.stopPropagation();
    w.getSelection().removeAllRanges();
    this.clearArea();
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
    this.area = {p: pv.id, rect: rect.map(round), text: this.textIn(pv, [rect])};
    this.drawArea(pv);
    this.actions();
  },
  // The actions on the selected text, greyed out without one (adding an excerpt: also with a
  // highlight selected).
  actions() {
    const sel = this.win()?.getSelection();
    const text = !!((sel && !sel.isCollapsed && sel.toString().trim()) || this.area?.text);
    const ed = this.app()?.pdfViewer?._layerProperties?.annotationEditorUIManager
      ?.firstSelectedEditor;
    const hl = !!(ed && ed.editorType === 'highlight');
    for (const [id, ok] of [['vr-pdf-find', text], ['vr-pdf-excerpt', text || hl]]) {
      const b = document.getElementById(id);
      if (b) { b.disabled = !ok; b.classList.toggle('disabled', !ok); }
    }
  },
  // Toggle an editing mode of the viewer (highlight, text, drawing, image).
  mode(m) {
    const a = this.app();
    if (!a || !a.pdfViewer) return;
    const now = a.pdfViewer.annotationEditorMode;
    a.eventBus.dispatch('switchannotationeditormode', {source: this, mode: now === m ? 0 : m});
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
    a: () => vrPdf.areaMode(),
    e: () => document.getElementById('vr-pdf-excerpt') && emitEvent('vr-pdf-excerpt'),
    b: () => emitEvent('vr-pdf-bookmark'),
    f: () => emitEvent('vr-pdf-find'),  // (without a selection: says so)
  },
  key(ev) {
    if (ev.key === 'Escape' && (this.area || this.areaOn)) {
      this.clearArea(); this.areaMode(false);
      return;
    }
    const act = this.keys[ev.key];
    if (!act || ev.ctrlKey || ev.metaKey || ev.altKey || ev.repeat) return;
    const t = ev.target;
    if (t && (t.isContentEditable || (t.closest && t.closest('input, textarea, select')))) return;
    ev.preventDefault(); ev.stopPropagation();
    act();
  },
};
document.addEventListener('keydown', (ev) => vrPdf.key(ev));
// The papers found in a document (links), and the excerpts filed in categories: drawn on
// the pages.
window.vrDoc = {
  id: null, needText: false, links: [], excerpts: [],
  show(links) { this.links = links; this.redraw(); },
  mark(excerpts) { this.excerpts = excerpts; this.redraw(); },
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
        Object.assign(mark.style, {
          position: 'absolute', left: left + 'px', top: top + 'px', width: w + 'px',
          height: h + 'px', pointerEvents: 'none', zIndex: 4,
          background: x.colour ? x.colour + '40' : 'rgba(255, 200, 0, 0.25)',
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
          emitEvent('vr-doc-paper', {i: l.i});
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
  w.document.addEventListener('selectionchange', () => {
    const sel = w.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim()) vrPdf.clearArea();
    vrPdf.actions();
  });
  // An area of a page, drawn (before PDF.js sees the mouse: no text selected then).
  w.addEventListener('mousedown', (ev) => vrPdf.areaDown(ev), true);
  w.addEventListener('mousemove', (ev) => vrPdf.areaMove(ev), true);
  w.addEventListener('mouseup', (ev) => vrPdf.areaUp(ev), true);
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
                           ['editorInkButton', 'D'], ['editorStampButton', 'I']]) {
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
</script>
"""


def _panels() -> dict[int, WeakSet[PublicationsPanel]]:
    """Person id -> their open publications panels, reloaded when a viewer window changes
    one of their papers (its PDF, tags or notes). Kept on the app: one registry, even if
    this module is imported again."""
    if not hasattr(app.state, "pdf_panels"):
        app.state.pdf_panels = {}
    return app.state.pdf_panels


def watch(panel: PublicationsPanel) -> None:
    """Keep ``panel`` up to date with the changes made from viewer windows."""
    _panels().setdefault(panel.person_id, WeakSet()).add(panel)


def changed(person_id: int, *, but: PublicationsPanel | None = None) -> None:
    """A paper of the person changed in a viewer window: reload their open panels (but
    ``but``, where the change was made)."""
    panels = _panels().get(person_id, WeakSet())
    for panel in list(panels):
        if gone(panel.container):
            panels.discard(panel)
        elif panel is not but:
            reload = getattr(panel, "reload_quietly", panel.reload)
            background_tasks.create(reload(), name="reload after a PDF window")


def gone(element: ui.element) -> bool:
    """Whether the page of ``element`` was left."""
    try:
        return element.is_deleted or element.client.id not in Client.instances
    except RuntimeError:  # (its client was deleted)
        return True


def _title(pub_id: int) -> tuple[str, int] | None:
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        return (pub.title or _("(untitled)"), pub.person_id) if pub else None


def register() -> None:
    @app.get("/pdfjs/{path:path}")
    def viewer_file(path: str) -> FileResponse:
        root = pdfs.viewer_dir().resolve()
        file = (root / path).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise HTTPException(404)
        return FileResponse(file, media_type=_TYPES.get(file.suffix))

    @app.get("/pdf-file/{pub_id}")
    def pdf_file(pub_id: int, download: bool = False) -> FileResponse:
        return file_response(pdfs.file_of(pub_id), download)

    @app.put("/pdf-file/{pub_id}")
    async def save_pdf(pub_id: int, request: Request) -> PlainTextResponse:
        found = _title(pub_id)
        if found is None or pdfs.file_of(pub_id) is None:
            raise HTTPException(404)
        first = pdfs.stored(found[1]).get(pub_id) is False  # (its first annotations)
        try:
            pdfs.save(pub_id, await request.body(), None, edited=True)
        except pdfs.PdfError as e:
            raise HTTPException(400, str(e)) from e
        if first:
            changed(found[1])
        return PlainTextResponse("ok")

    @ui.page("/pdf/{pub_id}")
    def pdf_page(
        pub_id: int, fetch: bool = False, period: int | None = None, page: int | None = None
    ) -> None:
        found = _title(pub_id)
        ui.page_title(f"{found[0] if found else 'PDF'} · SciReport Analyzer")
        ui.query(".nicegui-content").classes("p-0 gap-0")
        if found is None:
            ui.label(_("No such paper")).classes("p-4")
            return
        title, person_id = found
        if not pdfs.has_viewer() or pdfs.file_of(pub_id) is None:
            _prepare(pub_id, title, fetch, period)
            return
        from .viewer_side import Side, bookmarks_section

        file = pdfs.file_of(pub_id)
        side = Side(person_id, period, ("pub", pub_id))
        box = viewer_frame(
            title,
            ("SciReport Analyzer", f"/person/{person_id}"),
            f"/pdf-file/{pub_id}",
            int(file.stat().st_mtime) if file else 0,
            side,
            ("pub", pub_id),
            page=page,
        )
        side.attach(box)
        with side.section("notes", "sell", _("Tags and notes")):
            tags_box = ui.column().classes("w-full gap-2").mark("pdf-notes")
            with tags_box:
                ui.spinner()
        with side.section("details", "article", _("Publication details"), wide=True):
            details_box = ui.column().classes("w-full gap-2 vr-details").mark("pdf-pub-details")
            with details_box:
                ui.spinner()
        with side.section("bookmarks", "bookmarks", _("Bookmarks")):
            side.bookmarks = bookmarks_section("pub", pub_id)
        ui.timer(0.05, lambda: _side(side, tags_box, details_box, pub_id), once=True)


def file_response(file, download: bool) -> FileResponse:
    if file is None:
        raise HTTPException(404)
    return FileResponse(
        file,
        media_type="application/pdf",
        filename=file.name if download else None,
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "no-store"},
    )


def viewer_frame(
    title: str,
    home: tuple[str, str],
    file_url: str,
    version: int,
    side: Side,
    bookmarked: tuple[str, int],
    script: str = "",
    page: int | None = None,
) -> ui.column:
    """The page of a stored PDF: a header (back to ``home``, saving, bookmarks, finding the
    paper of a selection), PDF.js (opened at ``page``, else where it was last read), and the
    side column (returned)."""
    texts = {
        "saving": _("Saving…"),
        "saved": _("Saved at {time}"),
        "failed": _("Not saved: {error}"),
    }
    ui.add_css(MARKDOWN_CSS)
    ui.add_head_html(_SCRIPT % {"url": json.dumps(file_url), "texts": json.dumps(texts)} + script)
    with ui.row().classes("w-full items-center no-wrap gap-2 px-3 py-1 bg-primary text-white"):
        ui.link(home[0], home[1]).classes("text-white font-bold no-underline ellipsis max-w-48")
        for icon, step, tip in (
            ("arrow_back", "back", _("Back (after following a link; also ⌥← or ⌘[)")),
            ("arrow_forward", "forward", _("Forward (also ⌥→ or ⌘])")),
        ):
            ui.button(icon=icon).props(f"flat dense round color=white id=vr-pdf-{step}").on(
                "click", js_handler=f"() => vrPdf.app()?.pdfHistory?.{step}()"
            ).tooltip(tip).mark(f"pdf-{step}")
        ui.label(title).classes("ellipsis grow min-w-0 font-medium")
        ui.label("").classes("text-sm opacity-80").props("id=vr-pdf-status").mark("pdf-status")
        # (the actions on a selection, greyed out without one: their tooltips on a wrapper, a
        # disabled button showing none)
        with ui.element("span").tooltip(
            _("Find the paper of the selected text, e.g. a reference (F)")
        ):
            ui.button(icon="manage_search", on_click=side.find_selection).props(
                "flat dense round color=white id=vr-pdf-find"
            ).mark("pdf-find")
        ui.on("vr-pdf-find", side.find_selection)
        # An area (a rectangle) of a page, selected: its text, as a text selection.
        ui.button(icon="highlight_alt").props("flat dense round color=white id=vr-pdf-area").on(
            "click", js_handler="() => vrPdf.areaMode()"
        ).tooltip(
            _(
                "Select an area of a page: draw a rectangle, its text is the selection "
                "(A; also Alt+drag; Escape to leave)"
            )
        ).mark("pdf-area")
        if side.folder and side.source[0] == "doc":  # (excerpts: of documents, not papers)
            with ui.element("span").tooltip(
                _(
                    "Add the selected text (or the highlight clicked) to a category of {folder} (E)"
                ).format(folder=side.folder[1])
            ):
                ui.button(icon="playlist_add", on_click=side.add_excerpt).props(
                    "flat dense round color=white id=vr-pdf-excerpt"
                ).mark("pdf-excerpt")
            ui.on("vr-pdf-excerpt", side.add_excerpt)
        ui.button(icon="bookmark_add", on_click=lambda: side.add_bookmark(*bookmarked)).props(
            "flat dense round color=white"
        ).tooltip(_("Bookmark this place, named after the selected text if any (B)")).mark(
            "pdf-bookmark"
        )
        ui.on("vr-pdf-bookmark", lambda: side.add_bookmark(*bookmarked))
        ui.button(_("Save"), icon="save").props("flat dense color=white").on(
            "click", js_handler="() => vrPdf.save(true)"
        ).tooltip(_("Save the annotations into the stored PDF (also every few seconds)"))
        with ui.link(target=f"{file_url}?download=1"):
            ui.button(icon="download").props("flat dense round color=white").tooltip(
                _("Download a copy")
            )
        ui.button(icon="view_sidebar", on_click=lambda: box.set_visibility(not box.visible)).props(
            "flat dense round color=white"
        ).tooltip(_("Side panel (notes, bookmarks, papers)")).mark("pdf-toggle-notes")
    src = f"/pdfjs/web/viewer.html?file={file_url}%3Fv%3D{version}"
    # (at a page asked for, else where it was last read)
    src += f"#page={page}" if page else documents.place_hash(documents.last_place(*bookmarked))
    ui.on("vr-pdf-at", lambda e: documents.save_last_place(*bookmarked, e.args or {}))
    with ui.row().classes("w-full no-wrap gap-0"):
        ui.element("iframe").props(f'id=vr-pdf-frame src="{src}"').classes("grow").style(
            "height:calc(100vh - 40px); border:0"
        ).mark("pdf-frame")
        box = (
            ui.column()
            .classes("w-96 shrink-0 p-3 gap-2 overflow-auto border-l")
            .style("height:calc(100vh - 40px)")
            .mark("pdf-side")
        )
    return box


async def _side(side: Side, box: ui.column, details_box: ui.column, pub_id: int) -> None:
    """The paper's tags and notes (its own, and within a period), and its details (as in
    the publications panel), next to its PDF."""
    from .panel import period_label
    from .pub_details import show_details
    from .tags import paper_tags_and_notes, tags_dialog

    await side.load()
    s = next((x for x in side.stats if x.id == pub_id), None)
    periods = annotations.periods(side.person_id, include_hidden_folders=True)
    state = {"period": next((p for p in periods if p.id == side.host.period_id), None)}
    tags = annotations.all_tags()

    @ui.refreshable
    def body() -> None:
        if s is None:
            ui.label(_("This paper is no longer in the database")).classes("text-grey")
            return
        options = {0: _("All years"), **{p.id: period_label(p) for p in periods}}

        def set_period(e) -> None:
            state["period"] = next((p for p in periods if p.id == e.value), None)
            body.refresh()

        ui.select(
            options, value=state["period"].id if state["period"] else 0, on_change=set_period
        ).props("dense outlined options-dense").classes("w-full").tooltip(
            _("The period whose tags and notes are shown (e.g. a folder's)")
        ).mark("pdf-period")

        def manage() -> None:
            def changed_tags() -> None:
                tags[:] = annotations.all_tags()
                body.refresh()

            with box:
                tags_dialog(changed_tags)

        paper_tags_and_notes(
            s, state["period"], tags, lambda: changed(side.person_id, but=side.host), manage
        )

    box.clear()
    with box:
        body()
    if s is None:
        details_box.clear()
        with details_box:
            ui.label(_("This paper is no longer in the database")).classes("text-grey")
    else:  # (tags and notes: in their own tab)
        show_details(side.host, s, details_box, None, notes=False)


async def install_or_notify(label: ui.label) -> None:
    if not pdfs.has_viewer():
        label.text = _("Installing the PDF viewer (PDF.js, once)…")
        await pdfs.install_viewer()


def _prepare(pub_id: int, title: str, fetch: bool, period: int | None) -> None:
    """The viewer page before it can show the PDF: download PDF.js, and the paper's PDF when
    asked to (``fetch``)."""
    with ui.column().classes("p-6 gap-3 w-full max-w-3xl mx-auto") as box:
        ui.label(title).classes("text-lg font-medium")
        status = ui.row().classes("items-center gap-2")
        has_pdf = pdfs.file_of(pub_id) is not None
        if not has_pdf and not fetch:
            with status:
                ui.label(_("No PDF stored for this paper."))
                ui.button(
                    _("Download it"),
                    icon="download",
                    on_click=lambda: ui.navigate.to(page_url(pub_id, period, fetch=True)),
                ).props("flat").mark("pdf-fetch")
            _upload(pub_id, period)
            return
        with status:
            ui.spinner(size="sm")
            label = ui.label("")

    async def prepare() -> None:
        try:
            await _get(pub_id, label)
        except pdfs.PdfError as e:
            status.clear()
            with status:
                ui.icon("error", color="negative")
                ui.label(_("Could not get the PDF: {error}").format(error=e)).classes(
                    "text-negative"
                ).mark("pdf-error")
            with box:
                _upload(pub_id, period)
            return
        ui.navigate.to(page_url(pub_id, period))

    ui.timer(0.1, prepare, once=True)


async def _get(pub_id: int, label: ui.label) -> None:
    try:
        await install_or_notify(label)
        if pdfs.file_of(pub_id) is None:
            label.text = _("Downloading the PDF…")
            await pdfs.download(pub_id)
    finally:
        if found := _title(pub_id):
            changed(found[1])


def _upload(pub_id: int, period: int | None) -> None:
    async def done(e) -> None:
        try:
            pdfs.save(pub_id, await e.file.read(), None)
        except pdfs.PdfError as err:
            ui.notify(str(err), type="warning")
            return
        if found := _title(pub_id):
            changed(found[1])
        ui.navigate.to(page_url(pub_id, period))

    ui.label(_("Or upload it (a PDF file):")).classes("text-sm text-grey")
    ui.upload(on_upload=done, auto_upload=True).props('accept=".pdf,application/pdf"').mark(
        "pdf-upload"
    )


# ---- In the panel ---------------------------------------------------------------------------


def page_url(pub_id: int, period: int | None = None, *, fetch: bool = False) -> str:
    """The viewer page of a paper (with the tags and notes of ``period``)."""
    params = {k: v for k, v in (("fetch", 1 if fetch else None), ("period", period)) if v}
    return f"/pdf/{pub_id}" + (f"?{urlencode(params)}" if params else "")


def can_download(s: PubStat) -> bool:
    return bool(s.pdf_urls or s.doi or s.doi_manual)


def pdf_button(panel: PublicationsPanel, s: PubStat) -> None:
    """Next to a paper's title: its stored PDF (opened in a new window), else a button to
    download it (after confirmation) and open it."""
    period = panel.period_id
    if s.pdf:
        edited = s.pdf == "edited"
        with ui.link(target=page_url(s.id, period), new_tab=True).mark(f"view-pdf-{s.id}"):
            ui.icon(
                "edit_document" if edited else "picture_as_pdf", size="xs", color="red-8"
            ).tooltip(_("View the PDF (annotated)") if edited else _("View and annotate the PDF"))
    elif can_download(s):
        icon = (
            ui.icon("picture_as_pdf", size="xs", color="grey-6")
            .classes("vr-src cursor-pointer")
            .tooltip(_("Download the PDF (open access) to view and annotate it"))
            .mark(f"get-pdf-{s.id}")
        )
        if no_confirm():
            icon.on("click", lambda: None, js_handler=_open_js(s.id, period))
        else:
            icon.on("click", lambda: confirm_download(panel, s))


def no_confirm() -> bool:
    global _no_confirm
    if _no_confirm is None:
        _no_confirm = bool(annotations.ui_state(NO_CONFIRM, False))
    return _no_confirm


def _open_js(pub_id: int, period: int | None) -> str:
    # Opened from the click itself (a window opened later would be blocked as a pop-up).
    url = json.dumps(page_url(pub_id, period, fetch=True))
    return f"() => {{ window.open({url}, '_blank'); emit(); }}"


def confirm_download(panel: PublicationsPanel, s: PubStat) -> None:
    with panel.dialogs, ui.dialog() as dlg, ui.card().classes("w-full max-w-xl"):
        ui.label(_("Download the PDF?")).classes("text-lg font-medium")
        ui.label(s.title or _("(untitled)")).classes("font-medium")
        with ui.column().classes("gap-0 text-sm"):
            for url in s.pdf_urls:
                ui.link(url, url, new_tab=True).classes("break-all")
            if s.doi or s.doi_manual:
                ui.label(
                    _("Else an open-access copy found by Unpaywall")
                    if s.pdf_urls
                    else _("From an open-access copy found by Unpaywall (by its DOI)")
                ).classes("text-grey")
        ui.label(
            _(
                "It is stored in the data directory, and opens in a new window where you can "
                "highlight and annotate it."
            )
        ).classes("text-sm text-grey")
        again = ui.checkbox(_("Don't ask again")).mark("pdf-no-confirm")

        def go() -> None:
            global _no_confirm
            if again.value:
                annotations.save_ui_state(NO_CONFIRM, True)
                _no_confirm = True
            dlg.close()

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Download and open"), icon="download").on(
                "click", go, js_handler=_open_js(s.id, panel.period_id)
            ).mark("pdf-download-ok")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def download_dialog(panel: PublicationsPanel, rows: list[PubStat]) -> None:
    """Download the open-access PDFs of the papers shown (those without one yet)."""
    todo = [s for s in rows if not s.pdf and can_download(s)]
    stored = sum(1 for s in rows if s.pdf)
    with panel.dialogs, ui.dialog() as dlg, ui.card().classes("w-full max-w-2xl"):
        ui.label(_("Download the PDFs")).classes("text-lg font-medium")
        ui.label(
            _(
                "{todo} of the {total} papers shown have an open-access link or a DOI and no "
                "stored PDF ({stored} stored already). Their PDFs are downloaded into the data "
                "directory."
            ).format(todo=len(todo), total=len(rows), stored=stored)
        ).classes("text-sm").mark("pdf-batch-info")
        progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress.visible = False
        result = ui.column().classes("w-full gap-1 text-sm")
        titles = {s.id: s.title or _("(untitled)") for s in todo}

        async def run() -> None:
            start.disable()
            progress.visible = True

            def step(done: int, total: int) -> None:
                progress.value = done / total

            batch = await pdfs.download_many([s.id for s in todo], step)
            progress.visible = False
            with result:
                ui.label(
                    ngettext("{n} PDF downloaded", "{n} PDFs downloaded", len(batch.done)).format(
                        n=len(batch.done)
                    )
                ).classes("font-medium").mark("pdf-batch-done")
                if batch.failed:
                    ui.label(
                        ngettext(
                            "Not found for {n} paper:",
                            "Not found for {n} papers:",
                            len(batch.failed),
                        ).format(n=len(batch.failed))
                    ).classes("text-grey")
                    with ui.column().classes("gap-0 max-h-64 overflow-auto"):
                        for pid, why in batch.failed.items():
                            ui.label(f"{titles.get(pid, pid)} — {why}").classes(
                                "text-xs text-grey ellipsis w-full"
                            ).tooltip(why)
            await panel.reload()

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Close"), on_click=dlg.close).props("flat")
            start = ui.button(_("Download"), icon="download", on_click=run).mark("pdf-batch-ok")
            if not todo:
                start.disable()
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()
