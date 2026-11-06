"""Regression tests for benchmark inputs and statistics."""
import json
import csv
from unittest.mock import patch
from pathlib import Path
import struct
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from bench import matrix, pack_corpus, select_samples, chart_fields, normalize_input
from report import common_success, official_results, percentile, apply_ranking, validate_preload, generate_report

class CorpusTests(unittest.TestCase):
    def test_subset_is_reproducible_and_includes_largest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for i in range(12):
                p = root / str(i) / 'maidata.txt'
                p.parent.mkdir()
                p.write_bytes(bytes([i]) * (i + 1))
                paths.append(p)
            first = select_samples(paths, 4, 3730)
            self.assertEqual(first, select_samples(list(reversed(paths)), 4, 3730))
            self.assertEqual(4, len(set(first)))
            self.assertIn(paths[-1], first)

    def test_packed_inputs_preserve_exact_bytes_and_unicode_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample = root / 'samples/中文/maidata.txt'
            sample.parent.mkdir(parents=True)
            content = b'\xef\xbb\xbf&inote_1=(120){4}1,\r\n\x00'
            sample.write_bytes(content)
            target = root / 'corpus.bin'
            manifest = pack_corpus(root / 'samples', target, 0, 3730)
            with target.open('rb') as stream:
                self.assertEqual(b'SIMAI001', stream.read(8))
                self.assertEqual(1, struct.unpack('<I', stream.read(4))[0])
                name_len = struct.unpack('<I', stream.read(4))[0]
                self.assertEqual('中文/maidata.txt', stream.read(name_len).decode())
                byte_len = struct.unpack('<I', stream.read(4))[0]
                self.assertEqual(content, stream.read(byte_len))
                self.assertEqual(b'', stream.read())
            self.assertEqual(len(content), manifest['bytes'])
            self.assertEqual(manifest, pack_corpus(root / 'samples', target, 0, 3730))

    def test_empty_corpus_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                pack_corpus(Path(directory), Path(directory) / 'out.bin', 0, 1)

    def test_legacy_unavailable_features_are_explicit(self):
        rows = matrix()
        self.assertEqual(23, len(rows))
        self.assertEqual(len(rows), len({x['id'] for x in rows}))
        self.assertEqual(17, sum('unsupported' not in x for x in rows))
        for row in rows:
            if row['variant'] == 'maj-2.2.3' and (row['tolerance'] == 'Strict' or row['wrapper'] != '-'):
                self.assertIn('unsupported', row)
        self.assertEqual(4, sum(x['variant'].startswith('sharp') for x in rows))

class StatisticsTests(unittest.TestCase):
    def test_p99_is_nearest_rank(self):
        self.assertEqual(99, percentile(list(range(1, 101)), .99))
        self.assertEqual(2, percentile([2, 1], .99))
        self.assertIsNone(percentile([], .99))

    def test_common_subset_uses_cimai_on_same_files_and_preserves_round_pairing(self):
        rows = [{'id': 'cimai-native', 'status': 'ok'}, {'id': 'b', 'status': 'ok'}]
        profiles = {
            'cimai-native': [
                {'Success': True, 'Nanoseconds': [10, 20, 30]},
                {'Success': True, 'Nanoseconds': [1, 1, 1]},
                {'Success': True, 'Nanoseconds': [60, 10, 20]}],
            'b': [
                {'Success': True, 'Nanoseconds': [5, 15, 10]},
                {'Success': False, 'Nanoseconds': [100, 100, 100]},
                {'Success': True, 'Nanoseconds': [25, 5, 10]}],
        }
        manifest = {'count': 3, 'files': [{'path': 'x', 'bytes': 10}, {'path': 'y', 'bytes': 20},
                                         {'path': 'z', 'bytes': 30}]}
        result, names = common_success(rows, profiles, manifest)
        self.assertEqual(['x', 'z'], names)
        self.assertEqual('b', result[0]['id'])
        self.assertEqual(20, result[0]['median_ns'])
        self.assertEqual(.4, result[0]['relative_to_baseline'])
        self.assertEqual(50, result[1]['median_ns'])
        self.assertEqual(1, result[1]['relative_to_baseline'])
        self.assertEqual(40, result[1]['bytes'])
        self.assertTrue(all(row['baseline_id'] == 'cimai-native' for row in result))

    def test_common_subset_without_cimai_does_not_select_another_reference(self):
        rows = [{'id': 'b', 'status': 'ok'}]
        profiles = {'b': [{'Success': True, 'Nanoseconds': [10, 20, 30]}]}
        manifest = {'count': 1, 'files': [{'path': 'x', 'bytes': 10}]}
        result, names = common_success(rows, profiles, manifest)
        self.assertEqual(['x'], names)
        self.assertEqual(20, result[0]['median_ns'])
        self.assertIsNone(result[0]['relative_to_baseline'])
        self.assertIsNone(result[0]['baseline_id'])

    def test_google_uses_raw_wall_clock_repetitions(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            entries = []
            for method in ['Corpus', 'LargestFile']:
                for elapsed in [3, 1, 2]:
                    entries.append({'name': method + '/iterations:1/real_time', 'run_type': 'iteration',
                                    'real_time': elapsed, 'cpu_time': 999, 'time_unit': 'ms'})
                entries.append({'name': method + '_mean', 'run_type': 'aggregate', 'real_time': 999, 'time_unit': 'ms'})
            (folder / 'google.json').write_text(json.dumps({'benchmarks': entries}))
            self.assertEqual({'Corpus': 2e6, 'LargestFile': 2e6}, official_results(folder, 'native'))

    def test_criterion_uses_bootstrap_median_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for method, value in [('Corpus', 123456.0), ('LargestFile', 42.0)]:
                target = folder / 'criterion/Parser' / method / 'new'
                target.mkdir(parents=True)
                (target / 'estimates.json').write_text(json.dumps({'median': {'point_estimate': value}, 'mean': {'point_estimate': 999.0}}))
            self.assertEqual({'Corpus': 123456.0, 'LargestFile': 42.0}, official_results(folder, 'rust'))

    def test_bdn_missing_statistics_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            exports = folder / 'bdn/results'
            exports.mkdir(parents=True)
            (exports / 'test-report-full.json').write_text(json.dumps({'Benchmarks': [{'Method': 'Corpus', 'Statistics': None}]}))
            with self.assertRaises(ValueError):
                official_results(folder, 'managed')


class CoverageTests(unittest.TestCase):
    def test_chart_manifest_counts_all_effective_difficulties(self):
        data = b'\xef\xbb\xbf&title=A&B\r\n&inote_1=old\n&inote_7=seven\r&inote_1=new\n&lv_2=5'
        fields = chart_fields(data)
        self.assertEqual({1, 7}, set(fields))
        self.assertEqual(b'new\n', bytes(fields[1]))
        self.assertEqual(b'seven\r', bytes(fields[7]))


    def test_all_bom_encodings_produce_the_same_chart_input(self):
        text = '&title=中文\r\n&inote_1=(120){4}1,E\r\n&inote_7=(150){4}2,E\n'
        for encoding in ('utf-16', 'utf-16-be', 'utf-32', 'utf-32-be'):
            raw = text.encode(encoding)
            if encoding == 'utf-16-be':
                raw = b'\xfe\xff' + raw
            if encoding == 'utf-32-be':
                raw = b'\x00\x00\xfe\xff' + raw
            normalized, _, changed = normalize_input(raw)
            self.assertTrue(changed)
            self.assertEqual(text.encode('utf-8'), normalized)
            self.assertEqual({1, 7}, set(chart_fields(normalized)))

    def test_utf16_corpus_preserves_original_hash_and_counts_every_chart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample = root / 'samples/a/maidata.txt'
            sample.parent.mkdir(parents=True)
            original = '&inote_1=(120){4}1,E\n&inote_7=(150){4}2,E\n'.encode('utf-16')
            sample.write_bytes(original)
            manifest = pack_corpus(root / 'samples', root / 'out.bin', 0, 1)
            self.assertEqual(2, manifest['chart_count'])
            self.assertEqual(1, manifest['transcoded_files'])
            self.assertEqual(len(original), manifest['source_bytes'])
            self.assertEqual(original, sample.read_bytes())
            self.assertNotEqual(manifest['files'][0]['sha256'], manifest['files'][0]['source_sha256'])

    def test_preload_coverage_requires_every_file_and_byte(self):
        manifest = {'count': 2, 'bytes': 40}
        profile = {'preloaded': True, 'input_files': 2, 'input_bytes': 40, 'preloaded_bytes': 80}
        validate_preload(profile, manifest)
        for change in [{'preloaded': False}, {'input_files': 1}, {'input_bytes': 39}, {'preloaded_bytes': 40}]:
            with self.assertRaises(ValueError):
                validate_preload(dict(profile, **change), manifest)

class RankingTests(unittest.TestCase):
    def test_cimai_is_fixed_reference_and_low_acceptance_remains_excluded(self):
        rows = [
            {'id': 'reject-fast', 'status': 'ok', 'success_rate': .03, 'total_median_ns': 1},
            {'id': 'valid-fast', 'status': 'ok', 'success_rate': .95, 'total_median_ns': 20},
            {'id': 'valid-slow', 'status': 'ok', 'success_rate': 1, 'total_median_ns': 40},
            {'id': 'unknown', 'status': 'ok', 'success_rate': None, 'total_median_ns': .1},
            {'id': 'cimai-native', 'status': 'ok', 'success_rate': None, 'total_median_ns': 30},
        ]
        self.assertEqual('cimai-native', apply_ranking(rows, .90))
        self.assertIsNone(rows[0]['relative_to_baseline'])
        self.assertAlmostEqual(2 / 3, rows[1]['relative_to_baseline'])
        self.assertAlmostEqual(4 / 3, rows[2]['relative_to_baseline'])
        self.assertIsNone(rows[3]['relative_to_baseline'])
        self.assertEqual(1, rows[4]['relative_to_baseline'])
        self.assertTrue(rows[4]['is_baseline'])
        self.assertIsNone(rows[4]['success_rate'])
        self.assertTrue(all(row['baseline_id'] == 'cimai-native' for row in rows))

    def test_final_csv_and_json_use_cimai_and_keep_low_success_in_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            jobs = [{'id': name, 'kind': 'native', 'variant': 'cimai' if name == 'cimai-native' else name}
                    for name in ('reject-fast', 'qualified', 'cimai-native')]
            manifest = {'count': 10, 'bytes': 100, 'largest': {'path': '0'},
                        'files': [{'path': str(i), 'bytes': 10} for i in range(10)]}
            (output / 'run.json').write_text(json.dumps({'matrix': jobs, 'config': {'minimum_success_rate': .9}, 'corpus': {}}))
            (output / 'corpus.manifest.json').write_text(json.dumps(manifest))
            (output / 'status.json').write_text(json.dumps({j['id']: {'status': 'ok'} for j in jobs}))
            for job in jobs:
                folder = output / 'raw' / job['id']
                folder.mkdir(parents=True)
                files = [{'Path': str(i), 'Bytes': 10, 'Success': job['id'] != 'reject-fast' or i == 0,
                          'Nanoseconds': [100, 100, 100], 'NativeAllocated': 0, 'ManagedAllocated': 0}
                         for i in range(10)]
                for mode in ('timing', 'memory'):
                    (folder / f'{mode}.json').write_text(json.dumps({'files': files}))
                elapsed = {'reject-fast': 1, 'qualified': 20, 'cimai-native': 10}[job['id']]
                (folder / 'google.json').write_text(json.dumps({'benchmarks': [
                    {'name': method, 'run_type': 'iteration', 'real_time': elapsed, 'time_unit': 'ms'}
                    for method in ('Corpus', 'LargestFile')]}))
            with patch('report.write_markdown'):
                result = generate_report(output)
            self.assertEqual('cimai-native', result['ranking']['baseline'])
            self.assertEqual(['cimai-native', 'qualified'], [row['id'] for row in result['combinations']])
            self.assertEqual(1, result['ranking']['qualified_count'])
            self.assertEqual(3, len(result['all_combinations']))
            self.assertIsNone(result['combinations'][0]['success_rate'])
            self.assertEqual(1, result['combinations'][0]['relative_to_baseline'])
            self.assertEqual(2, result['combinations'][1]['relative_to_baseline'])
            with (output / 'summary.csv').open(encoding='utf-8-sig', newline='') as stream:
                exported = list(csv.DictReader(stream))
            self.assertEqual(['cimai-native', 'qualified'], [row['id'] for row in exported])
            self.assertEqual(['1.0', '2.0'], [row['relative_to_baseline'] for row in exported])
            self.assertEqual('', exported[0]['success_rate'])
            self.assertNotIn('relative_to_fastest', exported[0])
            with (output / 'all-combinations.csv').open(encoding='utf-8-sig', newline='') as stream:
                diagnostic = list(csv.DictReader(stream))
            self.assertEqual(3, len(diagnostic))
            self.assertEqual('', diagnostic[0]['relative_to_baseline'])
            self.assertEqual('cimai-native', diagnostic[0]['baseline_id'])
            self.assertTrue(all(row['baseline_id'] == 'cimai-native' for row in result['common_success']))

    def test_report_preserves_previous_threshold_unless_explicitly_overridden(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'run.json').write_text(json.dumps({'matrix': [], 'config': {'minimum_success_rate': .9}, 'corpus': {}}))
            (output / 'corpus.manifest.json').write_text(json.dumps({'count': 0, 'files': []}))
            (output / 'status.json').write_text('{}')
            (output / 'summary.json').write_text(json.dumps({'ranking': {'minimum_success_rate': .3}}))
            with patch('report.write_markdown'):
                result = generate_report(output)
                self.assertEqual(.3, result['ranking']['minimum_success_rate'])
                result = generate_report(output, minimum_success_rate=.95)
                self.assertEqual(.95, result['ranking']['minimum_success_rate'])
                result = generate_report(output)
                self.assertEqual(.95, result['ranking']['minimum_success_rate'])

    def test_missing_or_unavailable_cimai_has_no_fabricated_reference(self):
        for status in (None, 'unsupported'):
            with self.subTest(status=status):
                rows = [{'id': 'qualified', 'status': 'ok', 'success_rate': 1, 'total_median_ns': 1}]
                if status is not None:
                    rows.append({'id': 'cimai-native', 'status': status})
                self.assertIsNone(apply_ranking(rows, .9))
                self.assertTrue(all(row['relative_to_baseline'] is None for row in rows))
                self.assertTrue(all(row['baseline_id'] is None for row in rows))

    def test_no_eligible_results_has_no_fabricated_baseline(self):
        rows = [{'id': 'rejected', 'status': 'ok', 'success_rate': .2, 'total_median_ns': 1}]
        self.assertIsNone(apply_ranking(rows, .9))
        self.assertIsNone(rows[0]['relative_to_baseline'])

    def test_threshold_is_inclusive_and_configurable(self):
        rows = [{'id': 'a', 'status': 'ok', 'success_rate': .9, 'total_median_ns': 1},
                {'id': 'cimai-native', 'status': 'ok', 'success_rate': None, 'total_median_ns': 2}]
        self.assertEqual('cimai-native', apply_ranking(rows, .9))
        self.assertEqual(.5, rows[0]['relative_to_baseline'])
        self.assertEqual('cimai-native', apply_ranking(rows, .95))
        self.assertIsNone(rows[0]['relative_to_baseline'])
        self.assertEqual(1, rows[1]['relative_to_baseline'])

if __name__ == '__main__':
    unittest.main()
