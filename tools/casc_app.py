#!/usr/bin/env python3
"""Frozen Windows application entry point; also usable with ordinary Python."""
import json
import os
import sys


def main():
    if sys.argv[1:2] == ['--cli']:
        import casc_download
        try:
            casc_download.main(sys.argv[2:])
        except (casc_download.CascError, OSError, ValueError) as exc:
            print(f'casc_download: {exc}', file=sys.stderr)
            return 1
        return 0
    if sys.argv[1:] == ['--self-test']:
        import casc_catalog
        import casc_download
        import casc_gui
        import tkinter
        data = casc_catalog.load_catalog(casc_catalog.BUNDLED_CATALOG)
        endpoints = casc_download.historical_cdn_bases('tpr/odin')
        root = tkinter.Tk()
        root.withdraw()
        app = casc_gui.App(root)
        root.update()
        visible = len(app.visible)
        root.destroy()
        print(json.dumps({'builds': len(data['builds']), 'endpoints': len(endpoints),
                          'visible_rows': visible, 'tk': tkinter.TkVersion}))
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
