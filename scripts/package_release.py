"""Assemble source and Debian 13 AMD64 binary assets after CI verification."""
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aurora import __version__


def checksum(path):
    path.with_name(path.name+'.sha256').write_text(
        hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n', encoding='ascii')


def main():
    if platform.machine() != 'x86_64' or 'VERSION_ID="13"' not in Path('/etc/os-release').read_text():
        raise RuntimeError('This release package requires Debian 13 AMD64')
    output = ROOT/'dist/release'
    output.mkdir(parents=True, exist_ok=True)
    name = f'aurora-lite-{__version__}-linux-amd64-debian13'
    stage = ROOT/'work'/name
    stage.mkdir(parents=True, exist_ok=True)
    for source, target in [('dist/aurora-lite','aurora-lite'), ('dist/BUILD-INFO.json','BUILD-INFO.json'),
                           ('scripts/aurora-lite-binary.service','aurora-lite-binary.service'),
                           ('settings.example.json','settings.example.json'), ('README.md','README.md'),
                           ('UPGRADE-0.3.0.md','UPGRADE-0.3.0.md'), ('VALIDATION.md','VALIDATION.md')]:
        shutil.copy2(ROOT/source, stage/target)
    notices = []
    for distribution in importlib.metadata.distributions():
        package_name = distribution.metadata['Name']
        notice_dir = stage/'licenses'/package_name
        notice_dir.mkdir(parents=True, exist_ok=True)
        (notice_dir/'package-metadata.txt').write_text(str(distribution.metadata), encoding='utf-8')
        for file in distribution.files or []:
            if any(part.lower().startswith(('license','copying','copyright','notice')) for part in file.parts):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    shutil.copy2(source, notice_dir/str(file).replace('/', '_'))
        notices.append(package_name+' '+distribution.version)
    python_license = Path('/usr/share/doc/python3.13/copyright')
    if python_license.exists():
        shutil.copy2(python_license, stage/'PYTHON-COPYRIGHT.txt')
    (stage/'THIRD-PARTY.txt').write_text('Included build/runtime package notices:\n'+'\n'.join(sorted(notices))+'\n', encoding='utf-8')
    files = sorted(path for path in stage.rglob('*') if path.is_file())
    (stage/'SHA256SUMS').write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.relative_to(stage).as_posix()+'\n' for path in files), encoding='utf-8')
    binary_archive = output/(name+'.tar.gz')
    with tarfile.open(binary_archive, 'w:gz') as archive:
        archive.add(stage, arcname=name)
    checksum(binary_archive)
    source_name = f'aurora-lite-{__version__}-source'
    source_tar = subprocess.check_output(['git','archive','--format=tar','--prefix='+source_name+'/', 'HEAD'], cwd=ROOT)
    source_archive = output/(source_name+'.tar.gz')
    source_archive.write_bytes(gzip.compress(source_tar, mtime=0))
    checksum(source_archive)
    shutil.copy2(ROOT/'UPGRADE-0.3.0.md', output/'UPGRADE-0.3.0.md')
    shutil.copy2(ROOT/'dist/BUILD-INFO.json', output/'BUILD-INFO.json')
    print(json.dumps([{'name':p.name,'size':p.stat().st_size} for p in output.iterdir()], indent=2))


if __name__ == '__main__':
    main()
