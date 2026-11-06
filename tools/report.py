"""Aggregate official framework results and independent per-file diagnostics."""
from __future__ import annotations
import csv
import datetime as dt
import json
import math
from pathlib import Path
import statistics


BASELINE_ID = 'cimai-native'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def percentile(values, probability):
    """Nearest-rank percentile: ceil(p*N), one-based."""
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def files_of(profile):
    return profile.get('files', profile.get('Files', []))


def official_results(folder, kind):
    if kind == 'rust':
        return {method: load(folder / 'criterion/Parser' / method / 'new/estimates.json')['median']['point_estimate']
                for method in ('Corpus', 'LargestFile')}
    if kind == 'native':
        result = load(folder / 'google.json')
        units = {'ns': 1, 'us': 1e3, 'ms': 1e6, 's': 1e9}
        groups = {'Corpus': [], 'LargestFile': []}
        for entry in result['benchmarks']:
            method = entry['name'].split('/')[0]
            if entry.get('run_type') == 'iteration' and method in groups:
                if entry.get('error_occurred'):
                    raise ValueError(f'Google Benchmark error: {entry}')
                groups[method].append(entry['real_time'] * units[entry['time_unit']])
        if any(not x for x in groups.values()):
            raise ValueError(f'Missing raw Google Benchmark repetitions: {folder}')
        return {key: statistics.median(value) for key, value in groups.items()}
    exports = list((folder / 'bdn/results').glob('*-report-full.json'))
    if len(exports) != 1:
        raise ValueError(f'Expected one BenchmarkDotNet Full JSON export: {folder}')
    result = load(exports[0])
    groups = {}
    for entry in result['Benchmarks']:
        name = entry['Method']
        if name in ('Corpus', 'LargestFile'):
            if not entry.get('Statistics'):
                raise ValueError(f'BenchmarkDotNet did not measure {name}: {folder}')
            groups[name] = entry['Statistics']['Median']
    if len(groups) != 2:
        raise ValueError(f'Missing BenchmarkDotNet results: {folder}')
    return groups



def validate_preload(profile, manifest):
    """Reject profiles that did not prove full in-memory input coverage."""
    def field(snake, pascal):
        return profile.get(snake, profile.get(pascal))
    if field('preloaded', 'Preloaded') is not True:
        raise ValueError('The profile does not confirm preloading before measurement.')
    if field('input_files', 'InputFiles') != manifest['count']:
        raise ValueError('The preloaded file count differs from the corpus.')
    if field('input_bytes', 'InputBytes') != manifest['bytes']:
        raise ValueError('The preloaded input byte count differs from the corpus.')
    retained = field('preloaded_bytes', 'PreloadedBytes')
    if not isinstance(retained, int) or retained < manifest['bytes'] + 12:
        raise ValueError('The complete corpus was not retained in memory.')
    charts = field('input_charts', 'InputCharts')
    if charts is not None and charts != manifest.get('chart_count', charts):
        raise ValueError('The preloaded chart-field count differs from the corpus.')


def apply_ranking(rows, minimum_success_rate):
    """Filter acceptance rates, retaining cimai as the fixed timing reference."""
    if not 0 <= minimum_success_rate <= 1:
        raise ValueError('The success-rate threshold must be between 0 and 1.')
    eligible = []
    for row in rows:
        rate = row.get('success_rate')
        row.pop('relative_to_fastest', None)
        row['relative_to_baseline'] = None
        row['is_baseline'] = False
        row['ranking_eligible'] = row['status'] == 'ok' and (
            (rate is not None and rate >= minimum_success_rate)
            or (row['id'] == BASELINE_ID and rate is None))
        if row['ranking_eligible']:
            row['ranking_exclusion_reason'] = None
            eligible.append(row)
        elif row['status'] != 'ok':
            row['ranking_exclusion_reason'] = row.get('reason', row['status'])
        elif rate is None:
            row['ranking_exclusion_reason'] = '解析成功率未知；完成率不用于选择基准。'
        else:
            row['ranking_exclusion_reason'] = f'成功率 {rate:.2%} 低于 {minimum_success_rate:.2%} 门槛。'
    baseline = next((row for row in eligible if row['id'] == BASELINE_ID), None)
    for row in rows:
        row['baseline_id'] = baseline['id'] if baseline is not None else None
    if baseline is None:
        return None
    baseline['is_baseline'] = True
    for row in eligible:
        row['relative_to_baseline'] = row['total_median_ns'] / baseline['total_median_ns']
    return baseline['id']


def analyze(job, folder, manifest):
    timing = load(folder / 'timing.json')
    memory = load(folder / 'memory.json')
    if manifest.get('schema_version', 1) >= 2:
        validate_preload(timing, manifest)
        validate_preload(memory, manifest)
    times = files_of(timing)
    allocations = files_of(memory)
    expected = [x['path'] for x in manifest['files']]
    if [x['Path'] for x in times] != expected or [x['Path'] for x in allocations] != expected:
        raise ValueError('Profile does not match corpus order.')
    if any(x.get('Inconsistent') for x in times):
        raise ValueError('Success/failure changed across timing repetitions.')
    if any(x['Success'] != y['Success'] for x, y in zip(times, allocations)):
        raise ValueError('Timing and allocation probes disagree about success.')
    if any(x.get('NativeLiveAfterFree', 0) for x in allocations):
        raise ValueError('Native memory remained allocated after a file parse.')
    for observed, source in zip(times, manifest['files']):
        if observed['Bytes'] != source['bytes'] or not observed['Nanoseconds']:
            raise ValueError('Invalid per-file profile.')
    if job['variant'].startswith('sharp') and manifest.get('schema_version', 1) >= 2:
        for timed, allocated, source in zip(times, allocations, manifest['files']):
            if timed.get('ChartAttempts') != source['charts'] or allocated.get('ChartAttempts') != source['charts']:
                raise ValueError('A SimaiSharp file did not attempt every declared chart.')
    official = official_results(folder, job['kind'])
    total_ns = official['Corpus']
    per_file = [statistics.median(x['Nanoseconds']) for x in times]
    peak = max(allocations, key=lambda x: x.get('ManagedAllocated', 0) + x.get('NativeAllocated', 0))
    accepted = sum(x['Success'] for x in times)
    largest = next(x for x in times if x['Path'] == manifest['largest']['path'])
    row = dict(job)
    row.update({
        'status': 'ok', 'total_median_ns': total_ns,
        'input_preloaded': timing.get('preloaded', timing.get('Preloaded', False)),
        'chart_fields': manifest.get('chart_count'),
        'chart_attempts': sum(x.get('ChartAttempts', -1) for x in times) if job['variant'].startswith('sharp') and manifest.get('schema_version', 1) >= 2 else None,
        'throughput_mib_s': manifest['bytes'] / (1024 ** 2) / (total_ns / 1e9),
        'throughput_files_s': len(times) / (total_ns / 1e9),
        'success_definition': 'completion-only' if job['variant'] == 'cimai' else ('no-errors-or-panic' if job['kind'] == 'rust' else 'api-acceptance'),
        'file_p50_ns': statistics.median(per_file), 'file_p99_ns': percentile(per_file, .99),
        'largest_median_ns': official['LargestFile'], 'largest_accepted': largest['Success'],
        'max_file_allocated_bytes': peak.get('ManagedAllocated', 0) + peak.get('NativeAllocated', 0),
        'max_file_allocated_path': peak['Path'],
        'max_managed_allocated_bytes': max(x.get('ManagedAllocated', 0) for x in allocations),
        'max_native_allocated_bytes': max(x.get('NativeAllocated', 0) for x in allocations),
        'max_native_live_after_free_bytes': max(x.get('NativeLiveAfterFree', 0) for x in allocations),
        'peak_working_set_bytes': timing.get('peak_working_set_bytes', timing.get('PeakWorkingSetBytes')),
        'peak_commit_bytes': timing.get('peak_paged_memory_bytes', timing.get('PeakPagedMemoryBytes')),
        'completed_files': len(times), 'accepted_files': accepted,
        'success_rate': accepted / len(times) if job['variant'] != 'cimai' else None,
        'completion_rate': accepted / len(times),
    })
    return row, times, allocations


def common_success(rows, profiles, manifest):
    good = [row for row in rows if row['status'] == 'ok']
    if not good:
        return [], []
    indices = [i for i in range(manifest['count']) if all(profiles[row['id']][i]['Success'] for row in good)]
    if not indices:
        return [], []
    total_bytes = sum(manifest['files'][i]['bytes'] for i in indices)
    result = []
    for row in good:
        profile = profiles[row['id']]
        rounds = len(profile[0]['Nanoseconds'])
        totals = [sum(profile[i]['Nanoseconds'][r] for i in indices) for r in range(rounds)]
        elapsed = statistics.median(totals)
        result.append({'id': row['id'], 'files': len(indices), 'bytes': total_bytes, 'median_ns': elapsed,
                       'throughput_mib_s': total_bytes / 1024 ** 2 / (elapsed / 1e9)})
    baseline = next((row for row in result if row['id'] == BASELINE_ID), None)
    for row in result:
        row['baseline_id'] = baseline['id'] if baseline is not None else None
        row['relative_to_baseline'] = row['median_ns'] / baseline['median_ns'] if baseline is not None else None
    return sorted(result, key=lambda x: x['median_ns']), [manifest['files'][i]['path'] for i in indices]


def fmt(value, scale=1, digits=3):
    return '—' if value is None else f'{value / scale:,.{digits}f}'


def fmt_ratio(value):
    return '—' if value is None else f'{value:.3f}×'


def generate_report(output, minimum_success_rate=None):
    output = Path(output)
    metadata = load(output / 'run.json')
    manifest = load(output / 'corpus.manifest.json')
    statuses = load(output / 'status.json')
    if minimum_success_rate is None:
        previous = load(output / 'summary.json') if (output / 'summary.json').exists() else {}
        minimum_success_rate = previous.get('ranking', {}).get(
            'minimum_success_rate', metadata['config'].get('minimum_success_rate', 0.90))
    rows, profiles, failures = [], {}, []
    for job in metadata['matrix']:
        status = statuses.get(job['id'], {'status': 'not-run', 'reason': '未选择或尚未运行'})
        if status['status'] != 'ok':
            rows.append(dict(job, **status))
            continue
        try:
            row, times, allocations = analyze(job, output / 'raw' / job['id'], manifest)
            profiles[job['id']] = times
            rows.append(row)
            for timing, memory in zip(times, allocations):
                if not timing['Success']:
                    failures.append({'combination': job['id'], 'path': timing['Path'], 'bytes': timing['Bytes'],
                        'error': timing.get('Error'), 'median_ns': statistics.median(timing['Nanoseconds']),
                        'allocated_bytes': memory.get('ManagedAllocated', 0) + memory.get('NativeAllocated', 0)})
        except (ValueError, KeyError, FileNotFoundError) as error:
            rows.append(dict(job, status='invalid-result', reason=str(error)))
    good = [x for x in rows if x['status'] == 'ok']
    baseline = apply_ranking(rows, minimum_success_rate)
    ranked = sorted((row for row in rows if row['ranking_eligible']), key=lambda row: row['total_median_ns'])
    common, common_paths = common_success(ranked, profiles, manifest)
    (output / 'common-success.txt').write_text('\n'.join(common_paths), encoding='utf-8')
    summary = {'generated_at': dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
        'corpus': metadata['corpus'], 'combinations': ranked, 'ranked_combinations': ranked, 'all_combinations': rows,
        'ranking': {'minimum_success_rate': minimum_success_rate, 'baseline': baseline,
                    'requested_baseline': BASELINE_ID, 'eligible_count': len(ranked),
                    'qualified_count': sum(row['success_rate'] is not None for row in ranked),
                    'policy': 'verified-file-acceptance; cimai-native is the fixed timing reference with unknown acceptance',
                    'ratio_definition': 'combination median / cimai-native median over the same input files'},
        'common_success': common,
        'complete': all(x['status'] in ('ok', 'unsupported') for x in rows)}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    columns = ['id', 'status', 'variant', 'runtime', 'tolerance', 'wrapper', 'total_median_ns',
        'throughput_mib_s', 'throughput_files_s', 'file_p50_ns', 'file_p99_ns', 'largest_median_ns',
        'largest_accepted', 'success_definition', 'max_file_allocated_bytes', 'max_managed_allocated_bytes',
        'max_native_allocated_bytes', 'peak_working_set_bytes', 'peak_commit_bytes',
        'accepted_files', 'completed_files', 'success_rate', 'completion_rate', 'ranking_eligible', 'ranking_exclusion_reason',
        'input_preloaded', 'chart_fields', 'chart_attempts', 'baseline_id', 'is_baseline', 'relative_to_baseline', 'reason']
    with (output / 'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(sorted(ranked, key=lambda row: row['total_median_ns']))
    with (output / 'all-combinations.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    with (output / 'failures.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, ['combination', 'path', 'bytes', 'error', 'median_ns', 'allocated_bytes'])
        writer.writeheader()
        writer.writerows(failures)
    write_markdown(output, metadata, manifest, rows, common, summary)
    print(f'Report: {output / "report.md"}; {len(good)} measured / {len(rows)} combinations', flush=True)
    return summary


def write_markdown(output, metadata, manifest, rows, common, summary):
    config = metadata['config']
    host = metadata['host']
    good = [x for x in rows if x['status'] == 'ok']
    ranked = [x for x in good if x['ranking_eligible']]
    excluded = [x for x in good if not x['ranking_eligible']]
    lines = [
        '# Simai parser 基准测试报告', '',
        f'开始时间：{metadata["started_at"]}（Asia/Shanghai）。生成时间：{summary["generated_at"]}。', '',
        f'样本：**{manifest["count"]:,} 个文件**，**{manifest["bytes"]:,} 字节**；固定乱序种子 `{manifest["seed"]}`。',
        f'原始文件共 **{manifest.get("source_bytes", manifest["bytes"]):,} 字节**；预加载前将 **{manifest.get("transcoded_files", 0)} 个 UTF-16/UTF-32 文件**转为 UTF-8，原文件不修改，原字节数与原始哈希逐文件保留。' if manifest.get('schema_version', 1) >= 3 else '输入保持原始编码。',
        f'有效非空难度字段：**{manifest.get("chart_count", "未记录"):,} 个**；覆盖目录中发现的 **{manifest.get("discovered_files", manifest["count"]):,} 个文件**。' if manifest.get('chart_count') is not None else '旧协议未记录难度字段数量。',
        f'输入包 SHA-256：`{manifest["corpus_sha256"]}`。逐文件哈希见 `corpus.manifest.json`。', '',
        '**本报告使用显式抽取的子集，不是全量测试。**' if manifest.get('full_corpus') is False else '全部发现的样本文件均参与测量，未按成功率或文件大小筛选输入。', '',
        f'最大文件：`{manifest["largest"]["path"]}`，**{manifest["largest"]["bytes"]:,} 字节**。', '',
        '## 测试方法与解释', '',
        '- 采用参考 tools/MajSimai.ManagedBenchmark、tools/NativeBenchmark 和 benchmark-native.py 的完整内存语料方式：每个测量进程在预热前一次性读取并关闭输入文件，保留一个连续缓冲区和所有文件的偏移/长度。C# 使用 ReadOnlyMemory/MemoryStream 的数组切片，C/C++ 使用 string_view，Rust 使用借用切片；计时内不打开文件、不枚举目录、不重新读取数据。为覆盖所有编码的谱面，UTF-16/32 在打包阶段一次性转为 UTF-8，打包阶段的转码和编码检查不计入解析耗时；所有解析器使用同一份规范化输入。吞吐按实际送入解析器的 UTF-8 字节数计算。',
        f'- C# 整批与最大文件使用 BenchmarkDotNet {config["benchmarkdotnet_version"]}；C/C++ 使用 Google Benchmark v1.9.5（固定 commit）。各组合独立执行，计时与内存诊断分属不同进程；预热 {config["warmup_count"]} 次，正式 {config["iteration_count"]} 次，不剔除离群值，每次整批调用恰好遍历全部文件一次。单文件诊断另行预热后执行 {config["file_rounds"]} 轮。',
        '- 总耗时和最大文件耗时为框架正式样本的中位数（wall clock）。吞吐 = 输入字节或文件数 / 总耗时中位数，包含被拒绝的输入。倍率 = 该组合整批中位数 / cimai 的整批中位数，cimai 固定为 1.000×；大于 1 表示耗时更长，被排除组合没有倍率。',
        '- 单文件 P50/P99：先对每个文件的多轮诊断耗时取中位数，再在这些文件中位数上计算 P50 与 nearest-rank P99（ceil(0.99*N)）；它们描述文件大小与内容分布，不等同于同一文件的尾延迟。',
        '- 测量期间关闭控制台和库日志，避免终端 I/O 进入计时；2.2.3 的异常日志通过无输出计数器检测。Rust panic hook 关闭，panic 由 catch_unwind 捕获并计为失败。',
        '- 成功率 = 整个文件被公开 API 接受的数量 / 全部输入文件数；SimaiSharp 任一难度失败即记该文件失败。它不证明输出语义一致或支持所有语法。MajSimai 2.2.3 内部吞掉并写入 Console 的谱面异常也计为失败；重构版 Lenient 允许丢弃不合法音符/谱面，因此接受率不能视为语法正确率。cimai 不暴露错误状态，成功率记 N/A，仅统计完成率。',
        '- Strict 和其他拒绝输入的实现可能提前退出；全样本耗时应与成功率一并阅读。下方另外给出达到门槛的组合共同接受文件的诊断比较，并以 cimai 在同一文件子集上完成调用的耗时为基准，避免把早期拒绝当成完整解析加速。',
        '- 最大文件列标记其接受/拒绝状态；拒绝时的耗时表示返回失败所用时间。',
        '- 最大分配 = 单次完整文件调用的累计分配字节数最大值（托管 GC + 被跟踪的原生申请）；它不是最大一次 malloc、存活堆大小或 RSS。托管计数使用 GC.GetTotalAllocatedBytes(true)，包括 Parallel.For 工作线程。BDN 自带 MemoryDiagnoser 结果保留在原始文件中；最终表采用独立、全线程诊断。',
        '- cimai 的内存诊断拦截 malloc/calloc/realloc/free；realloc 按新请求大小累计。C++ 诊断拦截普通 operator new/delete，并跟踪 MajSimai 的 Marshal.AllocHGlobal/FreeHGlobal。诊断计时不参与性能结果。跟踪器自身、CRT/GC 内部原生保留区和 OS 分配不计入“最大分配”；进程峰值 RSS/commit 另列，包含运行时、预载样本与诊断记录。',
        '- C++ ParseAsView 包含托管解析、原生 ABI 结果转换和 RAII 释放；Parse 还包含复制为 C++ string/vector 模型与其析构。C# 不强制立即 GC，正常 GC 成本由框架正式轮次承担。',
        '- 两个 SimaiSharp 分支只有单谱面解析 API：适配器在计时内扫描完整 maidata 的 inote_1..7，对所有有效非空字段逐一调用 Deserialize，单个难度抛出异常后仍继续尝试同文件的其余难度。诊断中的 ChartAttempts 必须逐文件等于输入清单中的谱面数，否则该组合结果无效。master 在计时内转为 UTF-16 string，one-pass 直接使用 UTF-8 span。其他解析器使用完整文件 API。各库模型与哈希工作量不同，不能据此推断输出等价。', '',
        f'- Rust 使用 Criterion {config.get("criterion_version", "0.5.1")}，10 个 Flat 样本、1 秒框架预热、3 秒目标测量时间（框架可自动延长）；预载后另有与其他库相同的整批次数预热。正式中位数读取 Criterion estimates.json 的 median.point_estimate。',
        '- maidata-rs-parse 测 lex_maidata 指令 AST；maidata-rs-materialize 在同一解析后对全部难度 materialize 为带时间音符。Rust API 所需的 UTF-8 借用校验、诊断状态检查和结果析构在计时内；返回非空 errors 或 panic 均记失败，warning 不记失败。错误码 -1 表示 panic、-2 表示非法 UTF-8、正数表示 errors 条数。Rust 分配在独立 GlobalAlloc 构建中统计。', '',
        '## 全样本结果（cimai 为 1.000× 基准）', '',
        f'排名门槛：**{summary["ranking"]["minimum_success_rate"]:.2%}**。固定 1× 基准：`{summary["ranking"]["baseline"] or "cimai 测量结果不可用，倍率留空"}`。cimai 按用户指定作为耗时参照列入主表，其解析成功率为 N/A，完成率不能替代成功率；其他组合须达到门槛且成功率可验证，低成功率和其他成功率未知的结果单列为诊断。', '',
        '| 组合 | 总中位数 ms | MiB/s | 文件/s | 单文件 P50 µs | 单文件 P99 µs | 最大文件 ms | 最大分配 MiB | 峰值 RSS MiB | 成功率 | 相对 cimai 耗时 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for row in sorted(ranked, key=lambda x: x['total_median_ns']):
        rate = f'{row["success_rate"]:.2%}' if row['success_rate'] is not None else f'N/A（完成 {row["completion_rate"]:.2%}）'
        accepted = '完成，未验证' if row['variant'] == 'cimai' else ('接受' if row['largest_accepted'] else '拒绝')
        lines.append(f'| {row["id"]} | {fmt(row["total_median_ns"], 1e6)} | {fmt(row["throughput_mib_s"])} | {fmt(row["throughput_files_s"], digits=1)} | {fmt(row["file_p50_ns"], 1e3)} | {fmt(row["file_p99_ns"], 1e3)} | {fmt(row["largest_median_ns"], 1e6)}（{accepted}） | {fmt(row["max_file_allocated_bytes"], 1024**2)} | {fmt(row["peak_working_set_bytes"], 1024**2)} | {rate} | {fmt_ratio(row["relative_to_baseline"])} |')
    lines += ['', '## 排除的诊断结果（不参与排名）', '',
              '| 组合 | 总中位数 ms | MiB/s | 成功率 | 排除原因 |', '|---|---:|---:|---:|---|']
    for row in excluded:
        rate = '未知' if row['success_rate'] is None else f"{row['success_rate']:.2%}"
        lines.append(f"| {row['id']} | {fmt(row['total_median_ns'], 1e6)} | {fmt(row['throughput_mib_s'])} | {rate} | {row['ranking_exclusion_reason']} |")
    lines += ['', '## 输入覆盖与预加载验证', '',
              '计时和内存诊断均逐文件核对路径、原始字节数与完整输入清单；所有组合失败后继续下一文件。完整文件 API 保留自身的错误语义，Strict 可以在文件内提前拒绝；这不会跳过语料中的后续文件。', '',
              '| 组合 | 已预加载 | 完成文件数 | 输入谱面字段数 | 逐难度调用数 |', '|---|---|---:|---:|---:|']
    for row in good:
        attempts = str(row['chart_attempts']) if row.get('chart_attempts') is not None else '完整文件 API'
        lines.append(f"| {row['id']} | {'是' if row['input_preloaded'] else '未记录'} | {row['completed_files']:,} | {row.get('chart_fields') or '未记录'} | {attempts} |")
    lines += ['', '## 解析失败的解释', '', 'SimaiSharp one-pass 的锁定提交 `6e5f2d80d978d6d96e3c3320e0aa43f38cf7c654` 在 FindInsertIndexForTime 中先访问 timedList[^1]，之后才检查列表是否为空。对 `(120){4}1,2,3,4,E` 的单独复现触发 ArgumentOutOfRangeException。该提交的低耗时应结合失败率阅读，不能视为完成解析的速度。失败名单保存在 failures.csv。', '', '## 共同接受文件比较', '']
    if common:
        lines += [f'达到门槛的组合共同接受且 cimai 完成调用的文件交集：**{common[0]["files"]:,} 文件**，**{common[0]["bytes"]:,} 字节**。名单见 `common-success.txt`。', '',
            '本表使用独立诊断中的逐文件时间，在每一轮仅累加交集文件的解析耗时，再取轮次中位数；倍率 = 该组合的交集诊断中位数 / cimai 的同一交集诊断中位数，cimai 固定为 1.000×。cimai 仅验证完成调用。未重新运行框架整批基准，因此该指标与上表的框架整批耗时分开使用。', '',
            '| 组合 | 共同文件诊断中位数 ms | MiB/s | 相对 cimai 耗时 |', '|---|---:|---:|---:|']
        for row in common:
            lines.append(f'| {row["id"]} | {fmt(row["median_ns"], 1e6)} | {fmt(row["throughput_mib_s"])} | {fmt_ratio(row["relative_to_baseline"])} |')
    else:
        lines += ['所有合格组合的共同接受文件交集为空，因此不产生交集排名。' if good else '尚无可用于交集比较的完整结果。']
    lines += ['', '## 主表组合的内存明细', '', '| 组合 | 最大托管分配 MiB | 最大原生分配 MiB | 峰值 commit MiB | 最大分配文件 |', '|---|---:|---:|---:|---|']
    for row in ranked:
        lines.append(f'| {row["id"]} | {fmt(row["max_managed_allocated_bytes"], 1024**2)} | {fmt(row["max_native_allocated_bytes"], 1024**2)} | {fmt(row["peak_commit_bytes"], 1024**2)} | {row["max_file_allocated_path"]} |')
    lines += ['', '## 不支持、失败或未运行组合', '', '| 组合 | 状态 | 原因 |', '|---|---|---|']
    for row in rows:
        if row['status'] != 'ok':
            lines.append(f'| {row["id"]} | {row["status"]} | {row.get("reason", "").replace(chr(10), " ").replace("|", "/")} |')
    lines += ['', '## 源码版本', '', '源码通过 git archive 从以下 commit 导出，不切换或修改原仓库工作区；未提交改动不参与构建。`sources.lock.json` 固定这些 commit。', '',
        '| 变体 | 请求 ref | 完整 commit | commit 时间 |', '|---|---|---|---|']
    for name, source in metadata['sources'].items():
        lines.append(f'| {name} | {source["ref"]} | {source["commit"]} | {source["commit_time"]} |')
    lines += ['', '## 构建参数与环境', '',
        f'- 主机：`{host["os"]}` / `{host["architecture"]}`；CPU：`{json.dumps(host["cpu"], ensure_ascii=False)}`；内存 {host["physical_memory_bytes"] / 1024**3:.2f} GiB。',
        f'- 统一目标 `{config["framework"]}`，RID `{config["runtime_identifier"]}`，Release、Optimize=true、LangVersion=14、AllowUnsafeBlocks=true、Deterministic=true。为控制运行时变量，将各版本原始 Runtime/src 编译到独立 SDK 项目；MajSimai 2.2.3 原多目标最高 net9.0，实验中源代码统一以 net10.0 编译，原源码未修改。',
        '- JIT：TieredCompilation=0、TieredPGO=0、ReadyToRun=0；workstation GC、非 concurrent GC；NativeAOT 同样为 workstation / 非 concurrent GC、IlcOptimizationPreference=Speed，使用 SDK 默认指令集配置。未启用 native CPU 特化参数。',
        f'- 原生：{host["cmake"]}、MSVC {host.get("compiler")}、Ninja；C++17，/O2 /Ob3 /DNDEBUG /MD，cimai 静态链接、/GL，链接 /LTCG。MSVC 不支持 C23 bool 关键字，cimai 按 C17 并强制包含 stdbool.h 编译，其余源码保持 commit 原文。无 /fp:fast 或 /arch 特化。',
        f'- Google Benchmark commit：`{config["google_benchmark_commit"]}`；BenchmarkDotNet NuGet：`{config["benchmarkdotnet_version"]}`。',
        '- CppGen 使用重构 commit 自带的生成器，从该 commit 编译的原生 ABI 生成 wrapper；计时原生库包含原始 Runtime 和仅用于关闭日志/读取计数的附加 exports。内存专用库将 Marshal 申请/释放重定向到诊断器，改动位置与哈希见 instrumentation.json。',
        '- 全部具体构建命令与原生编译命令已复制到 build-info.json；原始文件位于 .work/build-logs/*.command.json 和 .work/native/compile_commands.json。每个测量命令与 stdout/stderr 见 raw/<组合>/*.command.json / *.log；BDN JSON 包含 job 与运行时参数。',
        '- 组合以固定种子打乱后串行运行；未固定 CPU 亲和性、CPU 频率或系统电源方案，存在调度、后台负载、温度与 GC 噪声。本报告为当前主机结果。', '',
        '```text', host['dotnet_info'], '```', '',
        '## Rust 构建信息', '',
        '- Release：opt-level=3、lto=thin、codegen-units=1、debug=1、panic=unwind；默认目标 x86_64-pc-windows-msvc，未启用 target-cpu=native。计时使用 System allocator；独立诊断构建启用 allocation-probe 特性，底层仍使用 System。Cargo.lock 见 rust-dependencies.lock。', '',
        '```text', host.get('rustc') or 'Rust 尚未运行', host.get('cargo') or '', '```', '',
        '## 原始数据', '',
        '- summary.csv：包含固定参照 cimai 与达到成功率门槛的组合；baseline_id 指明参照，relative_to_baseline 为相对 cimai 的耗时倍率。summary.json 同时保留筛选策略、主表结果和全部诊断状态。',
        '- all-combinations.csv：全部组合的原始汇总值、排除原因和覆盖计数。',
        '- failures.csv：所有失败文件与错误类型/错误码。',
        '- raw/<组合>/：框架原始报告、逐文件多轮时间、逐文件分配与完整命令日志。',
        '- corpus.manifest.json：固定顺序的样本路径、字节数和 SHA-256。',
        '- run.json：测试矩阵、构建配置、版本与主机信息。',
    ]
    (output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
