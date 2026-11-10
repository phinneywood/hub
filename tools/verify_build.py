"""Build a committed archive and test only its independently installed wheel.

Run with a Python 3.12 build environment containing build + pinned build tools.
Dependencies use pip's normal index/cache, or PIP_NO_INDEX/PIP_FIND_LINKS offline.
Outputs stay in fresh system scratch. The closed release directory contains only
committed source archives, the verified wheel, a reviewed dependency wheelhouse,
and its digest receipt, never runtime state.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile


def run(*args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dependency-env', type=Path,
                        help='Offline local verification: copy locked distributions from this existing venv (never modify it).')
    parser.add_argument('--macos-wheelhouse', type=Path, required=True,
                        help='Reviewed hash-locked macOS ARM64/Python 3.12 dependency archive.')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo):
        raise RuntimeError('Commit source changes before verifying; checkout must be clean.')
    root = Path(tempfile.mkdtemp(prefix='package-verification-')).resolve()
    print(f'VERIFICATION_ROOT={root}', flush=True)
    (root / 'home').mkdir()
    (root / 'install-home').mkdir()
    install_env = {'HOME': str(root / 'install-home'), 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.defpath,
                   'PIP_CONFIG_FILE': os.devnull, 'PYTHONDONTWRITEBYTECODE': '1',
                   'LANG': 'C.UTF-8', 'TMPDIR': str(root)}
    for key in ('PIP_NO_INDEX', 'PIP_FIND_LINKS'):
        if key in os.environ:
            install_env[key] = os.environ[key]
    source = root / 'source'
    source.mkdir()
    archive = root / 'source.tar'
    run('git', 'archive', '--format=tar', '-o', archive, commit, cwd=repo)
    with tarfile.open(archive) as tar:
        tar.extractall(source, filter='data')
    dependency_archive = root / 'macos-arm64-wheelhouse.tar'
    shutil.copy2(args.macos_wheelhouse, dependency_archive)
    run(sys.executable, '-I', source / 'tools/macos_wheelhouse.py', 'verify',
        '--requirements', source / 'requirements.lock',
        '--lock', source / 'tools/macos-arm64-wheelhouse.json',
        '--archive', dependency_archive, env=install_env)
    dependency_manifest = json.loads((source / 'tools/macos-arm64-wheelhouse.json').read_text())
    project = tomllib.loads((source / 'pyproject.toml').read_text())['project']['name']
    package = {'personal-hub': 'hub', 'family-site': 'family_site'}[project]
    dist = root / 'dist'
    # build's default builds the wheel FROM its newly built sdist.
    run(sys.executable, '-I', '-m', 'build', '--no-isolation', '--outdir', dist, source, env=install_env)
    sdist = next(dist.glob('*.tar.gz'))
    wheel = next(dist.glob('*.whl'))
    with tarfile.open(sdist) as tar:
        tar.extractall(root / 'unpacked', filter='data')
    unpacked = next((root / 'unpacked').iterdir())
    required = ['requirements.lock', 'tools/verify_installed.py', 'tools/macos_wheelhouse.py',
                'tools/macos-arm64-wheelhouse.json']
    required += [str(p.relative_to(source)) for p in (source / 'tests').rglob('*.py')]
    for name in required:
        assert (source / name).read_bytes() == (unpacked / name).read_bytes(), name
    packages = ('hub', 'preschool')
    files = [p for name in packages for p in (source / name).rglob('*') if p.is_file()]
    with zipfile.ZipFile(wheel) as z:
        expected = {str(p.relative_to(source)) for p in files}
        actual = {n for n in z.namelist() if any(n.startswith(name + '/') for name in packages) and not n.endswith('/')}
        assert actual == expected, (actual - expected, expected - actual)
        for p in files:
            assert z.read(str(p.relative_to(source))) == p.read_bytes(), p
    run(sys._base_executable, '-I', '-m', 'venv', root / 'venv', env=install_env)
    python = root / 'venv/bin/python'
    if args.dependency_env:
        # Read installed distribution records, not arbitrary runtime/source files.
        helper = '''import importlib.metadata as m,json,sys
from pathlib import Path
wanted=[line.split("==") for line in Path(sys.argv[1]).read_text().splitlines() if line and not line.startswith("#")]
files=[]
for name,version in wanted:
 d=m.distribution(name)
 assert d.version==version,(name,d.version,version)
 for f in d.files or []:
  if ".." not in f.parts and f.suffix not in {".pyc"}:
   files.append([str(d.locate_file(f)),str(f)])
print(json.dumps(files))'''
        records = json.loads(subprocess.check_output([str(args.dependency_env / 'bin/python'), '-I', '-c', helper, str(unpacked / 'requirements.lock')], text=True, env=install_env))
        site = next((root / 'venv/lib').glob('python*/site-packages'))
        for origin, relative in records:
            target = site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
    else:
        run(python, '-I', '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:', '-r', unpacked / 'requirements.lock', env=install_env)
    run(python, '-I', '-m', 'pip', 'install', '--no-index', '--no-deps', wheel, env=install_env)
    run(python, '-I', '-m', 'pip', 'check', env=install_env)
    shutil.copytree(unpacked / 'tests', root / 'tests')
    shutil.copy2(unpacked / 'tools/verify_installed.py', root / 'verify_installed.py')
    # Do not inherit service configuration or credentials into the test process.
    env = {'HOME': str(root / 'home'), 'PATH': str(root / 'venv/bin'),
           'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1',
           'LANG': 'C.UTF-8', 'TMPDIR': str(root)}
    run(python, '-I', root / 'verify_installed.py', root, package, cwd=root, env=env)
    result = {'commit': commit, 'project': project, 'package_files': len(files),
              'python': sys.version, 'dependency_mode': 'offline-copy' if args.dependency_env else 'locked-pip',
              'artifacts': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [archive, sdist, wheel, dependency_archive]},
              'wheelhouse_target': dependency_manifest['target'],
              'dependency_wheels': len(dependency_manifest['wheels']),
              'verified': True, 'deployment': False}
    report = root / 'verification.json'
    report.write_text(json.dumps(result, indent=2) + '\n')
    release = root / 'release'
    release.mkdir()
    for artifact in [archive, sdist, wheel, dependency_archive, report]:
        shutil.copy2(artifact, release / artifact.name)
    assert {p.name for p in release.iterdir()} == set(result['artifacts']) | {'verification.json'}
    print(report.read_text(), flush=True)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
            out.write(f'report={report}\nrelease={release}\n')


if __name__ == '__main__':
    main()
