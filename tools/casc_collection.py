#!/usr/bin/env python3
"""Resumable Odin-family preservation: SQLite references + immutable Cilo volumes.

An indexed DOWNLOAD is a plan, not proof that all its payloads are available.
Only fully verified, published objects count toward archived build coverage.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid

import casc_catalog as catalog
import casc_download as casc
import casc_probe
from cilo_bridge import ArchiveStream, check_cancelled, find_cilo, native_json

SCHEMA = 1
KEY = re.compile(r'[0-9a-f]{32}\Z')
VOLUME = re.compile(r'[0-9a-f]{32}\.cilo\Z')
SCOPE = ('DOWNLOAD-listed encoded objects (all tags), build manifest references and configurations. '
         'Patch transport history and recursive TVFS mount interpretation are not included. '
         'Metadata indexing does not establish payload availability or a runnable installation.')


def emit(event, **fields):
    print(json.dumps({'event': event, **fields}), flush=True)


def identity(row):
    values = [row.get(k) for k in ('product', 'build', 'catalog_build', 'build_config', 'cdn_config', 'region')]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def member(kind, key):
    casc.check(kind in ('config', 'data') and KEY.fullmatch(key), 'invalid collection object identity')
    return 'cdn/' + casc.key_path(kind, key)


def sync_directory(path):
    if os.name!='nt':
        descriptor=os.open(path,os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)


def atomic_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.json-', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(json.dumps(value, ensure_ascii=False, indent=2).encode())
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def validate(path, kind, key, size=None):
    casc.check(size is None or path.stat().st_size == size, 'collection object size mismatch')
    if kind == 'config':
        casc.check(casc.hash_file(path, 'md5') == key, 'collection configuration checksum mismatch')
    else:
        casc.verify_blte(path, key)
    return casc.hash_file(path)


def recovered_fallback(row):
    known=(row.get('probe') or {}).get('recovered_configs',{})
    if not known: return None
    def url(kind,key):
        casc.check(key in known,'no previous verified public-cache evidence for this key')
        return casc.blizztrack_config_url(row['product'],kind,key)
    return url


class Collection:
    def __init__(self, root, *, executable=None, cancel=None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.executable, self.cancel = executable, cancel
        self.db = sqlite3.connect(self.root / 'collection.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        casc.check(version in (0, SCHEMA), 'unsupported collection database version')
        self.db.executescript('''
            PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS builds (
                id TEXT PRIMARY KEY, row_json TEXT NOT NULL, bases TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', checked TEXT, error TEXT, details TEXT);
            CREATE TABLE IF NOT EXISTS objects (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL, size INTEGER,
                sha256 TEXT, volume TEXT, error TEXT, UNIQUE(kind,key));
            CREATE TABLE IF NOT EXISTS refs (
                build TEXT NOT NULL REFERENCES builds(id), object INTEGER NOT NULL REFERENCES objects(id),
                role TEXT NOT NULL, PRIMARY KEY(build,object)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS refs_object ON refs(object,build);
            CREATE TABLE IF NOT EXISTS volumes (name TEXT PRIMARY KEY, size INTEGER NOT NULL, sha256 TEXT NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recipes (
                digest BLOB PRIMARY KEY, size INTEGER NOT NULL) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS recipe_refs (
                build TEXT NOT NULL REFERENCES builds(id), digest BLOB NOT NULL REFERENCES recipes(digest),
                copies INTEGER NOT NULL, PRIMARY KEY(build,digest)) WITHOUT ROWID;
            PRAGMA user_version=1;
        ''')
        if 'error' not in {r[1] for r in self.db.execute('PRAGMA table_info(volumes)')}:
            self.db.execute('ALTER TABLE volumes ADD COLUMN error TEXT')
            self.db.commit()
        for name in ('volumes', 'pending', 'scratch'):
            (self.root / name).mkdir(exist_ok=True)

    def close(self):
        self.db.close()

    @contextmanager
    def lock(self):
        """OS releases the lock after a crash; the harmless lock file is permanent."""
        with (self.root / 'collection.lock').open('a+b') as file:
            if file.tell()==0: file.write(b'\0'); file.flush()
            file.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError('This collection is already open in another worker.') from exc
            try:
                # Only task-owned scratch directories are removed; verified resume
                # objects and published volumes are never treated as temporary.
                for path in (self.root / 'scratch').glob('work-*'):
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                yield self
            finally:
                for path in (self.root / 'scratch').glob('work-*'):
                    if path.is_dir() and not path.is_symlink(): shutil.rmtree(path,ignore_errors=True)
                if os.name == 'nt':
                    file.seek(0); msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(file, fcntl.LOCK_UN)

    def pending(self, kind, key):
        member(kind, key)
        return self.root / 'pending' / f'{kind}-{key}'

    def object(self, kind, key, size=None):
        member(kind, key)
        old = self.db.execute('SELECT id,size FROM objects WHERE kind=? AND key=?', (kind, key)).fetchone()
        if old:
            casc.check(size is None or old['size'] is None or size == old['size'], f'conflicting sizes for {kind}/{key}')
            if old['size'] is None and size is not None:
                self.db.execute('UPDATE objects SET size=? WHERE id=?', (size, old['id']))
            return old['id']
        return self.db.execute('INSERT INTO objects(kind,key,size) VALUES(?,?,?)', (kind,key,size)).lastrowid

    def retain(self, kind, key, path):
        sha = validate(path, kind, key)
        obj = self.object(kind,key,path.stat().st_size)
        old = self.db.execute('SELECT sha256,volume FROM objects WHERE id=?', (obj,)).fetchone()
        casc.check(old['sha256'] is None or old['sha256'] == sha, 'same CASC key has different SHA-256 bytes')
        if not old['volume']:
            target = self.pending(kind,key)
            if not target.exists():
                # Same filesystem, publish only after full validation.
                os.replace(path, target)
                with target.open('r+b') as file: os.fsync(file.fileno())
                sync_directory(target.parent)
            else:
                casc.check(casc.hash_file(target) == sha, 'corrupt pending object; run verify or remove this object and retry')
        self.db.execute('UPDATE objects SET sha256=?,error=NULL WHERE id=?', (sha,obj))
        return obj

    def register(self, rows, bases):
        with self.db:
            for row in rows:
                self.db.execute('''INSERT INTO builds(id,row_json,bases) VALUES(?,?,?)
                    ON CONFLICT(id) DO UPDATE SET row_json=excluded.row_json,bases=excluded.bases''',
                    (identity(row),json.dumps(row),json.dumps(bases[row['product']])))

    def accept_audit(self, rows, result):
        with self.db:
            ids = []
            for kind,key,size,role in result.get('objects', []):
                ids.append((self.object(kind,key,size),role))
            for kind,key,path in result.get('retained', []):
                self.retain(kind,key,Path(path))
            for row in rows:
                bid = identity(row)
                previous = self.db.execute('SELECT state FROM builds WHERE id=?',(bid,)).fetchone()[0]
                # A failed re-probe never destroys a verified historical plan.
                if result['state'] == 'indexed' or previous != 'indexed':
                    self.db.execute('DELETE FROM refs WHERE build=?',(bid,))
                    self.db.executemany('INSERT OR IGNORE INTO refs VALUES(?,?,?)', ((bid,obj,role) for obj,role in ids))
                    if row.get('cdn_config'):
                        obj = self.object('config',row['cdn_config'])
                        self.db.execute('INSERT OR IGNORE INTO refs VALUES(?,?,?)',(bid,obj,'cdn-config'))
                    self.db.execute('UPDATE builds SET state=?,checked=?,error=?,details=? WHERE id=?',
                        (result['state'],catalog.utc_now(),result.get('error'),json.dumps(result.get('details',{})),bid))
                    if 'recipes' in result:
                        self.db.execute('DELETE FROM recipe_refs WHERE build=?',(bid,))
                        self.db.executemany('INSERT OR IGNORE INTO recipes VALUES(?,?)',((digest,size) for digest,size,copies in result['recipes']))
                        self.db.executemany('INSERT INTO recipe_refs VALUES(?,?,?)',((bid,digest,copies) for digest,size,copies in result['recipes']))
                elif result.get('error'):
                    self.db.execute('UPDATE builds SET error=? WHERE id=?',
                                    ('Re-probe at '+catalog.utc_now()+' failed; retained plan and its verification time: '+result['error'],bid))

    def publish(self, profile='balanced', volume_bytes=4*1024**3):
        """Journal before archive publication; transaction before deleting pending bytes."""
        selected, total = [], 0
        for row in self.db.execute('SELECT * FROM objects WHERE volume IS NULL AND sha256 IS NOT NULL ORDER BY kind,key'):
            path = self.pending(row['kind'],row['key'])
            if not path.exists():
                continue
            if selected and (total + row['size'] > volume_bytes or len(selected)>=50000):
                self._publish(selected,profile); selected,total = [],0
            selected.append(dict(row)); total += row['size']
        if selected:
            self._publish(selected,profile)

    def _publish(self, selected, profile):
        check_cancelled(self.cancel)
        name = uuid.uuid4().hex + '.cilo'
        directory = self.root / 'volumes'
        entries = [{k:r[k] for k in ('kind','key','size','sha256')} for r in selected]
        journal = {'schema':1,'volume':name,'objects':entries}
        atomic_json(directory / (name+'.json'),journal)
        try:
            with ArchiveStream(directory/name,profile,executable=self.executable,cancel=self.cancel,max_file_gib=64) as writer:
                raw = json.dumps(journal).encode()
                writer.add('collection-volume.json',len(raw),lambda sink:sink.write(raw))
                for row in selected:
                    path = self.pending(row['kind'],row['key'])
                    casc.check(validate(path,row['kind'],row['key'],row['size']) == row['sha256'], 'pending object changed')
                    writer.add_path(member(row['kind'],row['key']),path)
                writer.finish()
            # Read back every compressed block before counting data as archived.
            native_json('verify',directory/name,executable=self.executable)
            sync_directory(directory)
            self._adopt(journal)
        except BaseException:
            if not (directory/name).exists():
                (directory/(name+'.json')).unlink(missing_ok=True)
            raise
        emit('volume',name=name,objects=len(selected),encoded_bytes=sum(r['size'] for r in selected),
             archive_bytes=(directory/name).stat().st_size)

    def _adopt(self, journal):
        name = journal['volume']
        casc.check(VOLUME.fullmatch(name), 'invalid volume name')
        path = self.root / 'volumes' / name
        # Match the recovery journal against checksummed embedded metadata.
        raw = subprocess.check_output([find_cilo(self.executable),'cat',str(path),'collection-volume.json'])
        casc.check(json.loads(raw) == journal, 'volume journal differs from embedded manifest')
        contents = native_json('contents',path,executable=self.executable)
        files = {f['path']:f for f in contents['files']}
        with self.db:
            for entry in journal['objects']:
                kind,key,size,sha = (entry[k] for k in ('kind','key','size','sha256'))
                casc.check(member(kind,key) in files and files[member(kind,key)]['size'] == size,'volume member mismatch')
                obj = self.object(kind,key,size)
                old = self.db.execute('SELECT sha256,volume FROM objects WHERE id=?',(obj,)).fetchone()
                casc.check(old['sha256'] in (None,sha), 'recovery object hash conflict')
                casc.check(old['volume'] in (None,name), 'object is already in another volume')
                self.db.execute('UPDATE objects SET sha256=?,volume=?,error=NULL WHERE id=?',(sha,name,obj))
            self.db.execute('INSERT OR REPLACE INTO volumes VALUES(?,?,?,NULL)',(name,path.stat().st_size,casc.hash_file(path)))
        for entry in journal['objects']:
            self.pending(entry['kind'],entry['key']).unlink(missing_ok=True)

    def recover(self,allow_damaged=False):
        for file in (self.root/'volumes').glob('*.cilo.json'):
            name = file.name[:-5]
            casc.check(VOLUME.fullmatch(name), 'invalid volume journal name')
            if not (file.parent/name).exists():
                file.unlink()  # interrupted writer; inputs remain in pending
            elif not self.db.execute('SELECT 1 FROM volumes WHERE name=?',(name,)).fetchone():
                native_json('verify',file.parent/name,executable=self.executable)
                self._adopt(json.loads(file.read_text(encoding='utf-8')))
        # Detect loss before skipping downloads based on an indexed volume.
        for row in self.db.execute('SELECT * FROM volumes'):
            path = self.root/'volumes'/row['name']
            if not path.is_file() or path.stat().st_size != row['size']:
                with self.db: self.db.execute('UPDATE volumes SET error=? WHERE name=?',('Missing/truncated volume',row['name']))
                self.report()
                casc.check(False,f'Missing/truncated volume: {path}; restore it from backup and run verify')
            casc.check(allow_damaged or row['error'] is None,f'Volume failed verification: {path}; restore it from backup and run verify')
        for row in self.db.execute('SELECT kind,key FROM objects WHERE volume IS NOT NULL'):
            self.pending(row['kind'],row['key']).unlink(missing_ok=True)

    def summary(self):
        builds = []
        for r in self.db.execute('''SELECT b.*, COUNT(CASE WHEN r.role!='cdn-config' THEN 1 END) required,
                COUNT(CASE WHEN r.role!='cdn-config' AND o.volume IS NOT NULL AND v.error IS NULL THEN 1 END) archived,
                COUNT(CASE WHEN r.role='cdn-config' THEN 1 END) optional,
                COUNT(CASE WHEN r.role='cdn-config' AND o.volume IS NOT NULL AND v.error IS NULL THEN 1 END) archived_optional,
                COUNT(CASE WHEN o.volume IS NULL AND o.error IS NOT NULL THEN 1 END) failed_objects,
                COALESCE(SUM(o.size),0) encoded_bytes FROM builds b
                LEFT JOIN refs r ON r.build=b.id LEFT JOIN objects o ON o.id=r.object
                LEFT JOIN volumes v ON v.name=o.volume GROUP BY b.id'''):
            row = json.loads(r['row_json'])
            builds.append({'id':r['id'],'product':row['product'],'build':row['build'],'build_config':row.get('build_config'),
                'metadata_state':r['state'],'required_objects':r['required'],'archived_objects':r['archived'],
                'optional_cdn_configs':r['optional'],'archived_optional_cdn_configs':r['archived_optional'],
                'failed_objects':r['failed_objects'],
                'encoded_bytes':r['encoded_bytes'],'checked_at_utc':r['checked'],
                'status': ('encoded_set_archived' if r['state']=='indexed' and r['required']>0 and r['required']==r['archived']
                           else 'partial' if r['archived'] else r['state']),
                'error':r['error'],'details':json.loads(r['details'] or '{}')})
            if r['failed_objects']:
                builds[-1]['failure_examples']=[dict(item) for item in self.db.execute('''SELECT o.kind,o.key,o.size,o.error,r.role
                    FROM refs r JOIN objects o ON o.id=r.object WHERE r.build=? AND o.volume IS NULL AND o.error IS NOT NULL LIMIT 5''',(r['id'],))]
        totals = dict(self.db.execute('''SELECT COUNT(*) unique_objects,COALESCE(SUM(size),0) unique_encoded_bytes,
                COUNT(CASE WHEN size IS NULL THEN 1 END) unknown_size_objects,
                COUNT(CASE WHEN volume IN (SELECT name FROM volumes WHERE error IS NULL) THEN 1 END) archived_objects,
                COALESCE(SUM(CASE WHEN volume IN (SELECT name FROM volumes WHERE error IS NULL) THEN size ELSE 0 END),0) archived_encoded_bytes
                FROM objects WHERE id IN (SELECT object FROM refs)''').fetchone())
        totals['referenced_encoded_bytes'] = sum(r['encoded_bytes'] for r in builds)
        totals['archive_bytes'] = self.db.execute('SELECT COALESCE(SUM(size),0) FROM volumes').fetchone()[0]
        totals['damaged_volumes'] = self.db.execute('SELECT COUNT(*) FROM volumes WHERE error IS NOT NULL').fetchone()[0]
        totals['exact_sharing_saved_bytes'] = totals['referenced_encoded_bytes']-totals['unique_encoded_bytes']
        totals['pending_bytes'] = sum(p.stat().st_size for p in (self.root/'pending').iterdir() if p.is_file())
        totals['named_recipe_unique_bytes']=self.db.execute('SELECT COALESCE(SUM(size),0) FROM recipes WHERE digest IN (SELECT digest FROM recipe_refs)').fetchone()[0]
        totals['named_recipe_referenced_bytes']=self.db.execute('SELECT COALESCE(SUM(r.size*f.copies),0) FROM recipe_refs f JOIN recipes r USING(digest)').fetchone()[0]
        totals['named_recipe_unique_files']=self.db.execute('SELECT COUNT(DISTINCT digest) FROM recipe_refs').fetchone()[0]
        totals['named_recipe_file_references']=self.db.execute('SELECT COALESCE(SUM(copies),0) FROM recipe_refs').fetchone()[0]
        return {'schema':SCHEMA,'generated_at_utc':catalog.utc_now(),'scope':SCOPE,'totals':totals,
                'states':dict(Counter(r['status'] for r in builds)),'products':dict(Counter(r['product'] for r in builds)),
                'builds':builds}

    def report(self):
        result = self.summary()
        atomic_json(self.root/'audit.json',result)
        # A consistent SQLite snapshot accompanies every completed operation.
        backup = sqlite3.connect(self.root/'collection-backup.sqlite3')
        try: self.db.backup(backup)
        finally: backup.close()
        return result


class DiskArchiveMap:
    """A large CDN archive search must not retain every index entry in RAM."""
    def __init__(self,path):
        self.path=path
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS locations (key TEXT PRIMARY KEY, archive TEXT, offset INTEGER, size INTEGER) WITHOUT ROWID')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path)
        try:
            with db: yield db
        finally: db.close()

    def __contains__(self,key):
        with self.connection() as db:
            return db.execute('SELECT 1 FROM locations WHERE key=?',(key,)).fetchone() is not None

    def __getitem__(self,key):
        with self.connection() as db:
            result=db.execute('SELECT archive,offset,size FROM locations WHERE key=?',(key,)).fetchone()
        if result is None: raise KeyError(key)
        return tuple(result)

    def update(self,entries):
        with self.connection() as db:
            db.executemany('INSERT OR REPLACE INTO locations VALUES(?,?,?,?)',((key,*value) for key,value in entries.items()))


class CollectionStorage(casc.Storage):
    """Reuse retained metadata/objects even when a new build uses a different CDN."""
    def __init__(self,*args,collection_root,executable=None,offline=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.collection_root=Path(collection_root)
        self.executable=executable
        self.offline=offline
        if self.cdn_key:
            casc.check(KEY.fullmatch(self.cdn_key),'invalid CDN configuration key')
            self.archive_map=DiskArchiveMap(self.output/('locations-'+self.cdn_key+'.sqlite3'))

    def _fetch(self,kind,key,size):
        target=self.path(casc.key_path(kind,key))
        if not target.exists():
            source=self.collection_root/'pending'/f'{kind}-{key}'
            if source.exists():
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target)
            else:
                db=sqlite3.connect(self.collection_root/'collection.sqlite3')
                try:
                    obj=db.execute('SELECT volume,sha256 FROM objects WHERE kind=? AND key=?',(kind,key)).fetchone()
                finally: db.close()
                if obj and obj[0]:
                    casc.check(VOLUME.fullmatch(obj[0]),'invalid indexed volume name')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    part=target.with_suffix('.part')
                    try:
                        with part.open('wb') as output:
                            subprocess.run([find_cilo(self.executable),'cat',str(self.collection_root/'volumes'/obj[0]),member(kind,key)],
                                           stdout=output,check=True)
                        casc.check(validate(part,kind,key,size)==obj[1],'archived object SHA-256 mismatch')
                        os.replace(part,target)
                    finally: part.unlink(missing_ok=True)
        casc.check(not self.offline or target.exists(),f'Object not stored locally: {kind}/{key}')
        return super()._fetch(kind,key,size)


def audit_one(row,bases,root,timeout,deep,cancel,executable=None):
    directory = tempfile.mkdtemp(prefix='work-',dir=Path(root)/'scratch')
    result = {'directory':directory,'state':'not_retrieved','objects':[],'retained':[],'details':{'archive_index_search':deep}}
    storage = CollectionStorage(bases,directory,row.get('cdn_config') if deep else None,timeout=timeout,attempts=1,index_jobs=2,
                                collection_root=root,executable=executable,config_fallback=recovered_fallback(row))
    storage.cancel = lambda: check_cancelled(cancel)
    # Seed only bounded metadata objects previously verified in this collection.
    for key in (row.get('build_config'),row.get('cdn_config')):
        if key and (source:=Path(root)/'pending'/f'config-{key}').exists():
            target = storage.path(casc.key_path('config',key)); target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
    try:
        check_cancelled(cancel)
        build = casc.Build(storage,row['build_config'])
        result['state']='config_verified'
        result['objects'].append(('config',row['build_config'],None,'build-config'))
        layout = casc.describe_layout(build.config)
        result['details']['unresolved_references']=layout['content_key_references']
        result['details']['other_build_fields']=layout['other_build_fields']
        for ref in layout['objects']:
            kind = ref['relative_path'].split('/')[0]
            size = build.config.get(ref['source']+'-size',[])
            size = int(size[1]) if kind=='data' and len(size)>1 and size[1].isdigit() else None
            result['objects'].append((kind,ref['key'],size,ref['source']))
        records = build.download_records()
        result['objects'].extend(('data',r['key'],r['size'],'download') for r in records)
        result['state']='indexed'
        result['details'].update(download_objects=len(records),download_encoded_bytes=sum(r['size'] for r in records))
        if layout['content_key_references']:
            try:
                encoding=build.encoding_records()
                known={r['key']:r['size'] for r in records}
                unresolved=[]; resolved=[]
                for ref in layout['content_key_references']:
                    entry=encoding.get(ref['content_key'])
                    if not entry:
                        unresolved.append(ref); continue
                    # One encoding is sufficient for a CKey-only dependency;
                    # prefer an object already in this build's DOWNLOAD set.
                    key=next((key for key in entry['keys'] if key in known),entry['keys'][0])
                    result['objects'].append(('data',key,known.get(key),ref['source']))
                    resolved.append({**ref,'encoding_key':key,'decoded_bytes':entry['size']})
                result['details'].update(unresolved_references=unresolved,resolved_references=resolved,
                    encoding_content_keys=len(encoding),encoding_multiply_encoded_keys=sum(len(r['keys'])>1 for r in encoding.values()))
            except (casc.CascError,OSError,ValueError) as exc:
                result['details']['encoding_resolver_error']=str(exc)[-8000:]
        if 'vfs-root' in build.config:
            try:
                files = build.files()
                types = defaultdict(lambda: {'files':0,'decoded_bytes':0})
                for file in files:
                    ext = Path(file['path']).suffix.lower() or '(none)'
                    types[ext]['files']+=1; types[ext]['decoded_bytes']+=file['size']
                # Identical verified metadata recipes imply the same reconstruction,
                # even when the filename changes. Payload bytes are still untested.
                recipes=Counter()
                for file in files:
                    recipe=[file['size'],[(s['encoding_key'],s['offset'],s['size']) for s in file['spans']]]
                    digest=hashlib.sha256(json.dumps(recipe,separators=(',',':')).encode()).digest()
                    recipes[(digest,file['size'])]+=1
                result['recipes']=[(digest,size,copies) for (digest,size),copies in recipes.items()]
                result['details'].update(named_files=len(files),named_decoded_bytes=sum(f['size'] for f in files),file_types=dict(types))
            except (casc.CascError,OSError,ValueError) as exc:
                result['details']['named_files_error']=str(exc)[-8000:]
        result['details']['verified_bases']=storage.ordered_bases()[:1]
        result['details']['recovered_configs']=storage.recovered_configs
    except (casc.CascError,OSError,ValueError) as exc:
        result['error']=str(exc)[-8000:]
    finally:
        for kind in ('config','data'):
            for path in (Path(directory)/'cdn'/kind).glob('*/*/*'):
                if KEY.fullmatch(path.name):
                    result['retained'].append((kind,path.name,str(path)))
    return result


def audit(collection,rows,*,jobs=4,timeout=6,deep=True,bases_override=None,refresh=False,resume=False):
    bases = {}
    for row in rows:
        if row['product'] not in bases:
            bases[row['product']] = bases_override or casc_probe.build_bases(row,timeout=timeout)
    collection.register(rows,bases)
    groups = defaultdict(list)
    for row in rows:
        previous=collection.db.execute('SELECT state,checked FROM builds WHERE id=?',(identity(row),)).fetchone()
        if (not resume or previous['checked'] is None) and (refresh or previous['state']!='indexed'):
            groups[(row.get('build_config'),row.get('cdn_path'))].append(row)
    tasks=iter(groups.values()); completed=0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        pending={}
        while True:
            check_cancelled(collection.cancel)
            while len(pending)<jobs:
                group=next(tasks,None)
                if group is None: break
                # Include all source paths/regions for catalog aliases.
                endpoints=list(dict.fromkeys(base for r in group for base in bases[r['product']]))
                pending[pool.submit(audit_one,group[0],endpoints,collection.root,timeout,deep,collection.cancel,collection.executable)]=group
            if not pending: break
            done,_=wait(pending,timeout=0.25,return_when=FIRST_COMPLETED)
            for task in done:
                group=pending.pop(task); result=task.result()
                try:
                    collection.accept_audit(group,result)
                finally:
                    shutil.rmtree(result['directory'],ignore_errors=True)
                completed+=len(group)
                emit('audit',completed=completed,total=sum(len(g) for g in groups.values()),product=group[0]['product'],
                     build=group[0]['build'],state=result['state'],objects=len(result['objects']))
    return collection.report()


def archive(collection,*,profile='balanced',volume_bytes=4*1024**3,max_bytes=0,max_objects=0,timeout=15):
    collection.recover()
    collection.publish(profile,volume_bytes)
    downloaded=0; count=0; pending_bytes=0; pending_count=0
    # One object per operation, paginated by integer ID: stable memory at millions
    # of entries. Failed objects are retried on the next run, not in a hot loop.
    last=0
    with tempfile.TemporaryDirectory(prefix='work-',dir=collection.root/'scratch') as directory:
        store=None; store_source=None; store_count=0
        while True:
            check_cancelled(collection.cancel)
            row=collection.db.execute('''SELECT o.* FROM objects o WHERE o.id>? AND o.volume IS NULL
                AND EXISTS(SELECT 1 FROM refs r JOIN builds b ON b.id=r.build WHERE r.object=o.id AND b.state='indexed')
                ORDER BY o.id LIMIT 1''',(last,)).fetchone()
            if row is None: break
            last=row['id']
            if max_objects and count>=max_objects: break
            if max_bytes and (row['size'] is None or downloaded+row['size']>max_bytes): continue
            errors=[]; fetched=None; tried_sources=set()
            for source in collection.db.execute('''SELECT DISTINCT b.row_json,b.bases FROM builds b
                    JOIN refs r ON r.build=b.id WHERE r.object=? ORDER BY b.id''',(row['id'],)):
                check_cancelled(collection.cancel)
                data=json.loads(source['row_json']); marker=(source['bases'],data.get('cdn_config'))
                identity=(tuple(sorted(json.loads(source['bases']))),data.get('cdn_config'))
                if identity in tried_sources: continue
                tried_sources.add(identity)
                # Bound in-memory archive indexes / lock dictionaries. All verified
                # downloaded index files remain in this run's managed scratch cache.
                if store_source!=marker or store_count>=1000:
                    store=CollectionStorage(json.loads(source['bases']),directory,data.get('cdn_config'),
                                       timeout=timeout,attempts=2,max_object_gib=64,index_jobs=2,
                                       collection_root=collection.root,executable=collection.executable,
                                       config_fallback=recovered_fallback(data))
                    store.cancel=lambda:check_cancelled(collection.cancel)
                    store_source=marker; store_count=0
                store_count+=1
                try:
                    fetched=store.fetch(row['kind'],row['key'],row['size']); break
                except (casc.CascError,OSError,ValueError) as exc:
                    errors.append(str(exc)[-1000:])
            if fetched:
                with collection.db:
                    collection.retain(row['kind'],row['key'],fetched)
                downloaded+=row['size'] or collection.pending(row['kind'],row['key']).stat().st_size
                pending_bytes+=row['size'] or 0; count+=1; pending_count+=1
                if pending_bytes>=volume_bytes or pending_count>=50000:
                    collection.publish(profile,volume_bytes); pending_bytes=0; pending_count=0
            else:
                with collection.db:
                    collection.db.execute('UPDATE objects SET error=? WHERE id=?',('\n'.join(errors)[-8000:],row['id']))
            emit('download',object=row['key'],ok=bool(fetched),downloaded_bytes=downloaded,objects=count,
                 error=errors[-1] if not fetched and errors else None)
    collection.publish(profile,volume_bytes)
    result=collection.report(); result['run']={'downloaded_bytes':downloaded,'objects':count}
    return result


def verify(collection):
    collection.recover(allow_damaged=True)
    for row in collection.db.execute('SELECT * FROM volumes'):
        check_cancelled(collection.cancel)
        path=collection.root/'volumes'/row['name']
        try:
            casc.check(casc.hash_file(path)==row['sha256'],f'Volume SHA-256 mismatch: {row["name"]}')
            native_json('verify',path,executable=collection.executable)
        except (ValueError,casc.CascError,OSError) as exc:
            with collection.db: collection.db.execute('UPDATE volumes SET error=? WHERE name=?',(str(exc),row['name']))
            collection.report()
            raise
        with collection.db: collection.db.execute('UPDATE volumes SET error=NULL WHERE name=?',(row['name'],))
        emit('verified_volume',name=row['name'])
    for row in collection.db.execute('SELECT * FROM objects WHERE sha256 IS NOT NULL AND volume IS NULL'):
        path=collection.pending(row['kind'],row['key'])
        casc.check(path.exists() and validate(path,row['kind'],row['key'],row['size'])==row['sha256'],f'Invalid pending object: {path}')
    return collection.report()


def restore(collection,bid,destination):
    """Restore exact encoded objects offline; publish a new directory atomically."""
    row=collection.db.execute('SELECT * FROM builds WHERE id=?',(bid,)).fetchone()
    casc.check(row is not None,'unknown build ID (use status to find the collection ID)')
    missing=collection.db.execute("SELECT COUNT(*) FROM refs r JOIN objects o ON o.id=r.object WHERE r.build=? AND r.role!='cdn-config' AND o.volume IS NULL",(bid,)).fetchone()[0]
    damaged=collection.db.execute('SELECT COUNT(*) FROM refs r JOIN objects o ON o.id=r.object JOIN volumes v ON v.name=o.volume WHERE r.build=? AND v.error IS NOT NULL',(bid,)).fetchone()[0]
    casc.check(row['state']=='indexed' and missing==0 and damaged==0,'build is not fully archived or has damaged volumes')
    destination=Path(destination).expanduser().absolute()
    casc.check(not os.path.lexists(destination),'restore destination already exists')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cilo-restore-',dir=destination.parent) as directory:
        stage=Path(directory)/'build'; stage.mkdir()
        # Read a batch from each volume through one native reader. Its block cache
        # is reused across members, avoiding decompression per object/process.
        volumes=collection.db.execute('''SELECT DISTINCT o.volume FROM objects o JOIN refs r ON r.object=o.id
                                         WHERE r.build=? AND o.volume IS NOT NULL ORDER BY o.volume''',(bid,)).fetchall()
        for volume in volumes:
            check_cancelled(collection.cancel)
            objects=collection.db.execute('''SELECT o.* FROM objects o JOIN refs r ON r.object=o.id
                                           WHERE r.build=? AND o.volume=? ORDER BY o.key''',(bid,volume[0])).fetchall()
            selection=Path(directory)/'selection.json'
            selection.write_text(json.dumps([member(r['kind'],r['key']) for r in objects]),encoding='utf-8')
            batch=Path(directory)/'batch'
            command=[find_cilo(collection.executable),'unpack-selected',str(collection.root/'volumes'/volume[0]),str(batch),str(selection)]
            subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
            for obj in objects:
                relative=member(obj['kind'],obj['key']); source=batch/relative
                casc.check(validate(source,obj['kind'],obj['key'],obj['size'])==obj['sha256'],'restored object SHA-256 mismatch')
                target=stage/relative; target.parent.mkdir(parents=True,exist_ok=True); os.replace(source,target)
            shutil.rmtree(batch)
        info=json.loads(row['row_json']); info['collection_build_id']=bid; info['scope']=SCOPE
        info['missing_optional_cdn_configs']=[r[0] for r in collection.db.execute("SELECT o.key FROM refs r JOIN objects o ON o.id=r.object WHERE r.build=? AND r.role='cdn-config' AND o.volume IS NULL",(bid,))]
        atomic_json(stage/'build.json',info)
        casc.check(not os.path.lexists(destination),'restore destination appeared during operation')
        os.rename(stage,destination)
    return {'restored':str(destination),'build':bid}


def browse(collection,bid,path=None):
    """Inspect named TVFS files offline; decode previews into a bounded sink."""
    record=collection.db.execute('SELECT row_json FROM builds WHERE id=?',(bid,)).fetchone()
    casc.check(record,'unknown collection build ID')
    row=json.loads(record[0])
    with tempfile.TemporaryDirectory(prefix='work-',dir=collection.root/'scratch') as directory:
        storage=CollectionStorage(['https://offline.invalid'],directory,collection_root=collection.root,
                                  executable=collection.executable,offline=True,max_object_gib=64)
        storage.cancel=lambda:check_cancelled(collection.cancel)
        build=casc.Build(storage,row['build_config']); files=build.files()
        if path is None: return {'files':files}
        file=next((f for f in files if f['path']==path),None)
        casc.check(file,'file is not in this build')
        class Prefix:
            def __init__(self): self.data=bytearray(); self.size=0
            def write(self,data):
                check_cancelled(collection.cancel)
                self.data.extend(data[:max(0,65536-len(self.data))]); self.size+=len(data)
                return len(data)
        output=Prefix()
        for span in file['spans']:
            casc.check(span['offset']==0,'nonzero TVFS span offsets are not supported for previews')
            object_path=storage.fetch('data',span['encoding_key'],span['encoded_size'])
            size=casc.decode_blte(object_path,output,limit=span['size'])
            casc.check(size==span['size'],'preview span length mismatch')
            object_path.unlink()
        casc.check(output.size==file['size'],'preview file length mismatch')
        return {'path':path,'size':file['size'],'bytes':list(output.data),'truncated':output.size>len(output.data),
                'integrity':'All source objects and decoded span lengths verified; TVFS supplies no full-file content hash.'}


def import_cache(collection,source):
    """Reuse an existing downloader cache, leaving every source file intact."""
    source=Path(source).expanduser().resolve()
    casc.check(source.is_dir() and source!=collection.root,'choose an existing downloader cache directory')
    imported=0; reused=0
    with tempfile.TemporaryDirectory(prefix='work-',dir=collection.root/'scratch') as directory:
        for kind in ('config','data'):
            paths=list((source/'cdn'/kind).glob('*/*/*'))+list(source.glob(kind+'-*'))
            for path in paths:
                check_cancelled(collection.cancel)
                key=path.name.removeprefix(kind+'-')
                if not KEY.fullmatch(key) or not path.is_file() or path.is_symlink(): continue
                sha=validate(path,kind,key)
                old=collection.db.execute('SELECT sha256 FROM objects WHERE kind=? AND key=?',(kind,key)).fetchone()
                if old and old[0]==sha: reused+=1; continue
                copied=Path(directory)/'object'; shutil.copyfile(path,copied)
                with copied.open('r+b') as file: os.fsync(file.fileno())
                with collection.db: collection.retain(kind,key,copied)
                imported+=1
                emit('import',kind=kind,key=key,bytes=path.stat().st_size)
    result=collection.report(); result['imported_objects']=imported; result['reused_objects']=reused
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['audit','archive','status','verify','restore','files','preview','import-cache'])
    parser.add_argument('collection',type=Path)
    parser.add_argument('--catalog',type=Path,default=catalog.BUNDLED_CATALOG)
    parser.add_argument('--product',action='append',help='default: every product containing odin')
    parser.add_argument('--build',action='append')
    parser.add_argument('--cdn-base',action='append')
    parser.add_argument('--jobs',type=int,default=4)
    parser.add_argument('--http-timeout',type=float,default=6)
    parser.add_argument('--loose-only',action='store_true',help='bounded metadata survey; skip CDN archive-index searches')
    parser.add_argument('--refresh',action='store_true',help='recheck successful metadata as well as retry failures')
    parser.add_argument('--resume-audit',action='store_true',help='continue an interrupted audit, skipping all previously checked rows')
    parser.add_argument('--cilo')
    parser.add_argument('--cancel-file',type=Path)
    parser.add_argument('--profile',choices=['fast','balanced','max'],default='balanced')
    parser.add_argument('--volume-gib',type=float,default=4)
    parser.add_argument('--max-download-gib',type=float,default=0,help='0: unlimited; applies to payloads fetched this run, not metadata/pending data')
    parser.add_argument('--max-objects',type=int,default=0)
    parser.add_argument('--build-id',help='collection build ID from audit.json/status')
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--path',help='named TVFS file for an offline preview')
    parser.add_argument('--source',type=Path,help='existing downloader directory for import-cache')
    args=parser.parse_args(argv)
    if not (1<=args.jobs<=16 and 0<args.http_timeout<=120 and 0<args.volume_gib<=16 and args.max_download_gib>=0 and args.max_objects>=0):
        parser.error('invalid resource limits')
    collection=Collection(args.collection,executable=args.cilo,cancel=args.cancel_file)
    try:
        with collection.lock():
            if args.action=='audit':
                data=catalog.load_catalog(args.catalog)
                rows=[r for r in data['builds'] if (r['product'] in args.product if args.product else 'odin' in r['product'])
                      and (not args.build or r['build'] in args.build)]
                casc.check(rows,'no matching builds')
                with collection.db:
                    collection.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('catalog',json.dumps({k:v for k,v in data.items() if k!='builds'})))
                result=audit(collection,rows,jobs=args.jobs,timeout=args.http_timeout,deep=not args.loose_only,
                             bases_override=args.cdn_base,refresh=args.refresh,resume=args.resume_audit)
            elif args.action=='archive':
                result=archive(collection,profile=args.profile,volume_bytes=int(args.volume_gib*1024**3),
                               max_bytes=int(args.max_download_gib*1024**3),max_objects=args.max_objects,timeout=args.http_timeout)
            elif args.action=='verify': result=verify(collection)
            elif args.action=='restore':
                casc.check(args.build_id and args.destination,'restore needs --build-id and --destination')
                result=restore(collection,args.build_id,args.destination)
            elif args.action in ('files','preview'):
                casc.check(args.build_id,'specify --build-id')
                casc.check(args.action!='preview' or args.path,'preview needs --path')
                result=browse(collection,args.build_id,args.path if args.action=='preview' else None)
            elif args.action=='import-cache':
                casc.check(args.source,'import-cache needs --source')
                result=import_cache(collection,args.source)
            else: result=collection.report()
            emit('result',**({k:v for k,v in result.items() if k!='builds'}))
    finally:
        collection.close()


if __name__=='__main__':
    try: main()
    except (ValueError,OSError,RuntimeError,casc.CascError,sqlite3.Error,subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))
