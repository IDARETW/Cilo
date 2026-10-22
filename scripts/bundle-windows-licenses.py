"""Copy runtime redistribution notices into the PyInstaller directory bundle."""
from pathlib import Path
import shutil
import sys
import importlib.metadata

destination = Path('dist/CiloDownloader/licenses')
destination.mkdir(parents=True, exist_ok=True)
shutil.copyfile(Path(sys.base_prefix) / 'LICENSE.txt', destination / 'Python-LICENSE.txt')
shutil.copyfile(Path(sys.base_prefix) / 'tcl/tk8.6/license.terms', destination / 'Tk-license.terms')
# Tcl's notice is included in the Python license aggregation; retain a separate
# notice too if that Python distribution supplies it.
tcl = Path(sys.base_prefix) / 'tcl/tcl8.6/license.terms'
if tcl.exists():
    shutil.copyfile(tcl, destination / 'Tcl-license.terms')
distribution = importlib.metadata.distribution('pyinstaller')
for entry in distribution.files:
    if entry.name == 'COPYING.txt':
        shutil.copyfile(distribution.locate_file(entry), destination / 'PyInstaller-COPYING.txt')
        break
else:
    raise RuntimeError('PyInstaller license and bootloader exception were not found')
