"""Reproducible simai parser benchmark matrix (Python 3.12+, Windows x64)."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import re
import shutil
import statistics
import struct
import subprocess
import sys
import tarfile
import time
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.work'
VARIANTS = {
    'cimai': ('cimai', 'HEAD', None),
    'maidata-rs': ('maidata-rs', 'HEAD', None),
    'maj-2.2.3': ('majsimai', '2.2.3', 'MAJ_LEGACY'),
    'maj-refactor': ('majsimai', 'HEAD', 'MAJ_REFACTOR'),
    'sharp-master': ('simaisharp', 'master', 'SHARP_MASTER'),
    'sharp-onepass': ('simaisharp', 'HEAD', 'SHARP_ONEPASS'),
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def capture(args, cwd=ROOT, env=None):
    return subprocess.check_output([str(x) for x in args], cwd=cwd, env=env).decode('utf-8', 'replace').strip()


def run_command(args, log, env=None, cwd=ROOT, timeout=3600):
    args = [str(x) for x in args]
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f'[{dt.datetime.now().strftime("%H:%M:%S")}] {args[0]} {" ".join(args[1:5])} -> {log.relative_to(ROOT)}', flush=True)
    started = time.monotonic()
    with log.open('wb') as stream:
        result = subprocess.run(args, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    record = {'argv': args, 'cwd': str(cwd), 'seconds': time.monotonic() - started, 'exit_code': result.returncode}
    write_json(log.with_suffix('.command.json'), record)
    if result.returncode:
        tail = log.read_bytes().decode('utf-8', 'replace').splitlines()[-22:]
        raise RuntimeError(f'Command failed ({result.returncode}): {log}\n' + '\n'.join(tail))


def developer_environment():
    env = dict(os.environ)
    if os.name != 'nt':
        raise RuntimeError('This runner currently targets Windows x64.')
    locator = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    install = capture([locator, '-latest', '-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'])
    if not install:
        raise RuntimeError('Visual Studio C++ x64 tools were not found.')
    vsdev = Path(install) / 'Common7/Tools/VsDevCmd.bat'
    command = f'cmd /d /s /c ""{vsdev}" -arch=x64 -host_arch=x64 >nul && set"'
    raw = subprocess.check_output(command).decode('utf-8', 'replace')
    for line in raw.splitlines():
        if '=' in line and not line.startswith('='):
            key, value = line.split('=', 1)
            env[key.upper()] = value
    cargo_bin = WORK / 'toolchains/cargo/bin'
    env['PATH'] = str(cargo_bin) + os.pathsep + env.get('PATH', '')
    env['CARGO_HOME'] = str(WORK / 'toolchains/cargo')
    env['RUSTUP_HOME'] = str(WORK / 'toolchains/rustup')
    env['RUSTFLAGS'] = ''
    env.update({'DOTNET_TieredCompilation': '0', 'DOTNET_TieredPGO': '0',
                'DOTNET_ReadyToRun': '0', 'DOTNET_gcServer': '0', 'DOTNET_gcConcurrent': '0',
                'VSLANG': '1033', 'DOTNET_CLI_UI_LANGUAGE': 'en-US'})
    return env


def executable(name, env):
    found = shutil.which(name, path=env.get('PATH'))
    if not found:
        raise RuntimeError(f'Executable not found: {name}')
    return found


def matrix():
    rows = [{'id': 'cimai-native', 'variant': 'cimai', 'runtime': 'Native', 'tolerance': '-', 'wrapper': '-', 'kind': 'native'}]
    for variant in ('maj-2.2.3', 'maj-refactor', 'sharp-master', 'sharp-onepass'):
        for tolerance in (('Lenient', 'Strict') if variant.startswith('maj') else ('-',)):
            for runtime in ('JIT', 'NativeAOT'):
                row = {'id': f'{variant}-{runtime}' if tolerance == '-' else f'{variant}-{tolerance}-{runtime}', 'variant': variant,
                       'runtime': runtime, 'tolerance': tolerance, 'wrapper': '-', 'kind': 'managed'}
                if variant == 'maj-2.2.3' and tolerance == 'Strict':
                    row['unsupported'] = '2.2.3 没有 SimaiParserOptions / Strict API。'
                rows.append(row)
    for variant in ('maj-2.2.3', 'maj-refactor'):
        for wrapper in ('view', 'model'):
            for tolerance in ('Lenient', 'Strict'):
                row = {'id': f'{variant}-cpp-{wrapper}-{tolerance}', 'variant': variant,
                       'runtime': 'NativeAOT+C++', 'tolerance': tolerance, 'wrapper': wrapper, 'kind': 'native'}
                if variant == 'maj-2.2.3':
                    row['unsupported'] = '2.2.3 没有 CppGen；旧 ABI 不满足新生成器的类型注解与签名要求。'
                rows.append(row)
    for pipeline in ('parse', 'materialize'):
        rows.append({'id': f'maidata-rs-{pipeline}', 'variant': 'maidata-rs', 'runtime': 'Rust Native',
                     'tolerance': '-', 'wrapper': '-', 'pipeline': pipeline, 'kind': 'rust'})
    return rows


def select_samples(paths, limit, seed):
    paths = sorted(paths)
    if limit and limit < len(paths):
        largest = max(paths, key=lambda p: p.stat().st_size)
        selected = random.Random(seed).sample([x for x in paths if x != largest], limit - 1)
        paths = sorted(selected + [largest])
    random.Random(seed).shuffle(paths)
    return paths




def normalize_input(content):
    """Convert BOM-marked UTF-16/32 to UTF-8 once, before any timed operation."""
    if content.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        return content.decode('utf-32').encode('utf-8'), 'utf-32', True
    if content.startswith((b'\xff\xfe', b'\xfe\xff')):
        return content.decode('utf-16').encode('utf-8'), 'utf-16', True
    content.decode('utf-8-sig' if content.startswith(b'\xef\xbb\xbf') else 'utf-8')
    return content, 'utf-8-sig' if content.startswith(b'\xef\xbb\xbf') else 'utf-8', False


def chart_fields(content):
    """Locate effective nonempty inote_1..7 values; duplicate difficulties use the last field."""
    starts = [m.end() - 1 for m in re.finditer(rb'(?m)^&|(?<=\r)&|\A\xef\xbb\xbf&', content)]
    fields = {}
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(content)
        equals = content.find(b'=', start + 1, end)
        if equals < 0:
            continue
        key = content[start + 1:equals].strip(b' ')
        if len(key) == 7 and key[:6] == b'inote_' and key[6] in b'1234567':
            fields[int(chr(key[6]))] = memoryview(content)[equals + 1:end]
    return {difficulty: value for difficulty, value in fields.items() if len(value)}


def pack_corpus(sample_root, output, limit, seed):
    discovered = list(sample_root.rglob('maidata.txt'))
    paths = select_samples(discovered, limit, seed)
    if not paths:
        raise ValueError(f'No maidata.txt samples in {sample_root}')
    entries = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('wb') as stream:
        stream.write(b'SIMAI001' + struct.pack('<I', len(paths)))
        for path in paths:
            name = path.relative_to(sample_root).as_posix()
            original = path.read_bytes()
            data, encoding, transcoded = normalize_input(original)
            name_bytes = name.encode('utf-8')
            stream.write(struct.pack('<I', len(name_bytes)) + name_bytes + struct.pack('<I', len(data)) + data)
            charts = chart_fields(data)
            entries.append({'path': name, 'bytes': len(data), 'source_bytes': len(original),
                            'source_sha256': hashlib.sha256(original).hexdigest(),
                            'source_encoding': encoding, 'transcoded': transcoded, 'charts': len(charts),
                            'chart_bytes': sum(len(value) for value in charts.values()),
                            'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'schema_version': 3, 'source_bytes': sum(x['source_bytes'] for x in entries),
                'transcoded_files': sum(x['transcoded'] for x in entries), 'input_encoding': 'utf-8', 'discovered_files': len(discovered), 'full_corpus': len(paths) == len(discovered),
                'chart_count': sum(x['charts'] for x in entries), 'chart_bytes': sum(x['chart_bytes'] for x in entries),
                'count': len(entries), 'bytes': sum(x['bytes'] for x in entries), 'seed': seed,
                'largest': max(entries, key=lambda x: x['bytes']), 'corpus_sha256': sha256(output), 'files': entries}
    write_json(output.with_suffix('.manifest.json'), manifest)
    return manifest


def resolve_source_commit(source, ref):
    candidates = ('HEAD',) if ref == 'HEAD' else (f'refs/remotes/origin/{ref}', ref)
    for candidate in candidates:
        result = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', f'{candidate}^{{commit}}'],
                                cwd=source, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    raise RuntimeError(f'Cannot resolve {ref!r} in {source}. Fetch parser branches and tags before refreshing sources.')


def prepare(config, refresh=False, limit=0):
    WORK.mkdir(exist_ok=True)
    lock_path = ROOT / 'sources.lock.json'
    lock = read_json(lock_path) if lock_path.exists() and not refresh else {'sources': {}}
    for variant, (repo, ref, define) in VARIANTS.items():
        repository = config['repositories'][repo]
        source = (ROOT / repository).resolve()
        if not (source / '.git').exists():
            raise RuntimeError(f'Parser repository is not initialized: {source}. Run git submodule update --init --recursive.')
        if variant not in lock['sources'] or refresh:
            commit = resolve_source_commit(source, ref)
            lock['sources'][variant] = {'repository': repository, 'ref': ref, 'commit': commit,
                'commit_time': capture(['git', 'show', '-s', '--format=%cI', commit], cwd=source),
                'origin': capture(['git', 'remote', 'get-url', 'origin'], cwd=source)}
        item = lock['sources'][variant]
        destination = WORK / 'sources' / variant
        marker = destination / '.benchmark-commit'
        if marker.exists() and marker.read_text() == item['commit']:
            continue
        if destination.exists():
            raise RuntimeError(f'Snapshot differs: {destination}. Use a fresh .work folder rather than replacing it.')
        archive = WORK / f'{variant}.tar'
        subprocess.run(['git', 'archive', '--format=tar', f'--output={archive}', item['commit']], cwd=source, check=True)
        destination.mkdir(parents=True)
        with tarfile.open(archive) as stream:
            stream.extractall(destination, filter='data')
        marker.write_text(item['commit'])
        archive.unlink()
    write_json(lock_path, lock)
    corpus = WORK / 'corpus' / ('full-utf8.bin' if not limit else f'limit-{limit}-utf8.bin')
    manifest = pack_corpus((ROOT / config['samples']).resolve(), corpus, limit, config['seed'])
    print(f'Corpus: {manifest["count"]}/{manifest["discovered_files"]} files, {manifest["chart_count"]} chart fields, {manifest["bytes"]:,} bytes, SHA256 {manifest["corpus_sha256"]}', flush=True)
    generate_projects(config)
    generate_rust_project(config)
    return corpus, manifest, lock


def compile_items(paths):
    return '\n'.join(f'    <Compile Include="{escape(str(p))}" />' for p in paths)


def project_text(config, body, name, *, output='Library', define='', aot=False):
    return f'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>{config['framework']}</TargetFramework>
    <OutputType>{output}</OutputType><AssemblyName>{name}</AssemblyName>
    <ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable><LangVersion>14</LangVersion>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks><Optimize>true</Optimize>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems><NuGetAudit>false</NuGetAudit>
    <DefineConstants>$(DefineConstants);{define}</DefineConstants>
    <ServerGarbageCollection>false</ServerGarbageCollection><ConcurrentGarbageCollection>false</ConcurrentGarbageCollection>
    <IlcOptimizationPreference>Speed</IlcOptimizationPreference><DebugType>portable</DebugType>
    <Deterministic>true</Deterministic><GenerateDocumentationFile>true</GenerateDocumentationFile><NoWarn>$(NoWarn);1591</NoWarn>
    {'<PublishAot>true</PublishAot><NativeLib>Shared</NativeLib>' if aot else ''}
  </PropertyGroup>
  <ItemGroup>
{body}
  </ItemGroup>
</Project>
'''


def generate_projects(config):
    common = ROOT / 'src/managed'
    for variant, (_, _, define) in VARIANTS.items():
        if not define:
            continue
        snapshot = WORK / 'sources' / variant
        runtime = snapshot / ('Runtime' if variant.startswith('maj') else 'SimaiSharp/src')
        parser = WORK / 'projects' / variant / 'parser' / 'Parser.csproj'
        parser.parent.mkdir(parents=True, exist_ok=True)
        assembly = 'MajSimai' if variant.startswith('maj') else 'SimaiSharp'
        body = compile_items([runtime / '**/*.cs']) + '\n    <InternalsVisibleTo Include="MajSimai.CppGen" />'
        parser.write_text(project_text(config, body, assembly), encoding='utf-8')
        for kind, program in (('bench', 'BenchProgram.cs'), ('probe', 'ProbeProgram.cs')):
            body = compile_items([common / 'Corpus.cs', common / 'Adapter.cs', common / program])
            body += f'\n    <ProjectReference Include="{parser}" />'
            if kind == 'bench':
                body += f'\n    <PackageReference Include="BenchmarkDotNet" Version="{config["benchmarkdotnet_version"]}" />'
            project = parser.parent.parent / kind / (f'{variant}.bench.csproj' if kind == 'bench' else 'Probe.csproj')
            project.parent.mkdir(parents=True, exist_ok=True)
            project.write_text(project_text(config, body, f'{variant}.{kind}', output='Exe', define=define), encoding='utf-8')
    refactor = WORK / 'sources/maj-refactor'
    cppgen = WORK / 'projects/cppgen/CppGen.csproj'
    cppgen.parent.mkdir(parents=True, exist_ok=True)
    body = compile_items([refactor / 'tools/MajSimai.CppGen/**/*.cs'])
    body += f'\n    <ProjectReference Include="{WORK / "projects/maj-refactor/parser/Parser.csproj"}" />'
    cppgen.write_text(project_text(config, body, 'MajSimai.CppGen', output='Exe'), encoding='utf-8')
    replacements = []
    diagnostic = WORK / 'diagnostic/maj-refactor/Runtime'
    for original in (refactor / 'Runtime').rglob('*.cs'):
        text = original.read_text(encoding='utf-8-sig')
        modified = text.replace('Marshal.AllocHGlobal(', 'SimaiBench.NativeHeap.AllocHGlobal(').replace('Marshal.FreeHGlobal(', 'SimaiBench.NativeHeap.FreeHGlobal(')
        target = diagnostic / original.relative_to(refactor / 'Runtime')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(modified, encoding='utf-8')
        if text != modified:
            replacements.append({'path': original.relative_to(refactor).as_posix(), 'original_sha256': sha256(original), 'instrumented_sha256': sha256(target)})
    write_json(WORK / 'diagnostic/instrumentation.json', replacements)
    for kind in ('timing', 'memory'):
        native = WORK / 'projects' / f'native-{kind}' / 'Native.csproj'
        native.parent.mkdir(parents=True, exist_ok=True)
        runtime = refactor / 'Runtime' if kind == 'timing' else diagnostic
        body = compile_items([runtime / '**/*.cs', common / 'NativeExports.cs'])
        native.write_text(project_text(config, body, 'MajNative' if kind == 'timing' else 'MajNativeMemory', aot=True), encoding='utf-8')


def build(config, env, only=None):
    logs = WORK / 'build-logs'
    for variant, (_, _, define) in VARIANTS.items():
        if not define:
            continue
        if only and not re.search(only, variant):
            continue
        base = WORK / 'projects' / variant
        for kind in ('bench', 'probe'):
            run_command(['dotnet', 'build', base / kind / (f'{variant}.bench.csproj' if kind == 'bench' else 'Probe.csproj'), '-c', 'Release', '--nologo'], logs / f'{variant}-{kind}.log', env)
        run_command(['dotnet', 'publish', base / 'probe/Probe.csproj', '-c', 'Release', '-r', config['runtime_identifier'],
                     '-p:PublishAot=true', '-p:IlcOptimizationPreference=Speed', '-o', WORK / 'bin' / variant / 'aot', '--nologo'], logs / f'{variant}-aot.log', env)
    if not only or re.search(only, 'maidata-rs'):
        build_rust(config, env)
    if only and not (re.search(only, 'cimai') or re.search(only, 'maj-refactor')):
        return
    run_command(['dotnet', 'run', '--project', WORK / 'projects/cppgen/CppGen.csproj', '-c', 'Release', '--', '--output', WORK / 'wrapper'], logs / 'cppgen.log', env)
    for kind in ('timing', 'memory'):
        run_command(['dotnet', 'publish', WORK / f'projects/native-{kind}/Native.csproj', '-c', 'Release', '-r', config['runtime_identifier'],
                     '-o', WORK / 'bin' / f'native-{kind}', '--nologo'], logs / f'native-{kind}.log', env)
        name = 'MajNative' if kind == 'timing' else 'MajNativeMemory'
        import_library = WORK / f'projects/native-{kind}/bin/Release' / config['framework'] / config['runtime_identifier'] / 'native' / (name + '.lib')
        shutil.copy2(import_library, WORK / f'bin/native-{kind}' / (name + '.lib'))
    cmake_args = [executable('cmake', env), '-S', ROOT, '-B', WORK / 'native', '-G', 'Ninja',
                  '-DCMAKE_BUILD_TYPE=Release', f'-DCMAKE_C_COMPILER={executable("cl", env)}',
                  f'-DCMAKE_CXX_COMPILER={executable("cl", env)}', f'-DCMAKE_MAKE_PROGRAM={executable("ninja", env)}',
                  f'-DCIMAI_SOURCE={WORK / "sources/cimai"}', f'-DMAJ_WRAPPER={WORK / "wrapper"}',
                  '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON', f"-DGOOGLE_BENCHMARK_COMMIT={config['google_benchmark_commit']}"]
    for kind in ('timing', 'memory'):
        name = 'MajNative' if kind == 'timing' else 'MajNativeMemory'
        cmake_args.extend([f'-DMAJ_{kind}_LIB={WORK / "bin" / f"native-{kind}" / (name + ".lib")}',
                           f'-DMAJ_{kind}_DLL={WORK / "bin" / f"native-{kind}" / (name + ".dll")}'])
    cmake_args = [str(x).replace('\\', '/') for x in cmake_args]
    run_command(cmake_args, logs / 'cmake-configure.log', env)
    run_command(['cmake', '--build', WORK / 'native', '--parallel', '4'], logs / 'cmake-build.log', env)


def optional_capture(args, env):
    result = subprocess.run([str(x) for x in args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode('utf-8', 'replace').strip() if result.returncode == 0 else None


def host_metadata(env):
    cpu = capture(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors) | ConvertTo-Json -Compress'])
    memory = capture(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory'])
    return {'os': platform.platform(), 'architecture': platform.machine(), 'cpu': json.loads(cpu),
            'physical_memory_bytes': int(memory), 'dotnet_info': capture(['dotnet', '--info'], env=env),
            'cmake': capture(['cmake', '--version'], env=env).splitlines()[0],
            'compiler': env.get('VCTOOLSVERSION'),
            'rustc': optional_capture([executable('rustc', env), '-Vv'], env) if shutil.which('rustc', path=env.get('PATH')) else None,
            'cargo': optional_capture([executable('cargo', env), '-V'], env) if shutil.which('cargo', path=env.get('PATH')) else None,
            'environment': {k: v for k, v in env.items() if k.startswith('DOTNET_')},
            'timezone': 'Asia/Shanghai'}


def run_matrix(config, corpus, manifest, lock, output, env, only=None, resume=False):
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'run.json').exists() and not resume:
        raise ValueError('Output already contains a run; use --resume or choose another output directory.')
    metadata = {'protocol_version': 3, 'input_loading': 'canonical-utf8-single-contiguous-buffer-before-warmup', 'started_at': dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(), 'config': config,
                'corpus': {k: v for k, v in manifest.items() if k != 'files'}, 'sources': lock['sources'],
                'host': host_metadata(env), 'matrix': matrix()}
    metadata['harness_sha256'] = {p.relative_to(ROOT).as_posix(): sha256(p)
        for folder in (ROOT / 'src', ROOT / 'tools') for p in folder.rglob('*')
        if p.is_file() and '__pycache__' not in p.parts}
    metadata['harness_sha256']['CMakeLists.txt'] = sha256(ROOT / 'CMakeLists.txt')
    if resume and (output / 'run.json').exists():
        previous = read_json(output / 'run.json')
        if previous.get('protocol_version') != 3:
            raise ValueError('The output belongs to a different measurement protocol; choose a new output directory.')
        performance_keys = {key for key in metadata['harness_sha256'] if key.startswith('src/') or key == 'CMakeLists.txt'}
        if any(previous.get('harness_sha256', {}).get(key) != metadata['harness_sha256'][key] for key in performance_keys):
            raise ValueError('Benchmark source changed since the recorded run; choose a new output directory.')
        if previous['corpus']['corpus_sha256'] != metadata['corpus']['corpus_sha256'] or previous['config'] != config:
            raise ValueError('Cannot resume with a different corpus or configuration; choose a new output directory.')
        if {k: v['commit'] for k, v in previous['sources'].items()} != {k: v['commit'] for k, v in lock['sources'].items()}:
            raise ValueError('Cannot resume with different parser commits.')
        metadata['resumed_at'] = previous.get('resumed_at', []) + [metadata['started_at']]
        metadata['started_at'] = previous['started_at']
    write_json(output / 'run.json', metadata)
    build_info = {'commands': {p.name: read_json(p) for p in (WORK / 'build-logs').glob('*.command.json')}}
    compile_commands = WORK / 'native/compile_commands.json'
    if compile_commands.exists():
        build_info['native_compile_commands'] = read_json(compile_commands)
    write_json(output / 'build-info.json', build_info)
    for pinned in ('sources.lock.json', 'benchmark-config.json', 'global.json', 'rust-dependencies.lock'):
        if (ROOT / pinned).exists():
            shutil.copy2(ROOT / pinned, output / pinned)
    shutil.copy2(corpus.with_suffix('.manifest.json'), output / 'corpus.manifest.json')
    shutil.copy2(WORK / 'diagnostic/instrumentation.json', output / 'instrumentation.json')
    statuses = read_json(output / 'status.json') if resume and (output / 'status.json').exists() else {}
    for unavailable in matrix():
        if unavailable.get('unsupported'):
            statuses[unavailable['id']] = {'status': 'unsupported', 'reason': unavailable['unsupported']}
    jobs = matrix()
    random.Random(config['seed']).shuffle(jobs)
    for job in jobs:
        identifier = job['id']
        if only and not re.search(only, identifier):
            continue
        if job.get('unsupported'):
            statuses[identifier] = {'status': 'unsupported', 'reason': job['unsupported']}
            continue
        if resume and statuses.get(identifier, {}).get('status') == 'ok':
            continue
        destination = output / 'raw' / identifier
        destination.mkdir(parents=True, exist_ok=True)
        print(f'\n=== {identifier} ===', flush=True)
        job_env = dict(env)
        try:
            if job['kind'] == 'managed':
                base = WORK / 'projects' / job['variant']
                job_env.update({'SIMAI_RUNTIME': job['runtime'], 'SIMAI_CORPUS': str(corpus),
                    'SIMAI_TOLERANCE': job['tolerance'], 'SIMAI_WARMUP': str(config['warmup_count']),
                    'SIMAI_ITERATIONS': str(config['iteration_count']), 'SIMAI_AOT_VERSION': config['nativeaot_compiler_version'], 'SIMAI_ARTIFACTS': str(destination / 'bdn')})
                bench = base / 'bench/bin/Release' / config['framework'] / f'{job["variant"]}.bench.dll'
                run_command(['dotnet', bench], destination / 'benchmark.log', job_env, cwd=base / 'bench')
                if job['runtime'] == 'JIT':
                    probe = ['dotnet', base / 'probe/bin/Release' / config['framework'] / f'{job["variant"]}.probe.dll']
                else:
                    probe = [WORK / 'bin' / job['variant'] / 'aot' / f'{job["variant"]}.probe.exe']
                for mode in ('timing', 'memory'):
                    run_command(probe + ['--corpus', corpus, '--out', destination / f'{mode}.json', '--mode', mode,
                        '--rounds', config['file_rounds'], '--warmup', config['warmup_count'], '--tolerance', job['tolerance']],
                        destination / f'{mode}.log', job_env)
            elif job['kind'] == 'rust':
                for mode in ('benchmark', 'timing', 'memory'):
                    binary = WORK / 'bin/rust' / ('memory' if mode == 'memory' else 'timing') / 'simai-rust-bench.exe'
                    run_command([binary, '--corpus', corpus, '--mode', mode, '--pipeline', job['pipeline'],
                        '--out', destination / ('criterion' if mode == 'benchmark' else f'{mode}.json'),
                        '--rounds', config['file_rounds'], '--warmup', config['warmup_count']],
                        destination / f'{mode}.log', job_env)
            else:
                prefix = 'cimai' if job['variant'] == 'cimai' else 'maj'
                for mode in ('benchmark', 'timing', 'memory'):
                    binary = WORK / 'native' / f'{prefix}_{"memory" if mode == "memory" else "timing"}_bench.exe'
                    args = [binary, '--corpus', corpus, '--mode', mode, '--out', destination / f'{mode}.json',
                        '--tolerance', job['tolerance'], '--wrapper', job['wrapper'], '--rounds', config['file_rounds'],
                        '--warmup', config['warmup_count'], '--repetitions', config['iteration_count']]
                    if mode == 'benchmark':
                        args.extend([f'--benchmark_out={destination / "google.json"}', '--benchmark_out_format=json'])
                    run_command(args, destination / f'{mode}.log', job_env)
            statuses[identifier] = {'status': 'ok'}
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            statuses[identifier] = {'status': 'failed', 'reason': str(error)}
            print(str(error), flush=True)
        write_json(output / 'status.json', statuses)
    write_json(output / 'status.json', statuses)
    return statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'build', 'run', 'report', 'all'])
    parser.add_argument('--config', type=Path, default=ROOT / 'benchmark-config.json')
    parser.add_argument('--limit', type=int, default=0, help='0 = all samples; a subset always includes the largest file')
    parser.add_argument('--min-success-rate', type=float, help='Ranking acceptance threshold in [0, 1]; report preserves its previous setting, new runs use configuration (0.90)')
    parser.add_argument('--only', help='Regular expression selecting combinations (or variant names for build)')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/canonical-full')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--refresh-sources', action='store_true')
    args = parser.parse_args()
    config = read_json(args.config)
    if args.min_success_rate is not None:
        if not 0 <= args.min_success_rate <= 1:
            parser.error('--min-success-rate must be between 0 and 1')
        config['minimum_success_rate'] = args.min_success_rate
    if args.limit < 0:
        parser.error('--limit must be >= 0')
    if args.command == 'report':
        from report import generate_report
        generate_report(args.output.resolve(), minimum_success_rate=args.min_success_rate)
        return 0
    corpus, manifest, lock = prepare(config, args.refresh_sources, args.limit)
    if args.command == 'prepare':
        return 0
    env = developer_environment()
    if args.command in ('build', 'all'):
        build(config, env, args.only)
    if args.command in ('run', 'all'):
        statuses = run_matrix(config, corpus, manifest, lock, args.output.resolve(), env, args.only, args.resume)
        from report import generate_report
        summary = generate_report(args.output.resolve(), minimum_success_rate=config.get('minimum_success_rate', 0.90))
        return int(any(x['status'] in ('failed', 'invalid-result') for x in summary.get('all_combinations', summary['combinations'])))
    return 0



def generate_rust_project(config):
    project = WORK / 'projects/rust'
    project.mkdir(parents=True, exist_ok=True)
    package_path = (WORK / 'sources/maidata-rs').as_posix()
    main_path = (ROOT / 'src/rust/main.rs').as_posix()
    text = f"""[package]
name = "simai-rust-bench"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "simai-rust-bench"
path = "{main_path}"

[features]
allocation-probe = []

[dependencies]
maidata = {{ path = "{package_path}" }}
criterion = {{ version = "={config['criterion_version']}", default-features = false }}
serde = {{ version = "1", features = ["derive"] }}
serde_json = "1"

[profile.release]
opt-level = 3
debug = 1
lto = "thin"
codegen-units = 1
panic = "unwind"
"""
    (project / 'Cargo.toml').write_text(text, encoding='utf-8')
    lock = project / 'Cargo.lock'
    if not lock.exists():
        frozen = ROOT / 'rust-dependencies.lock'
        shutil.copy2(frozen if frozen.exists() else WORK / 'sources/maidata-rs/Cargo.lock', lock)


def ensure_rust(config, env):
    cargo_bin = WORK / 'toolchains/cargo/bin'
    toolchain = WORK / 'toolchains/rustup/toolchains' / f"{config['rust_toolchain']}-x86_64-pc-windows-msvc"
    if (toolchain / 'bin/rustc.exe').exists():
        return
    import urllib.request
    tools = WORK / 'toolchains'
    tools.mkdir(parents=True, exist_ok=True)
    installer = tools / 'rustup-init.exe'
    if not installer.exists():
        urllib.request.urlretrieve('https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe', installer)
    run_command([installer, '-y', '--no-modify-path', '--profile', 'minimal', '--default-toolchain',
        config['rust_toolchain'], '--default-host', 'x86_64-pc-windows-msvc'], tools / 'install.log', env)


def build_rust(config, env):
    ensure_rust(config, env)
    env = dict(env, RUSTUP_TOOLCHAIN=config['rust_toolchain'])
    cargo = executable('cargo', env)
    project = WORK / 'projects/rust/Cargo.toml'
    env = dict(env, CARGO_TARGET_DIR=str(WORK / 'rust-target'))
    frozen = ROOT / 'rust-dependencies.lock'
    run_command([cargo, 'fetch', '--manifest-path', project] + (['--locked'] if frozen.exists() else []), WORK / 'build-logs/rust-fetch.log', env)
    shutil.copy2(project.with_name('Cargo.lock'), frozen)
    for mode in ('timing', 'memory'):
        args = [cargo, 'build', '--release', '--locked', '--manifest-path', project, '--bin', 'simai-rust-bench']
        if mode == 'memory':
            args += ['--features', 'allocation-probe']
        run_command(args, WORK / f'build-logs/rust-{mode}.log', env)
        destination = WORK / f'bin/rust/{mode}'
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WORK / 'rust-target/release/simai-rust-bench.exe', destination)
    run_command([cargo, 'test', '--release', '--locked', '--features', 'allocation-probe', '--manifest-path', project, '--bin', 'simai-rust-bench', '--', '--test-threads=1'],
        WORK / 'build-logs/rust-tests.log', env)
    shutil.copy2(project.with_name('Cargo.lock'), ROOT / 'rust-dependencies.lock')

if __name__ == '__main__':
    sys.exit(main())
