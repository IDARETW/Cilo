"""Read-only .cilo file browser with verified text/hex previews."""
from __future__ import annotations

import datetime
from pathlib import Path
import queue
import threading

from cilo_bridge import native_json


def hex_view(data):
    lines = []
    for offset in range(0, len(data), 16):
        row = data[offset:offset + 16]
        lines.append(f'{offset:08x}  {row.hex(" "):<47}  ' + ''.join(chr(b) if 32 <= b < 127 else '.' for b in row))
    return '\n'.join(lines)


def text_view(data):
    return ''.join(c if c in '\n\r\t' or ord(c) >= 32 and not 127 <= ord(c) <= 159 else '.'
                   for c in data.decode('utf-8', errors='replace'))


class ArchiveBrowser:
    def __init__(self, parent, path=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.window = tk.Toplevel(parent)
        self.window.title('Cilo Archive Browser')
        self.window.geometry('1060x760')
        self.window.minsize(760, 540)
        self.events, self.busy, self.archive = queue.Queue(), False, None
        self.files, self.visible = [], []
        frame = ttk.Frame(self.window, padding=12); frame.pack(fill='both', expand=True)
        top = ttk.Frame(frame); top.pack(fill='x')
        self.open_button = ttk.Button(top, text='Open .cilo archive...', command=self.choose)
        self.open_button.pack(side='left')
        self.verify_button = ttk.Button(top, text='Verify whole archive', command=self.verify, state='disabled')
        self.verify_button.pack(side='left', padx=8)
        self.summary = tk.StringVar(value='No archive selected')
        ttk.Label(frame, textvariable=self.summary, wraplength=990).pack(anchor='w', pady=8)
        search = ttk.Frame(frame); search.pack(fill='x')
        ttk.Label(search, text='Find file').pack(side='left')
        self.query = tk.StringVar()
        ttk.Entry(search, textvariable=self.query).pack(side='left', fill='x', expand=True, padx=8)
        self.query.trace_add('write', lambda *_: self.filter())
        table = ttk.Frame(frame); table.pack(fill='both', expand=True, pady=8)
        self.table = ttk.Treeview(table, columns=('path', 'size', 'modified'), show='headings', selectmode='browse', height=9)
        for column, title, width in [('path', 'Path inside archive', 570), ('size', 'Bytes', 130), ('modified', 'Modified (UTC)', 170)]:
            self.table.heading(column, text=title); self.table.column(column, width=width)
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        self.table.bind('<<TreeviewSelect>>', self.selection_changed)
        self.table.bind('<Double-1>', lambda _event: self.preview())
        self.details = tk.StringVar(value='Select a file to see details.')
        ttk.Label(frame, textvariable=self.details, wraplength=990).pack(anchor='w')
        actions = ttk.Frame(frame); actions.pack(fill='x', pady=8)
        self.preview_button = ttk.Button(actions, text='Preview selected file', command=self.preview, state='disabled')
        self.preview_button.pack(side='left')
        self.limit = tk.StringVar(value='64 KiB')
        ttk.Combobox(actions, textvariable=self.limit, values=['64 KiB', '256 KiB', '1 MiB', '4 MiB'], state='readonly', width=10).pack(side='left', padx=8)
        ttk.Label(actions, text='Reads directly from the archive. No extracted files are created.').pack(side='left')
        notebook = ttk.Notebook(frame); notebook.pack(fill='both', expand=True)
        self.text, self.hex = [], []
        for title, attr in [('Text (UTF-8)', 'text'), ('Hex', 'hex')]:
            page = ttk.Frame(notebook); notebook.add(page, text=title)
            widget = tk.Text(page, wrap='none', height=12, font=('Consolas', 10), state='disabled')
            sy = ttk.Scrollbar(page, orient='vertical', command=widget.yview)
            sx = ttk.Scrollbar(page, orient='horizontal', command=widget.xview)
            widget.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
            sy.pack(side='right', fill='y'); sx.pack(side='bottom', fill='x'); widget.pack(fill='both', expand=True)
            setattr(self, attr, widget)
        self.status = tk.StringVar(value='Ready')
        ttk.Label(frame, textvariable=self.status, wraplength=990).pack(anchor='w', pady=(8, 0))
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_id = self.window.after(100, self.poll)
        if path:
            self.load(path)

    def choose(self):
        from tkinter import filedialog
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.window, title='Open a Cilo archive', filetypes=[('Cilo archives', '*.cilo'), ('All files', '*')])
        if path:
            self.load(path)

    def start(self, label, kind, work):
        if self.busy:
            return
        self.busy = True; self.status.set(label)
        for button in (self.open_button, self.verify_button, self.preview_button):
            button.configure(state='disabled')
        def run():
            try:
                self.events.put((kind, work()))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=run, daemon=False).start()

    def load(self, path):
        archive = str(Path(path).resolve())
        self.start('Reading archive metadata...', 'loaded', lambda: (archive, native_json('contents', archive)))

    def filter(self):
        query = self.query.get().casefold()
        matches = [f for f in self.files if query in f['path'].casefold()]
        self.visible = matches[:3000]
        self.table.delete(*self.table.get_children())
        for i, entry in enumerate(self.visible):
            try:
                modified = datetime.datetime.fromtimestamp(entry['modified_seconds'], datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if entry['modified_seconds'] else 'Not provided'
            except (ValueError, OverflowError, OSError):
                modified = str(entry['modified_seconds'])
            self.table.insert('', 'end', iid=str(i), values=(entry['path'], f"{entry['size']:,}", modified))
        self.details.set(f'{len(matches):,} matching files' + ('; showing 3,000. Narrow the search.' if len(matches) > 3000 else ''))

    def selected(self):
        selection = self.table.selection()
        return self.visible[int(selection[0])] if selection else None

    def selection_changed(self, _event=None):
        entry = self.selected()
        if entry:
            self.details.set(f"{entry['path']} | {entry['size']:,} bytes | {entry['chunks']:,} chunks\nBLAKE3: {entry['blake3']}")
            if not self.busy:
                self.preview_button.configure(state='normal')

    def preview(self):
        entry = self.selected()
        if not entry or self.busy:
            return
        limit = {'64 KiB': 65536, '256 KiB': 262144, '1 MiB': 1048576, '4 MiB': 4194304}[self.limit.get()]
        archive, name = self.archive, entry['path']
        self.start('Reading and checking preview blocks...', 'preview',
                   lambda: native_json('preview', archive, '--max-bytes', str(limit), '--', name))

    def verify(self):
        if self.archive and not self.busy:
            archive = self.archive
            self.start('Verifying every block, chunk and file...', 'verified', lambda: native_json('verify', archive))

    def poll(self):
        try:
            kind, result = self.events.get_nowait()
            if kind == 'loaded':
                self.archive, info = result; self.files = info['files']; self.filter()
                for widget in (self.text, self.hex):
                    widget.configure(state='normal'); widget.delete('1.0', 'end'); widget.configure(state='disabled')
                self.summary.set(f"{self.archive}\n{len(self.files):,} files | {info['input_bytes']:,} original bytes | {info['archive_bytes']:,} archive bytes | format v{info['version']}")
                self.status.set('Manifest checksum verified. Use previews or Verify whole archive to check payloads.')
            elif kind == 'preview':
                data = bytes(result['bytes'])
                for widget, value in [(self.text, text_view(data)), (self.hex, hex_view(data))]:
                    widget.configure(state='normal'); widget.delete('1.0', 'end'); widget.insert('end', value); widget.configure(state='disabled')
                self.status.set(f"{result['file']['path']}: {len(data):,} preview bytes. " +
                                ('Full file checksum verified.' if result['file_hash_verified'] else 'Preview blocks/chunks verified; file truncated for display.'))
            elif kind == 'verified':
                self.status.set(f"Verified all {result['files']:,} files and {result['input_bytes']:,} logical bytes.")
            else:
                self.status.set(result)
            self.busy = False; self.open_button.configure(state='normal')
            self.verify_button.configure(state='normal' if self.archive else 'disabled')
            self.preview_button.configure(state='normal' if self.selected() else 'disabled')
        except queue.Empty:
            pass
        self.poll_id = self.window.after(100, self.poll)

    def close(self):
        if self.busy:
            self.status.set('A read is running; close after it finishes.')
        else:
            self.window.after_cancel(self.poll_id)
            self.window.destroy()
