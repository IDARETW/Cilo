#!/usr/bin/env python3
"""Frozen Windows application entry point; also usable with ordinary Python."""
import json
import os
import sys


def main():
    if sys.argv[1:2] == ['--collection']:
        import casc_collection
        try:
            casc_collection.main(sys.argv[2:])
        except Exception as exc:
            print(f'casc_collection: {exc}', file=sys.stderr)
            return 1
        return 0
    if sys.argv[1:2] == ['--cli']:
        import casc_download
        try:
            casc_download.main(sys.argv[2:])
        except (casc_download.CascError, OSError, ValueError, RuntimeError) as exc:
            print(f'casc_download: {exc}', file=sys.stderr)
            return 1
        return 0
    if sys.argv[1:] == ['--self-test']:
        import casc_catalog
        import casc_download
        import casc_gui
        import cilo_bridge
        import cilo_browser
        import casc_collection
        import cilo_collection_gui
        import tkinter
        import tempfile
        import time
        from pathlib import Path
        data = casc_catalog.load_catalog(casc_catalog.BUNDLED_CATALOG)
        endpoints = casc_download.historical_cdn_bases('tpr/odin')
        root = tkinter.Tk()
        root.withdraw()
        app = casc_gui.App(root)
        root.update()
        visible = len(app.visible)
        with tempfile.TemporaryDirectory(prefix='cilo-self-test-') as directory:
            archive = Path(directory) / 'smoke.cilo'
            with cilo_bridge.ArchiveStream(archive, profile='fast') as writer:
                writer.add('hello.txt', 6, lambda sink: sink.write(b'hello\n'))
                writer.finish()
            contents = cilo_bridge.native_json('contents', archive)
            preview = cilo_bridge.native_json('preview', archive, 'hello.txt')
            assert contents['files'][0]['path'] == 'hello.txt'
            assert bytes(preview['bytes']) == b'hello\n' and preview['file_hash_verified']
            browser = cilo_browser.ArchiveBrowser(root, archive)
            def wait_browser():
                deadline = time.monotonic() + 20
                while browser.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.02)
                assert not browser.busy, 'Archive browser timed out'
            wait_browser()
            assert browser.files[0]['path'] == 'hello.txt'
            browser.table.selection_set('0')
            browser.preview()
            wait_browser()
            assert browser.text.get('1.0', 'end-1c') == 'hello\n'
            browser.verify()
            wait_browser()
            assert browser.status.get().startswith('Verified all 1 files')
            browser.close()
            collection = casc_collection.Collection(Path(directory) / 'collection')
            collection.report()
            collection.close()
            window = cilo_collection_gui.CollectionWindow(root, data)
            window.directory.set(str(Path(directory) / 'collection'))
            window.run('status')
            deadline = time.monotonic() + 30
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            assert not window.busy and window.status.get() == 'Finished', 'Collection GUI worker failed'
            window.close()
        root.destroy()
        print(json.dumps({'builds': len(data['builds']), 'endpoints': len(endpoints),
                          'visible_rows': visible, 'tk': tkinter.TkVersion, 'archive_stream_preview': True,
                          'collection_gui_worker': True}))
        return 0
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        # Keep the console-enabled bootloader for piped --cli worker output,
        # but detach the GUI process from its console when launched normally.
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
        # A detached console handle cannot accept prints from a probe helper.
        # User-visible errors/results are delivered through the GUI event queue.
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')
    import casc_gui
    casc_gui.main()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
