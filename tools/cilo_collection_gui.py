"""Desktop controls for the resumable Odin-family collection worker."""
from pathlib import Path
import json
import os
import queue
import subprocess
import sys
import threading

import casc_catalog as catalog


def command(action, directory, *args):
    prefix = [sys.executable, '--collection'] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('casc_collection.py'))]
    return [*prefix,action,str(directory),*map(str,args)]


class CollectionWindow:
    def __init__(self,parent,data):
        import tkinter as tk
        from tkinter import ttk
        self.tk,self.ttk,self.data=tk,ttk,data
        self.window=tk.Toplevel(parent); self.window.title('Cilo — Odin collection')
        self.window.geometry('1120x760')
        self.events=queue.Queue(maxsize=256); self.busy=False; self.rows={}; self.cancel=None
        frame=ttk.Frame(self.window,padding=12); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Archive the Odin family',font=('Segoe UI',18,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Shared CASC objects are downloaded once and referenced by every build that needs them.').pack(anchor='w',pady=4)
        bar=ttk.Frame(frame); bar.pack(fill='x',pady=8)
        self.directory=tk.StringVar(value=str(Path.home()/'Downloads'/'Cilo-odin-collection'))
        ttk.Label(bar,text='Collection folder').pack(side='left')
        ttk.Entry(bar,textvariable=self.directory).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(bar,text='Choose…',command=self.choose).pack(side='left')
        options=ttk.Frame(frame); options.pack(fill='x')
        self.profile=tk.StringVar(value='balanced')
        ttk.Combobox(options,textvariable=self.profile,values=['fast','balanced','max'],state='readonly',width=12).pack(side='left')
        ttk.Label(options,text='Download limit this run (GiB; 0 = all)').pack(side='left',padx=(12,5))
        self.limit=tk.StringVar(value='0'); ttk.Entry(options,textvariable=self.limit,width=10).pack(side='left')
        self.deep=tk.BooleanVar(value=True)
        ttk.Checkbutton(options,text='Search archive indexes for missing manifests (slower)',variable=self.deep).pack(side='left',padx=12)
        buttons=ttk.Frame(frame); buttons.pack(fill='x',pady=10)
        self.actions=[]
        for label,action in [('Audit all Odin builds','audit'),('Archive / resume all indexed builds','archive'),('Refresh status','status'),('Verify stored data','verify')]:
            button=ttk.Button(buttons,text=label,command=lambda a=action:self.run(a)); button.pack(side='left',padx=(0,6)); self.actions.append(button)
        self.stop=ttk.Button(buttons,text='Stop',command=self.stop_work,state='disabled'); self.stop.pack(side='left')
        self.summary=tk.StringVar(value='Choose the future storage folder, then audit. Audit downloads metadata only.')
        ttk.Label(frame,textvariable=self.summary,wraplength=1080).pack(anchor='w',pady=6)
        pane=ttk.Frame(frame); pane.pack(fill='both',expand=True)
        self.table=ttk.Treeview(pane,columns=('product','build','state','objects','bytes'),show='headings',selectmode='browse')
        for column,title,width in [('product','Product',180),('build','Build',100),('state','Collection status',210),('objects','Stored / required objects',170),('bytes','Encoded bytes',150)]:
            self.table.heading(column,text=title); self.table.column(column,width=width)
        scroll=ttk.Scrollbar(pane,orient='vertical',command=self.table.yview); self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left',fill='both',expand=True); scroll.pack(side='right',fill='y')
        self.table.bind('<<TreeviewSelect>>',self.details)
        row=ttk.Frame(frame); row.pack(fill='x',pady=8)
        self.restore_button=ttk.Button(row,text='Restore selected encoded build…',command=self.restore); self.restore_button.pack(side='left'); self.actions.append(self.restore_button)
        browse_button=ttk.Button(row,text='Browse selected build files…',command=self.browse_files)
        browse_button.pack(side='left',padx=8); self.actions.append(browse_button)
        import_button=ttk.Button(row,text='Import an existing cache…',command=self.import_cache)
        import_button.pack(side='left'); self.actions.append(import_button)
        ttk.Button(row,text='Browse an archive volume…',command=self.browse_volume).pack(side='left',padx=8)
        self.status=tk.StringVar(value='Ready'); ttk.Label(row,textvariable=self.status).pack(side='right')
        self.log=tk.Text(frame,height=9,wrap='word',state='disabled'); self.log.pack(fill='x')
        ttk.Label(frame,text='An indexed build is a download plan. “Encoded set archived” verifies this scope, not a runnable install.\nKeep the entire collection folder together, including its database, volumes and verified pending resume data.',wraplength=1080).pack(anchor='w',pady=8)
        self.window.protocol('WM_DELETE_WINDOW',self.close); self.window.after(100,self.poll)

    def choose(self):
        from tkinter import filedialog
        if self.busy: return
        path=filedialog.askdirectory(parent=self.window,title='Choose collection storage folder')
        if path: self.directory.set(path); self.run('status')

    def append(self,text):
        self.log.configure(state='normal'); self.log.insert('end',text+'\n'); self.log.see('end')
        if int(self.log.index('end-1c').split('.')[0])>600: self.log.delete('1.0','100.0')
        self.log.configure(state='disabled')

    def run(self,action,extra=()):
        from tkinter import messagebox
        if self.busy: return
        directory=Path(self.directory.get()).expanduser()
        try:
            limit=float(self.limit.get())
            if limit<0: raise ValueError('Download limit must be nonnegative.')
            directory.mkdir(parents=True,exist_ok=True)
            if action=='audit':
                if not self.data: raise ValueError('Load the GitHub catalog in the main window first.')
                snapshot=directory/'catalog.json.gz'; catalog.save_catalog(self.data,snapshot)
                extra=('--catalog',snapshot,*(() if self.deep.get() else ('--loose-only',)))
            elif action=='archive':
                if not (directory/'collection.sqlite3').exists(): raise ValueError('Run the metadata audit first.')
                extra=('--profile',self.profile.get(),'--max-download-gib',str(limit))
            self.cancel=directory/'.stop'; self.cancel.unlink(missing_ok=True)
            args=command(action,directory,'--cancel-file',self.cancel,*extra)
        except (ValueError,OSError) as exc:
            messagebox.showerror('Collection',str(exc),parent=self.window); return
        self.busy=True; self.status.set(action.capitalize()+' running…')
        for button in self.actions: button.configure(state='disabled')
        self.stop.configure(state='normal')
        def work():
            try:
                process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                for line in process.stdout: self.events.put(('log',line.rstrip()))
                self.events.put(('done',(process.wait(),str(directory))))
            except Exception as exc: self.events.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def stop_work(self):
        if self.busy and self.cancel:
            self.cancel.touch(); self.status.set('Stopping after the current operation; verified inputs are retained…')

    def load(self,directory):
        path=Path(directory)/'audit.json'
        if not path.exists(): return
        report=json.loads(path.read_text(encoding='utf-8')); totals=report['totals']
        self.rows={r['id']:r for r in report['builds']}
        self.table.delete(*self.table.get_children())
        for row in sorted(self.rows.values(),key=lambda r:(r['product'],r['build'])):
            self.table.insert('', 'end',iid=row['id'],values=(row['product'],row['build'],row['status'],
                f"{row['archived_objects']:,} / {row['required_objects']:,}",f"{row['encoded_bytes']:,}"))
        self.summary.set(f"{len(self.rows):,} catalog records | {totals['unique_objects']:,} unique objects | "
            f"{totals['unique_encoded_bytes']/10**12:.3f} TB known unique encoded data | "
            f"{totals['exact_sharing_saved_bytes']/10**12:.3f} TB exact sharing savings | "
            f"{totals['archive_bytes']/1024**3:.2f} GiB stored in volumes. Unknown sizes: {totals['unknown_size_objects']:,}.")

    def details(self,_event=None):
        selected=self.table.selection()
        if selected: self.append(json.dumps(self.rows[selected[0]],indent=2))

    def restore(self):
        from tkinter import filedialog,messagebox
        selected=self.table.selection()
        if not selected: return
        row=self.rows[selected[0]]
        if row['status']!='encoded_set_archived':
            messagebox.showinfo('Incomplete build','Archive all required objects before restoring this build.',parent=self.window); return
        path=filedialog.asksaveasfilename(parent=self.window,title='New directory for the encoded build',initialfile=row['product']+'-'+row['build'])
        if path: self.run('restore',('--build-id',selected[0],'--destination',path))

    def browse_volume(self):
        from tkinter import filedialog
        from cilo_browser import ArchiveBrowser
        path=filedialog.askopenfilename(parent=self.window,initialdir=str(Path(self.directory.get())/'volumes'),filetypes=[('Cilo volumes','*.cilo')])
        if path: ArchiveBrowser(self.window,path)

    def browse_files(self):
        selected=self.table.selection()
        if selected: CollectionFiles(self.window,self.directory.get(),selected[0])

    def import_cache(self):
        from tkinter import filedialog
        path=filedialog.askdirectory(parent=self.window,title='Existing downloader folder containing cdn/config and cdn/data')
        if path: self.run('import-cache',('--source',path))

    def poll(self):
        try:
            for _ in range(100):
                kind,value=self.events.get_nowait()
                if kind=='log': self.append(value)
                else:
                    self.busy=False
                    for button in self.actions: button.configure(state='normal')
                    self.stop.configure(state='disabled')
                    if self.cancel: self.cancel.unlink(missing_ok=True)
                    if kind=='error': self.append(value); self.status.set('Failed; see log')
                    else:
                        code,directory=value; self.status.set('Finished' if code==0 else 'Stopped or failed; see log')
                        try: self.load(directory)
                        except (OSError,ValueError) as exc: self.append(str(exc))
        except queue.Empty: pass
        if self.window.winfo_exists(): self.window.after(100,self.poll)

    def close(self):
        if self.busy:
            self.stop_work(); return
        self.window.destroy()


class CollectionFiles:
    """Named files reconstructed from local manifests; no network requests."""
    def __init__(self,parent,directory,bid):
        import tkinter as tk
        from tkinter import ttk
        self.directory,self.bid=directory,bid
        self.window=tk.Toplevel(parent); self.window.title('Collection files — offline'); self.window.geometry('1050x720')
        self.events=queue.Queue(); self.busy=False; self.files=[]; self.visible=[]
        frame=ttk.Frame(self.window,padding=12); frame.pack(fill='both',expand=True)
        self.query=tk.StringVar(); ttk.Entry(frame,textvariable=self.query).pack(fill='x')
        self.query.trace_add('write',lambda *_:self.filter())
        self.status=tk.StringVar(value='Reading local manifests…'); ttk.Label(frame,textvariable=self.status).pack(anchor='w',pady=8)
        self.table=ttk.Treeview(frame,columns=('path','size','spans'),show='headings',height=12)
        for col,width in [('path',700),('size',130),('spans',70)]:
            self.table.heading(col,text=col.title()); self.table.column(col,width=width)
        self.table.pack(fill='both',expand=True)
        self.table.bind('<<TreeviewSelect>>',self.details)
        self.button=ttk.Button(frame,text='Preview selected stored file (first 64 KiB)',command=self.preview)
        self.button.pack(anchor='w',pady=8)
        self.hex=tk.BooleanVar(value=False); ttk.Checkbutton(frame,text='Hex view',variable=self.hex).pack(anchor='w')
        self.text=tk.Text(frame,height=14,wrap='none'); self.text.pack(fill='both',expand=True)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.run('files'); self.window.after(100,self.poll)

    def run(self,action,*extra):
        if self.busy: return
        self.busy=True; self.button.configure(state='disabled')
        args=command(action,self.directory,'--build-id',self.bid,*extra)
        def work():
            try:
                result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                if result.returncode: raise ValueError(result.stderr or result.stdout)
                self.events.put((action,json.loads(result.stdout)))
            except Exception as exc: self.events.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def filter(self):
        query=self.query.get().casefold(); matches=[f for f in self.files if query in f['path'].casefold()]
        self.visible=matches[:3000]; self.table.delete(*self.table.get_children())
        for i,file in enumerate(self.visible): self.table.insert('','end',iid=str(i),values=(file['path'],f"{file['size']:,}",len(file['spans'])))
        self.status.set(f'{len(matches):,} matching files; showing {len(self.visible):,}. Filter to narrow the list. Payloads may not yet be stored.')

    def details(self,_event=None):
        selected=self.table.selection()
        if selected:
            self.text.delete('1.0','end'); self.text.insert('1.0',json.dumps(self.visible[int(selected[0])],indent=2))

    def preview(self):
        selected=self.table.selection()
        if selected: self.run('preview','--path',self.visible[int(selected[0])]['path'])

    def poll(self):
        try:
            kind,result=self.events.get_nowait(); self.busy=False; self.button.configure(state='normal')
            if kind=='files': self.files=result['files']; self.filter()
            elif kind=='error': self.status.set(result[:500])
            else:
                from cilo_browser import hex_view,text_view
                self.text.delete('1.0','end'); raw=bytes(result['bytes'])
                self.text.insert('1.0',hex_view(raw) if self.hex.get() else text_view(raw))
                self.status.set(result['integrity']+(' Preview truncated.' if result['truncated'] else ''))
        except queue.Empty: pass
        if self.window.winfo_exists(): self.window.after(100,self.poll)

    def close(self):
        if self.busy: self.status.set('Wait for the local read to finish before closing.'); return
        self.window.destroy()
