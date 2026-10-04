"""The PDF of a paper, read and annotated in the browser with PDF.js (highlight, text,
drawing, images): its edits are saved back into the data directory. Also what the viewer
pages share (the documents' too, see documents.py): bookmarks, the paper of a selection,
and a paper's details next to the PDF."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from weakref import WeakSet

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from nicegui import Client, app, background_tasks, ui

from .. import annotations, documents, livereload, pdfs, pdftext
from ..db.models import Publication
from ..db.session import session_scope
from ..i18n import N_, _, ngettext
from .dialogs import transient_dialog
from .theme import APP_NAME, bare_page

if TYPE_CHECKING:
    from ..pubview import PubStat
    from .panel import PublicationsPanel
    from .viewer_side import Side

NO_CONFIRM = "ui.pdf.no_confirm"  # AppSetting: download without asking first
SIDE_WIDTH = "ui.pdf.side_width"  # AppSetting: the width (px) of the side column
SIDE_DEFAULT, SIDE_MIN = 384, 240
PANE_TOKEN = re.compile(r"[a-z0-9]{1,40}")  # (of a PDF window, its side panel elsewhere)
_no_confirm: bool | None = None  # (cached NO_CONFIRM)
TAG_LIST_TIP = N_(
    "Tag from a list: find the papers of the selected list (e.g. an area over numbered "
    "references) and tag them (L)"
)

_TYPES = {
    ".mjs": "text/javascript",
    ".js": "text/javascript",
    ".ftl": "text/plain; charset=utf-8",
    ".wasm": "application/wasm",
    ".bcmap": "application/octet-stream",
    ".pfb": "application/octet-stream",
    ".icc": "application/octet-stream",
}

# Dragging the splitter between the PDF and the side column resizes the latter (the iframe
# is covered meanwhile, else it swallows the mouse events); its width is sent on release.
_SPLITTER = """
<script>
document.addEventListener('mousedown', (ev) => {
  const bar = ev.target.closest && ev.target.closest('#vr-pdf-splitter');
  if (!bar) return;
  ev.preventDefault();
  const side = document.getElementById('vr-pdf-side'), row = side.parentElement;
  const cover = document.createElement('div');
  cover.style.cssText = 'position:fixed;inset:0;z-index:9999;cursor:col-resize';
  document.body.appendChild(cover);
  const width = (x) => Math.round(Math.max(%(min)d,
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
</script>
"""

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
      if (top !== null && Math.abs(r.top - top) > r.height / 2) text += '\\n';  // (a new line)
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
  areaText() { return this.areas.map(x => x.text).filter(t => t).join('\\n'); },
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
    if (t && (t.isContentEditable || (t.closest && t.closest('input, textarea, select')))) {
      return false;
    }
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
    e: () => document.getElementById('vr-pdf-excerpt') && vrPane.emit('vr-pdf-excerpt'),
    b: () => vrPane.emit('vr-pdf-bookmark'),
    f: () => vrPane.emit('vr-pdf-find'),  // (without a selection: says so)
    q: () => vrPane.emit('vr-pdf-quote'),  // (into the note last used)
    l: () => vrPane.emit('vr-pdf-tag-list'),  // (the papers of a list, tagged)
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
    const act = this.keys[ev.key];
    if (!act || ev.ctrlKey || ev.metaKey || ev.altKey || ev.repeat) return;
    const t = ev.target;
    if (t && (t.isContentEditable || (t.closest && t.closest('input, textarea, select')))) return;
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
    const id = hits.length ? hits[(hits.indexOf(this.selected) + 1) %% hits.length] : null;
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
</script>
"""

# The side panel in its own window (the pane): what its tabs ask of the PDF (vrPdf, vrDoc) goes
# to the PDF window, through the channel of the browser named after its token (see vrPane
# there); the actions of the PDF window's shortcuts and links come from it.
_PANE_SCRIPT = """
<script>
window.vrPane = {
  token: %(token)s, texts: %(texts)s, channel: null, asked: 0, waiting: {}, done: false,
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
  const name = {q: 'vr-pdf-quote', e: 'vr-pdf-excerpt', b: 'vr-pdf-bookmark', f: 'vr-pdf-find',
                l: 'vr-pdf-tag-list'}[ev.key];
  if (!name || ev.ctrlKey || ev.metaKey || ev.altKey || ev.repeat) return;
  if (name === 'vr-pdf-excerpt' && !document.getElementById('vr-pdf-excerpt')) return;
  const t = ev.target;
  if (t && (t.isContentEditable || (t.closest && t.closest('input, textarea, select')))) return;
  ev.preventDefault();
  emitEvent(name);
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
        pub_id: int,
        fetch: bool = False,
        period: int | None = None,
        page: int | None = None,
        pane: str | None = None,
    ) -> None:
        found = _title(pub_id)
        bare_page(found[0] if found else "PDF")
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
            (APP_NAME, f"/person/{person_id}"),
            f"/pdf-file/{pub_id}",
            int(file.stat().st_mtime) if file else 0,
            side,
            ("pub", pub_id),
            page=page,
            pane=pane,
        )
        side.attach(box)
        side.folder_notes()
        with side.section("notes", "sell", _("Tags and notes"), fill=True):
            tags_box = ui.column().classes("w-full gap-2").mark("pdf-notes")
            with tags_box:
                ui.spinner()
        with side.section("details", "article", _("Publication details"), wide=True):
            details_box = ui.column().classes("w-full gap-2 vr-details").mark("pdf-pub-details")
            with details_box:
                ui.spinner()
        with side.section("bookmarks", "bookmarks", _("Bookmarks")):
            side.bookmarks = bookmarks_section("pub", pub_id)
        side.on_attach.append(lambda: _side(side, tags_box, details_box, pub_id))
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
    pane: str | None = None,
) -> ui.column:
    """The page of a stored PDF: a header (back to ``home``, saving, bookmarks, finding the
    paper of a selection), PDF.js (opened at ``page``, else where it was last read), and the
    side column (returned). With ``pane`` (the token of a PDF window): only the side column,
    in its own window, kept in sync with that one."""
    if pane and PANE_TOKEN.fullmatch(pane):
        return _pane_frame(title, home, side, bookmarked, pane)
    texts = {
        "saving": _("Saving…"),
        "saved": _("Saved at {time}"),
        "failed": _("Not saved: {error}"),
    }
    ui.add_head_html(_SPLITTER % {"min": SIDE_MIN})
    ui.add_head_html(pdftext.SCRIPT)
    ui.add_head_html(_SCRIPT % {"url": json.dumps(file_url), "texts": json.dumps(texts)} + script)
    with ui.row().classes("w-full items-center no-wrap gap-2 px-3 py-1 bg-primary text-white"):
        ui.link(home[0], home[1]).classes("text-white font-bold no-underline ellipsis max-w-48")
        for icon, step, tip in (
            ("arrow_back", "back", _("Back (after following a link; also ⌥← or ⌘[)")),
            ("arrow_forward", "forward", _("Forward (also ⌥→ or ⌘])")),
        ):
            with ui.element("span").tooltip(tip):  # (a button with its own id shows none)
                ui.button(icon=icon).props(f"flat dense round color=white id=vr-pdf-{step}").on(
                    "click", js_handler=f"() => vrPdf.app()?.pdfHistory?.{step}()"
                ).mark(f"pdf-{step}")
        ui.label(title).classes("ellipsis grow min-w-0 font-medium")
        ui.label("").classes("text-sm opacity-80").props("id=vr-pdf-status").mark("pdf-status")
        livereload.banner(dense=True)  # (--live-reload: a new version, to restart)
        _selection_actions(side, bookmarked, here=True)
        ui.button(_("Save"), icon="save").props("flat dense color=white").on(
            "click", js_handler="() => vrPdf.save(true)"
        ).tooltip(_("Save the annotations into the stored PDF (also every few seconds)"))
        with ui.link(target=f"{file_url}?download=1"):
            ui.button(icon="download").props("flat dense round color=white").tooltip(
                _("Download a copy")
            )
        ui.button(icon="open_in_new").props("flat dense round color=white").on(
            "click", js_handler="() => vrPane.toggle()"
        ).tooltip(
            _(
                "Show the side panel (notes, excerpts, bookmarks…) in another window, e.g. on "
                "another screen; again: back here"
            )
        ).mark("pdf-pane")

        def toggle_side() -> None:
            if side.detached:  # (in another window: back here)
                ui.run_javascript("vrPane.toggle()")
            else:
                box.set_visibility(not box.visible)

        ui.button(icon="view_sidebar", on_click=toggle_side).props(
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
        splitter = ui.element("div").props("id=vr-pdf-splitter")
        splitter.classes("shrink-0 bg-grey-4 hover:bg-primary").style(
            "width:5px; cursor:col-resize; height:calc(100vh - 40px)"
        ).tooltip(_("Drag to resize the side panel")).mark("pdf-splitter")
        width = max(SIDE_MIN, int(annotations.ui_state(SIDE_WIDTH, SIDE_DEFAULT) or SIDE_DEFAULT))
        box = (
            ui.column()
            .classes("shrink-0 p-3 gap-2 overflow-auto")
            .style(f"width:{width}px;height:calc(100vh - 40px)")
            .props("id=vr-pdf-side")
            .mark("pdf-side")
        )
        ui.on(
            "vr-pdf-side-width",
            lambda e: annotations.save_ui_state(SIDE_WIDTH, max(SIDE_MIN, int(e.args))),
        )

    async def detached(e) -> None:  # (the side panel in another window, or back)
        side.detached = bool(e.args)
        box.set_visibility(not side.detached)
        splitter.set_visibility(not side.detached)
        if not side.detached:
            await side.reattached()

    ui.on("vr-pane", detached)
    ui.timer(0, lambda: ui.run_javascript("vrPane.start()"), once=True)  # (once connected)
    return box


def _selection_actions(side: Side, bookmarked: tuple[str, int], *, here: bool) -> None:
    """The header's actions on the PDF's selection, and the events of their shortcuts.
    ``here``: in the PDF's window (with the area selection; done in the pane while the side
    panel is in another window), else in that other window."""

    def action(event: str, icon: str, tip: str, act: Callable[[], Any], mark: str) -> None:
        # (greyed out without a selection: the tooltip on a wrapper, a disabled button
        # showing none; the id for the PDF's script)
        with ui.element("span").tooltip(tip):
            ui.button(icon=icon, on_click=_here(side, event, act) if here else act).props(
                f"flat dense round color=white id={event}"
            ).mark(mark)
        ui.on(event, act)

    ui.on("vr-pdf-quote", lambda: side.quote())
    action(
        "vr-pdf-find",
        "manage_search",
        _("Find the paper of the selected text, e.g. a reference (F)"),
        side.find_selection,
        "pdf-find",
    )
    action(
        "vr-pdf-tag-list", "playlist_add_check", _(TAG_LIST_TIP), side.tag_selection, "pdf-tag-list"
    )
    if here:
        # An area (a rectangle) of a page, selected: its text, as a text selection.
        with ui.element("span").tooltip(
            _(
                "Select an area of a page (A, or Alt+drag): draw a rectangle on the page, the "
                "text inside it becomes the selection, to quote (Q), add as an excerpt (E), find "
                "its paper (F), tag the papers of a list (L) or copy (⌘C / Ctrl+C). Shift+drag "
                "adds another area (their texts, in order). Escape to leave."
            )
        ):
            ui.button(icon="highlight_alt").props("flat dense round color=white id=vr-pdf-area").on(
                "click", js_handler="() => vrPdf.areaMode()"
            ).mark("pdf-area")
    if side.folder and side.source[0] == "doc":  # (excerpts: of documents, not papers)
        action(
            "vr-pdf-excerpt",
            "playlist_add",
            _(
                "Add the selected text (or the highlight clicked) to a category of {folder} (E)"
            ).format(folder=side.folder[1]),
            side.add_excerpt,
            "pdf-excerpt",
        )
    action(
        "vr-pdf-bookmark",
        "bookmark_add",
        _("Bookmark this place, named after the selected text if any (B)"),
        lambda: side.add_bookmark(*bookmarked),
        "pdf-bookmark",
    )


def _here(side: Side, event: str, act: Callable[[], Any]) -> Callable[[], Awaitable[None]]:
    """A header action on the selection (``event``: its shortcut's), done in the pane while
    the side panel is in another window."""

    async def run() -> None:
        if side.detached:
            ui.run_javascript(f"vrPane.emit({json.dumps(event)})")
        elif inspect.isawaitable(done := act()):
            await done

    return run


def _pane_frame(
    title: str, home: tuple[str, str], side: Side, bookmarked: tuple[str, int], token: str
) -> ui.column:
    """The side column of a PDF window (its ``token``) in a window of its own (e.g. on
    another screen): a header (the actions on the PDF's selection, the way back), the column
    (returned)."""
    ui.page_title(_("{title} · side panel").format(title=title))
    texts = {
        "closed": _("The PDF window is closed"),
        "back": _("The side panel is back in the PDF window: this one can be closed"),
    }
    ui.add_head_html(_PANE_SCRIPT % {"token": json.dumps(token), "texts": json.dumps(texts)})
    with ui.row().classes("w-full items-center no-wrap gap-2 px-3 py-1 bg-primary text-white"):
        ui.link(home[0], home[1]).classes("text-white font-bold no-underline ellipsis max-w-48")
        ui.label(title).classes("ellipsis grow min-w-0 font-medium")
        ui.label("").classes("text-sm opacity-80").props("id=vr-pane-status").mark("pane-status")
        livereload.banner(dense=True)
        _selection_actions(side, bookmarked, here=False)
        ui.button(icon="close_fullscreen").props("flat dense round color=white").on(
            "click", js_handler="() => vrPane.back()"
        ).tooltip(_("Back into the PDF window (closes this one)")).mark("pane-back")
    box = (
        ui.column()
        .classes("w-full p-3 gap-2 overflow-auto")
        .style("height:calc(100vh - 40px)")
        .props("id=vr-pdf-side")
        .mark("pdf-side")
    )
    ui.timer(0, lambda: ui.run_javascript("vrPane.start()"), once=True)  # (once connected)
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
            s,
            state["period"],
            tags,
            lambda: changed(side.person_id, but=side.host),
            manage,
            quote_tool=side.quote_tool,
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


def page_url(
    pub_id: int, period: int | None = None, *, fetch: bool = False, page: int | None = None
) -> str:
    """The viewer page of a paper (with the tags and notes of ``period``), at ``page``."""
    params = {
        k: v for k, v in (("fetch", 1 if fetch else None), ("period", period), ("page", page)) if v
    }
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
    with (
        panel.dialogs,
        transient_dialog(_("Download the PDF?"), width="w-full max-w-xl") as (dlg, _card),
    ):
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


def download_dialog(panel: PublicationsPanel, rows: list[PubStat]) -> None:
    """Download the open-access PDFs of the papers shown (those without one yet)."""
    todo = [s for s in rows if not s.pdf and can_download(s)]
    stored = sum(1 for s in rows if s.pdf)
    with (
        panel.dialogs,
        transient_dialog(_("Download the PDFs"), width="w-full max-w-2xl") as (dlg, _card),
    ):
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
