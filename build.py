"""Build and package on the current OS. Never copies user settings or downloaded tools."""
from pathlib import Path
import ast
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tkinter

ROOT = Path(__file__).resolve().parent


def app_version():
    tree = ast.parse((ROOT / 'Transcode_Movie_UI.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'APP_VERSION' for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError('APP_VERSION is missing')


def collect_runtime_notices(destination):
    destination.mkdir(parents=True, exist_ok=True)
    base = Path(sys.base_prefix)
    bundled = ROOT / 'assets/runtime-notices'
    python_candidates = [base / 'LICENSE.txt', base / 'LICENSE',
                         Path(f'/usr/share/doc/python{sys.version_info.major}.{sys.version_info.minor}/copyright'),
                         bundled / 'PYTHON-LICENSE.txt']
    python_notice = next((p for p in python_candidates if p.is_file()), None)
    if python_notice is None:
        raise RuntimeError('Cannot find Python license. Add the runtime notice to assets/runtime-notices/PYTHON-LICENSE.txt.')
    shutil.copy2(python_notice, destination / 'PYTHON-LICENSE.txt')
    tk_dir = Path(tkinter.__file__).parent
    candidates = list((base / 'tcl').glob('*/license.terms')) + list(tk_dir.glob('license*'))
    candidates += [bundled / 'TCL-TK-license.terms']
    notice = next((p for p in candidates if p.is_file()), None)
    if notice is None:
        raise RuntimeError('Cannot find Tcl/Tk notice. Add it to assets/runtime-notices/TCL-TK-license.terms.')
    shutil.copy2(notice, destination / 'TCL-TK-license.terms')


def main():
    version = app_version()
    system = platform.system()
    architecture = platform.machine().lower()
    architecture = {'amd64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64'}.get(architecture, architecture)
    tag = f'LibraryStudio-{version}-{system}-{architecture}'
    dist = ROOT / 'dist'
    stage = dist / tag
    if stage.exists():
        raise FileExistsError(f'{stage} already exists. Move the previous package before rebuilding.')
    dist.mkdir(exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
               '--name', 'LibraryStudio', '--distpath', str(dist), '--workpath', str(ROOT / 'build/pyinstaller'),
               '--specpath', str(ROOT / 'build')]
    if system in ('Windows', 'Darwin'):
        command += ['--icon', str(ROOT / 'assets' / ('library-studio.ico' if system == 'Windows' else 'library-studio.icns'))]
    command += [str(ROOT / 'Transcode_Movie_UI.py')]
    subprocess.run(command, cwd=ROOT, check=True)
    stage.mkdir()
    if system == 'Darwin':
        shutil.copytree(dist / 'LibraryStudio.app', stage / 'LibraryStudio.app', symlinks=True)
    else:
        binary = 'LibraryStudio.exe' if system == 'Windows' else 'LibraryStudio'
        shutil.copy2(dist / binary, stage / binary)
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY.md', 'CHANGELOG.md'):
        shutil.copy2(ROOT / name, stage / name)
    (stage / 'assets').mkdir()
    shutil.copy2(ROOT / 'assets/screenshot.png', stage / 'assets/screenshot.png')
    collect_runtime_notices(stage / 'licenses/runtime')
    if system == 'Darwin':
        archive = dist / (tag + '.zip')
        # Preserve application-bundle framework symlinks and executable permissions.
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(stage), str(archive)], check=True)
    else:
        archive = Path(shutil.make_archive(str(dist / tag), 'zip', root_dir=dist, base_dir=tag))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{checksum}  {archive.name}\n', encoding='utf-8')
    print(f'Package: {archive}\nSHA-256: {checksum}')


if __name__ == '__main__':
    main()
