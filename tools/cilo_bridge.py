"""Standard-library bridge to the native Cilo archiver; no compression in Python."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile


def find_cilo(explicit=None):
    name = 'cilo.exe' if os.name == 'nt' else 'cilo'
    if explicit or os.environ.get('CILO_BINARY'):
        candidate = Path(explicit or os.environ['CILO_BINARY']).expanduser()
        if not candidate.is_file():
            raise ValueError(f'Cilo executable not found: {candidate}')
        return str(candidate.resolve())
    root = Path(__file__).resolve().parents[1]
    candidates = [Path(sys.executable).parent / name] if getattr(sys, 'frozen', False) else []
    candidates += [root / name, root / 'target/release' / name]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    found = shutil.which(name)
    if found:
        return found
    raise ValueError('Cilo 0.2+ is required. Place cilo.exe beside CiloDownloader.exe, install cilo on PATH, or use --cilo PATH.')


class ArchiveCancelled(RuntimeError):
    pass


def check_cancelled(path):
    if path and Path(path).exists():
        raise ArchiveCancelled('Archive download cancelled.')


def native_json(command, archive, *args, executable=None):
    result = subprocess.run([find_cilo(executable), command, str(archive), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', errors='replace').strip() or 'Cilo command failed')
    return json.loads(result.stdout)


class _Sink:
    def __init__(self, pipe, size, cancel):
        self.pipe, self.size, self.cancel = pipe, size, cancel
        self.count, self.hash = 0, hashlib.sha256()

    def write(self, data):
        check_cancelled(self.cancel)
        if self.count + len(data) > self.size:
            raise ValueError('Decoded file exceeds its declared size')
        self.pipe.write(data)
        self.hash.update(data)
        self.count += len(data)
        return len(data)


class ArchiveStream:
    def __init__(self, destination, profile='balanced', threads=4, executable=None, cancel=None, max_file_gib=16):
        destination = Path(destination).expanduser()
        self.destination = destination.parent.resolve() / destination.name
        self.executable, self.profile, self.threads = find_cilo(executable), profile, threads
        self.cancel, self.max_file_gib = cancel, max_file_gib
        self.process, self.output, self.errors = None, None, None
        self.finished = False

    def __enter__(self):
        check_cancelled(self.cancel)
        if os.path.lexists(self.destination):
            raise ValueError(f'Refusing to replace archive: {self.destination}')
        args = [self.executable, 'pack-stream', str(self.destination), '--profile', self.profile,
                '--threads', str(self.threads), '--max-file-gib', str(self.max_file_gib)]
        if self.cancel:
            args += ['--cancel-file', str(Path(self.cancel).resolve())]
        try:
            self.output = tempfile.TemporaryFile()
            self.errors = tempfile.TemporaryFile()
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=self.output, stderr=self.errors,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.process.stdin.write(b'CILOSTR1')
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def add(self, path, size, write):
        check_cancelled(self.cancel)
        if self.finished:
            raise ValueError('Archive stream is already finished')
        header = json.dumps({'path': path, 'size': size}, ensure_ascii=False).encode('utf-8')
        if len(header) > 65536:
            raise ValueError('File header is too large')
        self.process.stdin.write(struct.pack('<I', len(header)) + header)
        sink = _Sink(self.process.stdin, size, self.cancel)
        write(sink)
        if sink.count != size:
            raise ValueError(f'Decoded file length mismatch: {path}')
        return {'path': path, 'bytes': size, 'sha256': sink.hash.hexdigest(), 'state': 'streamed'}

    def add_path(self, name, path):
        def write(sink):
            with Path(path).open('rb') as source:
                while data := source.read(1024 * 1024):
                    sink.write(data)
        return self.add(name, Path(path).stat().st_size, write)

    def finish(self):
        check_cancelled(self.cancel)
        self.process.stdin.write(b'\0\0\0\0')
        self.process.stdin.close()
        code = self.process.wait()
        self.errors.seek(0)
        error = self.errors.read().decode('utf-8', errors='replace')
        if code:
            raise ValueError(error.strip() or f'Cilo exited with status {code}')
        self.output.seek(0)
        result = json.load(self.output)
        self.finished = True
        return result

    def __exit__(self, *_):
        # EOF without the explicit terminator aborts the native writer. Let it
        # unwind and delete its staging file/archive instead of force-killing it.
        if self.process:
            if self.process.stdin and not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            self.process.wait()
        for file in (self.output, self.errors):
            if file:
                file.close()
