import argparse
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import random_suffix_sites as random_sites

ROOT = Path(__file__).resolve().parents[1]


class RandomSites(unittest.TestCase):
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
