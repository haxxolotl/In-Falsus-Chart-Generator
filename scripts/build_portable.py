"""Build a self-contained Windows folder, with CPython rather than a freezer.

Only declared runtime distributions and project source are copied; no local jobs,
models, game files or reference downloads enter the release.
"""
from __future__ import annotations

import hashlib
import argparse
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
VERSION = '3.12.10'
URL = f'https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip'
MD5 = 'fe8ef205f2e9c3ba44d0cf9954e1abd3'  # Published on python.org's release page.


def main():
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12):
        raise SystemExit('Build using Windows x64 Python 3.12.')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',default='InFalsusStudio-Portable',help='output directory name inside dist')
    name=parser.parse_args().name
    target=(ROOT / 'dist' / name).resolve()
    if target.parent!=(ROOT/'dist').resolve():
        raise SystemExit('Output name must be one directory inside dist.')
    if target.exists():
        raise SystemExit(f'{target} already exists. Rename it before rebuilding; no output is deleted automatically.')
    cache = ROOT / 'build' / 'portable-downloads'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / URL.rsplit('/', 1)[1]
    if not archive.exists():
        urllib.request.urlretrieve(URL, archive)
    if hashlib.md5(archive.read_bytes()).hexdigest() != MD5:
        raise SystemExit(f'Runtime download failed published checksum: {archive}')
    runtime = target / 'runtime'
    runtime.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(runtime)
    site = runtime / 'Lib' / 'site-packages'
    site.mkdir(parents=True)
    requirements = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['dependencies']
    pending = [Requirement(value) for value in requirements]
    included = {}
    while pending:
        req = pending.pop()
        if req.marker and not req.marker.evaluate({'extra': ''}):
            continue
        dist = metadata.distribution(req.name)
        name = dist.metadata['Name']
        if name in included:
            continue
        if dist.version not in req.specifier:
            raise RuntimeError(f'Installed {name} {dist.version} does not satisfy {req}')
        included[name] = dist.version
        origin = Path(dist.locate_file('')).resolve()
        for item in dist.files or ():
            source = Path(dist.locate_file(item)).resolve()
            if not source.is_relative_to(origin) or '__pycache__' in source.parts or source.name == 'direct_url.json':
                continue
            dest = site / source.relative_to(origin)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        pending.extend(Requirement(value) for value in dist.requires or ())
    # The embeddable runtime intentionally omits Tcl/Tk. Bundle the builder's
    # matching 3.12 extension and Tcl/Tk distribution explicitly.
    import tkinter, _tkinter
    tcl = Path(tkinter.Tcl().eval('info library'))
    tk = tcl.with_name('tk8.6')
    shutil.copytree(Path(tkinter.__file__).parent, site / 'tkinter', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(_tkinter.__file__, runtime / '_tkinter.pyd')
    for dll in ('tcl86t.dll', 'tk86t.dll'):
        candidates = [Path(sys.base_prefix) / 'DLLs' / dll, Path(sys.base_prefix) / 'Library' / 'bin' / dll]
        source = next((p for p in candidates if p.is_file()), None)
        if source is None:
            raise RuntimeError(f'Missing Tcl/Tk runtime {dll}')
        shutil.copy2(source, runtime / dll)
    # Conda's Tcl links zlib dynamically; python.org builds may not.
    import pefile
    native = [runtime / name for name in ('_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll')]
    while native:
        binary = native.pop()
        with pefile.PE(str(binary)) as pe:
            names = [entry.dll.decode() for entry in getattr(pe,'DIRECTORY_ENTRY_IMPORT',())]
        for name in names:
            if name.lower().startswith(('api-ms-','ext-ms-')):
                continue  # Windows resolves API-set contracts; do not copy SDK stubs.
            if (runtime / name).exists():
                continue
            candidates = [Path(sys.base_prefix) / 'DLLs' / name, Path(sys.base_prefix) / 'Library' / 'bin' / name]
            source = next((p for p in candidates if p.is_file()), None)
            if source:
                shutil.copy2(source, runtime / name)
                native.append(runtime / name)
    shutil.copytree(tcl, runtime / 'tcl')
    shutil.copytree(tk, runtime / 'tk')
    (runtime / 'python312._pth').write_text('python312.zip\n.\nLib/site-packages\n../src\nimport site\n')
    shutil.copytree(ROOT / 'src', target / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
    (target / 'scripts').mkdir()
    shutil.copy2(ROOT / 'scripts' / 'run.py', target / 'scripts' / 'run.py')
    for name in ('README.md', 'LICENSE', 'RESULT.md'):
        shutil.copy2(ROOT / name, target / name)
    shutil.copytree(ROOT / 'third-party', target / 'third-party')
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64',
                    f'/out:{target / "InFalsusStudio.exe"}', str(ROOT / 'scripts' / 'Launcher.cs')],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    subprocess.run([str(compiler), '/nologo', '/target:exe', '/define:CLI', '/platform:x64',
                    f'/out:{target / "InFalsusStudio-CLI.exe"}', str(ROOT / 'scripts' / 'Launcher.cs')],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    receipt = {'python': VERSION, 'runtime_source': URL,
               'runtime_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
               'dependencies': dict(sorted(included.items())), 'game_data_included': False,
               'source_sha256': {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                   for folder in ('src', 'scripts', 'decorations') for p in sorted((ROOT / folder).rglob('*'))
                   if p.is_file() and '__pycache__' not in p.parts and not any(x in p.parts for x in ('bin', 'obj'))}}
    (target / 'build-receipt.json').write_text(json.dumps(receipt, indent=2))
    print(target, flush=True)


if __name__ == '__main__':
    main()
