#!/usr/bin/env python3
"""Small Tk desktop browser for the CASC downloader (Python 3.10+)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading

import casc_catalog as catalog
import casc_probe as probe


def cache_path():
    root = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) if os.name == 'nt' else Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))
    return root / 'Cilo' / 'casc-availability.json.gz'


def download_command(row, bases, output, action='extract', pattern='*', limit=0, archive=False, profile='balanced', cancel=None):
    if action not in ('extract', 'mirror', 'list') or limit < 0:
        raise ValueError('invalid download action or file limit')
    if not row.get('build_config'):
        raise ValueError('this catalog record has no build configuration key')
    # Use the selected exact hashes. A catalog directory can contain several
    # versions/regions; reselecting by build number could fetch a different row.
    launcher = ([sys.executable, '--cli'] if getattr(sys, 'frozen', False) else
                [sys.executable, '-u', str(Path(__file__).with_name('casc_download.py'))])
    args = launcher + [action,
            '--product', row['product'], '--build-config', row['build_config'],
            '--archive' if archive else '--output', str(output), '--limit', str(limit)]
    if archive:
        if action == 'list' or profile not in ('fast', 'balanced', 'max'):
            raise ValueError('invalid archive action/profile')
        args += ['--compression', profile]
        if cancel:
            args += ['--cancel-file', str(cancel)]
    if row.get('cdn_config'):
        args += ['--cdn-config', row['cdn_config']]
    for base in bases:
        args += ['--cdn-base', base]
    if action in ('extract', 'list') and pattern:
        args += ['--pattern', pattern]
    return args


class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root = tk, ttk, root
        if os.name != 'nt' and 'clam' in ttk.Style(root).theme_names():
            ttk.Style(root).theme_use('clam')
        self.events = queue.Queue(maxsize=256)
        self.data, self.visible, self.busy = None, [], False
        self.process = None
        self.collection_windows = []
        self.cancel_path = None
        self.github_token = None
        self.cache = cache_path()
        root.title('Cilo - CASC Build Browser')
        root.geometry('1120x820')
        root.minsize(850, 660)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='CASC Build Browser', font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Browse exact builds, check their availability, and download verified files.').pack(anchor='w', pady=(2, 12))
        top = ttk.Frame(frame)
        top.pack(fill='x')
        self.update_button = ttk.Button(top, text='Update availability from GitHub', command=self.update_cache)
        self.update_button.pack(side='right')
        ttk.Button(top, text='Odin collection…', command=self.open_collection).pack(side='right', padx=8)
        self.cache_status = tk.StringVar(value='Loading local availability cache...')
        ttk.Label(top, textvariable=self.cache_status).pack(side='left')
        filters = ttk.Frame(frame)
        filters.pack(fill='x', pady=12)
        ttk.Label(filters, text='Product').pack(side='left')
        self.product = tk.StringVar(value='odin')
        self.products = ttk.Combobox(filters, textvariable=self.product, width=25)
        self.products.pack(side='left', padx=(8, 18))
        ttk.Label(filters, text='Find build / version / config').pack(side='left')
        self.search = tk.StringVar()
        ttk.Entry(filters, textvariable=self.search).pack(side='left', fill='x', expand=True, padx=(8, 0))
        self.product.trace_add('write', lambda *_: self.filter_rows())
        self.search.trace_add('write', lambda *_: self.filter_rows())
        table = ttk.Frame(frame)
        table.pack(fill='both', expand=True)
        self.table = ttk.Treeview(table, columns=('product', 'build', 'version', 'state', 'checked'), show='headings', height=11, selectmode='browse')
        for column, title, width in [('product', 'Product', 125), ('build', 'Build', 100), ('version', 'Version', 190),
                                     ('state', 'Availability evidence', 180), ('checked', 'Checked (UTC)', 150)]:
            self.table.heading(column, text=title)
            self.table.column(column, width=width, minwidth=65)
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.table.tag_configure('sample_verified', foreground='#14623a')
        self.table.tag_configure('sample_partial', foreground='#8c4c05')
        self.table.tag_configure('config_unverified', foreground='#8c4c05')
        self.table.tag_configure('not_retrieved', foreground='#666666')
        self.table.bind('<<TreeviewSelect>>', self.show_selection)
        self.row_status = tk.StringVar()
        ttk.Label(frame, textvariable=self.row_status).pack(anchor='w', pady=(4, 8))
        self.details = tk.Text(frame, height=5, wrap='word', relief='flat', state='disabled')
        self.details.pack(fill='x')
        controls = ttk.Frame(frame)
        controls.pack(fill='x', pady=(12, 6))
        ttk.Label(controls, text='Save builds in').pack(side='left')
        self.destination = tk.StringVar(value=str(Path.home() / 'Downloads' / 'Cilo-builds'))
        ttk.Entry(controls, textvariable=self.destination).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(controls, text='Browse...', command=self.browse).pack(side='left')
        options = ttk.Frame(frame)
        options.pack(fill='x', pady=6)
        self.action = tk.StringVar(value='Extract named files')
        ttk.Combobox(options, textvariable=self.action, values=['Extract named files', 'Mirror encoded content'], state='readonly', width=24).pack(side='left')
        ttk.Label(options, text='Filename pattern').pack(side='left', padx=(16, 6))
        self.pattern = tk.StringVar(value='*')
        ttk.Entry(options, textvariable=self.pattern, width=23).pack(side='left')
        ttk.Label(options, text='File limit (0 = all)').pack(side='left', padx=(16, 6))
        self.limit = tk.StringVar(value='0')
        ttk.Spinbox(options, from_=0, to=999999, textvariable=self.limit, width=8).pack(side='left')
        archive_options = ttk.Frame(frame); archive_options.pack(fill='x', pady=4)
        self.archive_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(archive_options, text='Download directly to a .cilo archive', variable=self.archive_mode).pack(side='left')
        self.compression = tk.StringVar(value='balanced')
        ttk.Combobox(archive_options, textvariable=self.compression, values=['fast', 'balanced', 'max'], state='readonly', width=11).pack(side='left', padx=10)
        ttk.Button(archive_options, text='Browse a .cilo archive...', command=self.open_archive).pack(side='right')
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=(6, 10))
        self.check_button = ttk.Button(buttons, text='Check selected build', command=self.check_build)
        self.check_button.pack(side='left')
        self.list_button = ttk.Button(buttons, text='List files', command=lambda: self.download('list'))
        self.list_button.pack(side='left', padx=8)
        self.download_button = ttk.Button(buttons, text='Download', command=self.download)
        self.download_button.pack(side='left')
        self.cancel_button = ttk.Button(buttons, text='Cancel archive download', command=self.cancel_archive, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.progress = ttk.Progressbar(buttons, mode='indeterminate', length=140)
        self.progress.pack(side='right')
        self.status = tk.StringVar(value='Ready')
        ttk.Label(buttons, textvariable=self.status).pack(side='right', padx=12)
        self.log = tk.Text(frame, height=8, wrap='word', state='disabled')
        self.log.pack(fill='x')
        ttk.Label(frame, text='Samples do not verify a full build. Checks use temporary storage and remove probe files afterwards.').pack(anchor='w', pady=(8, 0))
        self.load_local()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def open_collection(self):
        from cilo_collection_gui import CollectionWindow
        self.collection_windows.append(CollectionWindow(self.root, self.data))

    def set_text(self, widget, text, append=False):
        widget.configure(state='normal')
        if not append:
            widget.delete('1.0', 'end')
        widget.insert('end', text)
        if append:
            # A long download must not grow the log indefinitely.
            if int(widget.index('end-1c').split('.')[0]) > 800:
                widget.delete('1.0', '200.0')
            widget.see('end')
        widget.configure(state='disabled')

    def load_local(self):
        for path in (self.cache, catalog.BUNDLED_CATALOG):
            if not path.exists():
                continue
            try:
                self.install_catalog(catalog.load_catalog(path))
                return
            except (OSError, ValueError, EOFError) as exc:
                self.set_text(self.log, f'Could not load {path}: {exc}\n', True)
        self.cache_status.set('No local cache. Use "Update availability from GitHub".')

    def install_catalog(self, data):
        self.data = data
        products = sorted({r['product'] for r in data['builds']})
        self.products.configure(values=['All products'] + products)
        self.cache_status.set(f"{len(data['builds']):,} builds | {len(products)} products | cache {data['generated_at_utc'][:10]}")
        self.filter_rows()

    def filter_rows(self):
        if not self.data:
            return
        product, query = self.product.get().lower().strip(), self.search.get().lower().strip()
        rows = [r for r in self.data['builds']
                if (not product or product == 'all products' or r['product'].lower() == product)
                and (not query or query in ' '.join(str(r.get(k, '')) for k in ('build', 'version', 'build_config')).lower())]
        rows.sort(key=lambda r: (int(r['build']) if r['build'].isdigit() else 0, r['build']), reverse=True)
        self.visible = rows[:1500]
        self.table.delete(*self.table.get_children())
        for i, row in enumerate(self.visible):
            self.table.insert('', 'end', iid=str(i), values=(row['product'], row['build'], row['version'],
                              catalog.LABELS[row['availability']], row['checked_at_utc'][:19].replace('T', ' ')), tags=(row['availability'],))
        self.row_status.set(f'{len(rows):,} matching builds' + (' | showing first 1,500; narrow the search' if len(rows) > 1500 else ''))
        self.set_text(self.details, 'Select a build to see its evidence and exact configuration.')

    def selected(self):
        selection = self.table.selection()
        if not selection:
            raise ValueError('Select a build first.')
        return self.visible[int(selection[0])]

    def show_selection(self, _event=None):
        try:
            row = self.selected()
        except ValueError:
            return
        observation = row.get('probe', row.get('observation', {}))
        summary = observation.get('scope', 'Imported US-only config checks; payloads were not checked by this observation.')
        text = f"{row['product']} | {row['version']} | {catalog.LABELS[row['availability']]}\n"
        text += f"Checked: {row['checked_at_utc']} | Path: {row.get('cdn_path') or 'unknown'}\n"
        text += f"Build config: {row.get('build_config')} | CDN config: {row.get('cdn_config')}\n{summary}"
        if 'error' in observation:
            text += '\nLast error: ' + observation['error'][:600]
        elif 'source' in observation:
            text += f"\nSource: {observation['source']} | build: {observation.get('build_config')} | CDN: {observation.get('cdn_config')}"
        if row.get('latest_attempt'):
            latest = row['latest_attempt']
            text += f"\nLater attempt {latest['checked_at_utc']}: {catalog.LABELS[latest['stage']]}"
            if latest.get('error'):
                text += ' | ' + latest['error'][:240]
        self.set_text(self.details, text)

    def browse(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title='Choose where to save downloaded builds')
        if path:
            self.destination.set(path)

    def start(self, name, work):
        if self.busy:
            return
        self.busy = True
        self.status.set(name)
        self.progress.start()
        for button in (self.update_button, self.check_button, self.list_button, self.download_button):
            button.configure(state='disabled')
        def run():
            try:
                work()
            except Exception as exc:
                self.events.put(('error', str(exc)))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=run, daemon=False).start()

    def update_cache(self):
        def work():
            try:
                data = catalog.update_catalog(self.cache, token=self.github_token)
            except catalog.CatalogAccessError as exc:
                self.events.put(('github_access', str(exc)))
                return
            self.events.put(('catalog', data))
            self.events.put(('log', f'Updated {len(data["builds"]):,} records from GitHub. Cache: {self.cache}\n'))
        self.start('Updating cache...', work)

    def check_build(self):
        try:
            row = self.selected().copy()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        def work():
            self.events.put(('log', f'Checking {row["product"]} {row["build"]}; probe downloads will be removed.\n'))
            result = probe.probe_build(row, samples=3, max_sample_mib=32)
            self.events.put(('probe', result))
        self.start('Checking build...', work)

    def download(self, command=None):
        if self.busy:
            return
        try:
            row = self.selected().copy()
            action = command or ('extract' if self.action.get() == 'Extract named files' else 'mirror')
            limit = int(self.limit.get())
            if limit < 0 or not self.destination.get().strip():
                raise ValueError('Choose a destination and a nonnegative file limit.')
            pattern = self.pattern.get()
            output = Path(self.destination.get()).expanduser() / row['product'] / f"{row['build']}-{row['build_config'][:8]}"
            archive_mode = self.archive_mode.get() and action != 'list'
            profile = self.compression.get()
            if archive_mode:
                from cilo_bridge import find_cilo
                find_cilo()  # report a missing native tool before starting any download
                output = output.with_suffix('.cilo')
        except (ValueError, TypeError) as exc:
            self.status.set(str(exc))
            return
        control = tempfile.TemporaryDirectory(prefix='cilo-control-') if archive_mode else None
        self.cancel_path = Path(control.name) / 'cancel' if control else None
        cancel = self.cancel_path
        def work():
            try:
                bases = probe.build_bases(row)
                args = download_command(row, bases, output, action, pattern, limit, archive_mode, profile, cancel)
                self.events.put(('log', f'{action}: {row["product"]} {row["build"]} -> {output}\n'))
                with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding='utf-8', errors='replace',
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as child:
                    self.process = child
                    for line in child.stdout:
                        self.events.put(('log', line))
                    code = child.wait()
                    if code:
                        raise RuntimeError(f'Downloader exited with status {code}; see the log. ' +
                                           ('Incomplete archive and staging files were discarded.' if archive_mode else 'Verified downloads can be resumed.'))
                self.events.put(('log', f'Completed. Saved to {output}\n'))
            finally:
                self.process = None
                self.cancel_path = None
                if control:
                    control.cleanup()
        self.start('Downloading...' if action != 'list' else 'Listing files...', work)
        if archive_mode:
            self.cancel_button.configure(state='normal')

    def cancel_archive(self):
        if self.cancel_path:
            try:
                self.cancel_path.touch()
                self.status.set('Cancelling and cleaning up; an active network read may need its timeout.')
                self.cancel_button.configure(state='disabled')
            except OSError as exc:
                self.status.set(str(exc))

    def open_archive(self):
        from cilo_browser import ArchiveBrowser
        ArchiveBrowser(self.root).choose()

    def poll(self):
        try:
            for _ in range(80):
                kind, value = self.events.get_nowait()
                if kind == 'catalog':
                    self.install_catalog(value)
                elif kind == 'probe':
                    catalog.merge_probes(self.data, [value])
                    try:
                        catalog.save_catalog(self.data, self.cache)
                    except OSError as exc:
                        self.set_text(self.log, f'Could not save observation: {exc}\n', True)
                    self.filter_rows()
                    self.set_text(self.log, json.dumps(value, indent=2) + '\nProbe files cleaned up.\n', True)
                elif kind in ('log', 'error'):
                    self.set_text(self.log, value + ('\n' if kind == 'error' else ''), True)
                elif kind == 'github_access':
                    from tkinter import simpledialog
                    self.set_text(self.log, value + '\n', True)
                    token = simpledialog.askstring('GitHub repository access',
                        'The cache is in IDARETW/Cilo. Enter a GitHub token with Contents read access '
                        'to this repository, or Cancel to keep the bundled cache. '
                        'The token is kept only in memory for this session.', show='*', parent=self.root)
                    if token and token.strip():
                        self.github_token = token.strip()
                        # The current worker's done event precedes this idle callback.
                        self.root.after(150, self.update_cache)
                elif kind == 'done':
                    self.busy = False
                    self.progress.stop()
                    self.status.set('Ready - see log for results')
                    self.cancel_button.configure(state='disabled')
                    for button in (self.update_button, self.check_button, self.list_button, self.download_button):
                        button.configure(state='normal')
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if any(window.busy for window in self.collection_windows):
            self.status.set('Stop the collection worker and wait for it before closing.')
            return
        if self.busy:
            self.status.set('A task is running; close after it finishes.')
            return
        self.root.destroy()


def main():
    try:
        import tkinter as tk
    except ImportError as exc:
        raise SystemExit(f'The GUI requires Python with Tk and a desktop display. On Debian/Ubuntu install python3-tk. {exc}')
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise SystemExit(f'Cannot open the desktop GUI: {exc}. The command-line downloader works without a display.')
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
