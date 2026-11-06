import argparse
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

import random_suffix_sites as random_sites

ROOT = Path(__file__).resolve().parents[1]


class RandomSites(unittest.TestCase):
    def test_named_domain_column(self):
        data = 'GlobalRank,TldRank,Domain,TLD\n1,1,one.ir,ir\n2,1,two.com,com\n'
        self.assertEqual(list(random_sites.iter_rows(data)), ['one.ir', 'two.com'])
        self.assertEqual(list(random_sites.iter_rows('Rank,Domain,Score\n1,one.ir,5\n')), ['one.ir'])

    def test_merge_and_partial_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            one, two = Path(tmp) / 'one.csv', Path(tmp) / 'two.csv'
            one.write_text('1,one.ir\n2,shared.ir\n3,wrongir\n')
            two.write_text('Rank,Domain\n1,shared.ir\n2,two.ir\n3,wrong.ir.com\n')
            with patch.object(random_sites, 'download_source', side_effect=[one, OSError('offline'), two]):
                domains, reports = random_sites.collect_sources(['tranco', 'majestic', 'umbrella'], 'ir')
            self.assertEqual(domains, {'one.ir', 'shared.ir', 'two.ir'})
            self.assertEqual([r['status'] for r in reports], ['ok', 'failed', 'ok'])
            self.assertEqual(reports[-1]['added_unique'], 1)
            with patch.object(random_sites, 'download_source', side_effect=OSError('offline')):
                with self.assertRaisesRegex(ValueError, 'All sources failed'):
                    random_sites.collect_sources(['tranco'], 'ir')

    def test_bad_refresh_preserves_cache(self):
        import io
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'tranco.zip'
            target = cache.parent / random_sites.SOURCES['majestic'][1]
            target.write_text('1,original.ir\n')
            with patch.object(random_sites, 'CACHE', cache), patch.object(
                    random_sites.urllib.request, 'urlopen', return_value=io.BytesIO(b'<html>error</html>')):
                with self.assertRaises(ValueError):
                    random_sites.download_source('majestic', True)
            self.assertEqual(target.read_text(), '1,original.ir\n')

    def test_source_names(self):
        self.assertEqual(len(random_sites.source_names('all')), 4)
        self.assertEqual(random_sites.source_names('majestic,tranco,majestic'), ['majestic', 'tranco'])
        with self.assertRaises(argparse.ArgumentTypeError):
            random_sites.source_names('unknown')

    def test_suffix_validation(self):
        self.assertEqual(random_sites.valid_suffix(' .Co.Uk '), 'co.uk')
        for value in ['..ae', 'ae/evil', '', '-ae', 'a' * 64]:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                random_sites.valid_suffix(value)

    def test_csv_zip_parity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'list.csv'
            path.write_text('1,one.ae\n2,https://TWO.ae/path\n3,bad domain\n', encoding='utf-8')
            zipped = Path(tmp) / 'list.zip'
            with zipfile.ZipFile(zipped, 'w') as archive:
                archive.write(path, 'list.csv')
            self.assertEqual(list(random_sites.read_source(path)), ['one.ae', 'two.ae'])
            self.assertEqual(list(random_sites.read_source(zipped)), ['one.ae', 'two.ae'])

    def test_default_cli_and_standalone(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'domains.txt'
            source.write_text('one.ae\none.ae\nhttps://TWO.ae/x\nnotae\nx.ae.evil.com\nthree.sa\n')
            for script in ['sqld.py', 'random_suffix_sites.py']:
                output = Path(tmp) / script
                command = [sys.executable, str(ROOT / script), '--suffix', '.ae',
                           '--count', '1000', '--input', str(source), '--output', str(output)]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(set((output / 'domains.txt').read_text().splitlines()), {'one.ae', 'two.ae'})
                summary = json.loads((output / 'summary.json').read_text())
                self.assertEqual(summary['returned'], 2)
                self.assertEqual(summary['available_matching_domains'], 2)
                again = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(again.returncode, 0)
                self.assertEqual(json.loads((output / 'summary.json').read_text()), summary)

    def test_commoncrawl_query_still_available(self):
        result = subprocess.run([sys.executable, str(ROOT / 'sqld.py'), '--source', 'commoncrawl',
            '--suffix', '.ae', '--limit', '10', '--crawl', 'CC-MAIN-2025-33', '--print-query'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LIMIT 90', result.stdout)


if __name__ == '__main__':
    unittest.main()
