# Run with python.org Windows Python 3.12+ (Tk included).
# A directory bundle avoids unpacking a Python runtime into TEMP on every launch.
$ErrorActionPreference = 'Stop'
$ciloRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ciloRoot
python -m pip install --disable-pip-version-check 'pyinstaller==6.16.0'
if ($LASTEXITCODE -ne 0) { throw 'Could not install PyInstaller' }
python -m PyInstaller --noconfirm --clean --onedir --console --name CiloDownloader `
  --paths tools --add-data 'data/casc-availability.json.gz;data' `
  --add-data 'data/cdn-endpoints.json;data' tools/casc_app.py
if ($LASTEXITCODE -ne 0) { throw 'Downloader build failed' }
& .\dist\CiloDownloader\CiloDownloader.exe --self-test
if ($LASTEXITCODE -ne 0) { throw 'Bundled GUI/resources smoke test failed' }
& .\dist\CiloDownloader\CiloDownloader.exe --cli --help
if ($LASTEXITCODE -ne 0) { throw 'Bundled CLI dispatch failed' }
Copy-Item docs\DOWNLOADER.md dist\CiloDownloader\DOWNLOADER.md
Compress-Archive -Path dist\CiloDownloader -DestinationPath dist\CiloDownloader-windows-x64.zip -Force
Get-FileHash dist\CiloDownloader-windows-x64.zip -Algorithm SHA256
