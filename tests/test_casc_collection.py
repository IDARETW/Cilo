"""Integration tests: real HTTP, SQLite restart, native volumes and offline restore."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import test_casc_download as fixtures
import casc_collection as cc
from cilo_bridge import find_cilo, ArchiveCancelled


class Collections(unittest.TestCase):
    setUp=fixtures.Network.setUp
    def tearDown(self):
        if hasattr(self,'collection'): self.collection.close()
        fixtures.Network.tearDown(self)

    def prepare(self):
        try: self.binary=find_cilo()
        except ValueError as exc: self.skipTest(str(exc))
        self.encoded={}
        def object(raw):
            data,key=fixtures.blte(raw)
            self.routes['/'+cc.casc.key_path('data',key)]=data; self.encoded[key]=data
            return key,len(data)
        self.shared,size=object(b'shared across builds\n'*500)
        extra,length=object(bytes(range(256))*200)
        self.rows=[]
        for number,records in [(1,[(self.shared,size)]),(2,[(self.shared,size),(extra,length)])]:
            raw=b'DL\x01\x10\x00'+struct.pack('>IH',len(records),0)
            raw+=b''.join(bytes.fromhex(key)+length.to_bytes(5,'big')+b'\x01' for key,length in records)
            dk,_=object(raw)
            config=f'build-name = collection-{number}\ndownload = {cc.casc.md5(raw).hex()} {dk}\n'.encode()
            key=cc.casc.md5(config).hex()
            self.routes['/'+cc.casc.key_path('config',key)]=config
            self.rows.append({'product':'odin' if number==1 else 'odinv1','build':str(number),'catalog_build':str(number),
                              'build_config':key,'cdn_config':None,'region':'us','cdn_path':'test'})
        self.directory=self.root/'collection'
        self.collection=cc.Collection(self.directory,executable=self.binary)
        self.addCleanup(self.collection.close)
        with contextlib.redirect_stdout(io.StringIO()):
            cc.audit(self.collection,self.rows,jobs=2,bases_override=[self.base],timeout=1,deep=False)
        return self.collection

    def run_archive(self,**kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return cc.archive(self.collection,profile='fast',volume_bytes=180,timeout=1,**kwargs)

    def test_global_reuse_resume_and_exact_offline_restore(self):
        collection=self.prepare()
        first=self.run_archive(max_objects=1)
        self.assertEqual(first['states'],{'partial':1,'encoded_set_archived':1})
        before=len(self.requests)
        result=self.run_archive()
        self.assertEqual(result['states'],{'encoded_set_archived':2})
        self.assertGreater(result['totals']['exact_sharing_saved_bytes'],0)
        self.assertEqual(sum(route.endswith(self.shared) for route,_ in self.requests),1)
        self.assertEqual(list((self.directory/'pending').iterdir()),[])
        self.assertEqual(list((self.directory/'scratch').iterdir()),[])
        again=len(self.requests); self.run_archive(); self.assertEqual(len(self.requests),again)
        self.routes.clear()  # Restoration must not contact a CDN.
        for row in self.rows:
            out=self.root/('restored-'+row['build'])
            cc.restore(collection,cc.identity(row),out)
            for obj in collection.db.execute('SELECT o.* FROM objects o JOIN refs r ON r.object=o.id WHERE r.build=?',(cc.identity(row),)):
                restored=out/cc.member(obj['kind'],obj['key'])
                self.assertEqual(cc.casc.hash_file(restored),obj['sha256'])
        self.assertEqual(len(self.requests),again)
        with contextlib.redirect_stdout(io.StringIO()): cc.verify(collection)

    def test_published_volume_recovered_after_database_commit_interruption(self):
        collection=self.prepare()
        with patch.object(collection,'_adopt',side_effect=RuntimeError('simulated crash')):
            with self.assertRaisesRegex(RuntimeError,'simulated crash'):
                collection.publish('fast')
        self.assertTrue(list((self.directory/'volumes').glob('*.cilo')))
        self.assertTrue(list((self.directory/'pending').iterdir()))
        collection.recover()
        self.assertGreater(collection.summary()['totals']['archived_objects'],0)
        self.assertFalse(list((self.directory/'pending').iterdir()))
        self.run_archive()
        self.assertEqual(collection.summary()['states'],{'encoded_set_archived':2})

    def test_tampered_recovery_journal_never_adopts_volume(self):
        collection=self.prepare()
        with patch.object(collection,'_adopt',side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError): collection.publish('fast')
        journal=next((self.directory/'volumes').glob('*.json'))
        data=json.loads(journal.read_text()); data['objects'][0]['sha256']='0'*64
        journal.write_text(json.dumps(data))
        with self.assertRaisesRegex(cc.casc.CascError,'differs from embedded'):
            collection.recover()
        self.assertEqual(collection.summary()['totals']['archived_objects'],0)

    def test_missing_payload_cannot_mark_complete_and_next_run_retries(self):
        collection=self.prepare()
        route='/'+cc.casc.key_path('data',self.shared); payload=self.routes.pop(route)
        failed=self.run_archive()
        self.assertNotIn('encoded_set_archived',failed['states'])
        self.assertTrue(all(row['failed_objects'] and row['failure_examples'] for row in failed['builds']))
        self.routes[route]=payload
        result=self.run_archive()
        self.assertEqual(result['states'],{'encoded_set_archived':2})
        self.assertFalse(list((self.directory/'scratch').iterdir()))

    def test_corrupt_payload_and_pending_bytes_are_rejected(self):
        collection=self.prepare()
        route='/'+cc.casc.key_path('data',self.shared)
        raw=self.routes[route]; self.routes[route]=raw[:-1]+bytes([raw[-1]^1])
        self.run_archive()
        self.assertIsNone(collection.db.execute('SELECT volume FROM objects WHERE key=?',(self.shared,)).fetchone()[0])
        self.assertFalse(list((self.directory/'scratch').iterdir()))

    def test_cancel_budget_conflicts_and_exclusive_lock(self):
        collection=self.prepare()
        with collection.lock():
            other=cc.Collection(self.directory)
            try:
                with self.assertRaisesRegex(ValueError,'another worker'):
                    with other.lock(): self.fail('second writer entered')
            finally: other.close()
        with self.assertRaisesRegex(cc.casc.CascError,'conflicting sizes'):
            collection.object('data',self.shared,1)
        result=self.run_archive(max_bytes=1)
        self.assertEqual(result['run']['objects'],0)
        flag=self.directory/'.stop'; flag.touch(); collection.cancel=flag
        with self.assertRaises(ArchiveCancelled): self.run_archive()
        flag.unlink(); collection.cancel=None
        self.run_archive()
        self.assertEqual(collection.summary()['states'],{'encoded_set_archived':2})

    def test_aliases_with_distinct_cdn_configs_and_reprobe_failure(self):
        collection=self.prepare()
        row={**self.rows[0],'region':'eu','cdn_config':'42'*16}
        collection.register([row],{row['product']:[self.base]})
        result=cc.audit_one(self.rows[0],[self.base],self.directory,1,False,None)
        try: collection.accept_audit([row],result)
        finally: shutil.rmtree(result['directory'])
        self.assertEqual(collection.db.execute('SELECT count(*) FROM refs r JOIN objects o ON o.id=r.object WHERE r.build=? AND o.key=?',
                                              (cc.identity(row),'42'*16)).fetchone()[0],1)
        old=collection.db.execute('SELECT count(*) FROM refs WHERE build=?',(cc.identity(row),)).fetchone()[0]
        checked=collection.db.execute('SELECT checked FROM builds WHERE id=?',(cc.identity(row),)).fetchone()[0]
        collection.accept_audit([row],{'state':'not_retrieved','error':'transient failure'})
        self.assertEqual(collection.db.execute('SELECT count(*) FROM refs WHERE build=?',(cc.identity(row),)).fetchone()[0],old)
        self.assertEqual(collection.db.execute('SELECT checked FROM builds WHERE id=?',(cc.identity(row),)).fetchone()[0],checked)



class AdditionalCollectionCases(Collections):
    # Run only these additional cases; shared fixture helpers are inherited.
    def test_offline_named_listing_preview_and_active_cancel_callback(self):
        import test_archive_download as archive_fixture
        archive_fixture.ArchiveDownload.fixture(self)
        self.rows=[{'product':'odin','build':'1','catalog_build':'1','build_config':self.config_key,'cdn_config':None,'region':'us','cdn_path':'test'}]
        self.directory=self.root/'collection'; self.collection=cc.Collection(self.directory,executable=self.binary,cancel=self.root/'nonexistent-stop')
        self.addCleanup(self.collection.close)
        with contextlib.redirect_stdout(io.StringIO()):
            cc.audit(self.collection,self.rows,jobs=1,bases_override=[self.base],deep=False)
        self.run_archive()
        before=len(self.requests); self.routes.clear()
        bid=cc.identity(self.rows[0]); listed=cc.browse(self.collection,bid)
        self.assertEqual({f['path'] for f in listed['files']},set(self.files))
        for name,raw in self.files.items():
            result=cc.browse(self.collection,bid,name)
            self.assertEqual(bytes(result['bytes']),raw[:65536]); self.assertEqual(result['size'],len(raw))
        self.assertEqual(len(self.requests),before)
        self.assertEqual(list((self.directory/'scratch').iterdir()),[])
        self.assertEqual(self.collection.summary()['totals']['named_recipe_file_references'],2)

    def test_volume_corruption_updates_coverage_and_can_be_repaired(self):
        collection=self.prepare(); self.run_archive()
        volume=next((self.directory/'volumes').glob('*.cilo')); original=volume.read_bytes()
        changed=bytearray(original); changed[64]^=1; volume.write_bytes(changed)
        with self.assertRaises(cc.casc.CascError): cc.verify(collection)
        self.assertEqual(collection.summary()['totals']['damaged_volumes'],1)
        self.assertNotEqual(collection.summary()['states'],{'encoded_set_archived':2})
        volume.write_bytes(original)
        with contextlib.redirect_stdout(io.StringIO()): cc.verify(collection)
        self.assertEqual(collection.summary()['states'],{'encoded_set_archived':2})

    def test_import_cache_preserves_source_and_avoids_payload_download(self):
        collection=self.prepare(); source=self.root/'old-cache'; source.mkdir()
        path=source/('data-'+self.shared); original=self.encoded[self.shared]; path.write_bytes(original)
        with contextlib.redirect_stdout(io.StringIO()):
            imported=cc.import_cache(collection,source)
            reused=cc.import_cache(collection,source)
        self.assertEqual(imported['imported_objects'],1)
        self.assertEqual(reused['reused_objects'],1)
        self.run_archive()
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(sum(route.endswith(self.shared) for route,_ in self.requests),0)

    def test_missing_loose_object_uses_disk_index_and_archive_range(self):
        collection=self.prepare()
        raw=self.routes.pop('/'+cc.casc.key_path('data',self.shared))
        index,archive=fixtures.index_fixture(self.shared,17,len(raw))
        self.routes['/'+cc.casc.key_path('data',archive)+'.index']=index
        self.routes['/'+cc.casc.key_path('data',archive)]=b'x'*17+raw+b'tail'
        config=f'archives = {archive}\n'.encode(); key=cc.casc.md5(config).hex()
        self.routes['/'+cc.casc.key_path('config',key)]=config
        with collection.db:
            for row in self.rows:
                old_id=cc.identity(row); row['cdn_config']=key
                # Preserve the existing collection identity in this synthetic
                # source update: the actual test exercises CDN source lookup.
                collection.db.execute('UPDATE builds SET row_json=? WHERE id=?',(json.dumps(row),old_id))
        result=self.run_archive()
        self.assertEqual(result['states'],{'encoded_set_archived':2})
        expected=f'bytes=17-{16+len(raw)}'
        self.assertTrue(any(path.endswith(archive) and value==expected for path,value in self.requests))
        self.assertFalse(list((self.directory/'scratch').iterdir()))

# Avoid rerunning the base integration tests through inheritance.
for _name in list(Collections.__dict__):
    if _name.startswith('test_'): setattr(AdditionalCollectionCases,_name,None)


if __name__ == '__main__':
    unittest.main()
