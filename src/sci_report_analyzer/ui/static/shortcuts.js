// What the PDF window and its side pane share: the shortcuts on the selection, and whether
// the user is typing (then no shortcut).
window.vrKeys = {
  // A key: the event of the action on the selection (that of a header button).
  selection: {
    q: 'vr-pdf-quote',  // (into the note last used)
    e: 'vr-pdf-excerpt',  // (only with excerpts: the button there)
    b: 'vr-pdf-bookmark',
    f: 'vr-pdf-find',  // (without a selection: says so)
    l: 'vr-pdf-tag-list',  // (the papers of a list, tagged)
  },
  // The event of a key on the selection (none: not one, or not on this page).
  event(key) {
    const name = this.selection[key];
    return name === 'vr-pdf-excerpt' && !document.getElementById(name) ? null : name;
  },
  typing(t) {
    return !!(t && (t.isContentEditable || (t.closest && t.closest('input, textarea, select'))));
  },
  plain(ev) { return !(ev.ctrlKey || ev.metaKey || ev.altKey || ev.repeat); },
};
