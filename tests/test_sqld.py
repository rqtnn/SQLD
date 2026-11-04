import unittest
from unittest.mock import patch
import sqld


class Filters(unittest.TestCase):
    def test_php_discovery_filter(self):
        urls = ['https://shop.example.ae/item.php?id=1',
                'https://shop.example.ae/item.php?id=2',
                'https://blog.example.ae/SEARCH.PHP?q=test',
                'https://other.example.ae/item?id=3',
                'https://wrong.example.sa/item.php?id=4',
                'https://tracking.example.ae/item.php?utm_source=test',
                'https://path.example.ae/item.php/extra?id=1']
        rows = sqld.collect(urls, 'ae', 1000, 'php')
        self.assertEqual([r['hostname'] for r in rows], ['shop.example.ae', 'blog.example.ae'])
        self.assertEqual(len(sqld.collect(urls, 'ae', 1000, 'any')), 4)
        self.assertIn("ends_with(lower(url_path), '.php')", sqld.build_query('ae', 1000, 'CC-MAIN-2025-33'))
        self.assertNotIn("ends_with(lower(url_path), '.php')", sqld.build_query('ae', 1000, 'CC-MAIN-2025-33', page_type='any'))

    def test_suffix_boundary(self):
        self.assertIsNone(sqld.candidate('https://fakeae/?id=1', 'ae'))
        self.assertIsNone(sqld.candidate('https://example.ae.evil.com/?id=1', 'ae'))
        self.assertIsNotNone(sqld.candidate('https://shop.example.co.uk/?id=1', 'co.uk'))

    def test_exclusions(self):
        for url in ['https://x.ae/x.png?id=1', 'https://x.ae/?token=x',
                    'https://x.ae/?utm_source=x', 'https://u:p@x.ae/?id=1',
                    'ftp://x.ae/?id=1', 'https://x.ae:bad/?id=1']:
            with self.subTest(url=url):
                self.assertIsNone(sqld.candidate(url, 'ae'))

    def test_normalization_and_dedup(self):
        urls = ['https://SHOP.example.ae/?id=1#top', 'https://shop.example.ae/?q=2',
                'https://blog.example.ae/?q=3']
        rows = sqld.collect(urls, 'ae', 1000)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['url'], 'https://shop.example.ae/?id=1')

    def test_limits_and_injection(self):
        self.assertEqual(sqld.limit_value(10000), 10000)
        for n in [0, 10001]:
            with self.assertRaises(ValueError):
                sqld.limit_value(n)
        with self.assertRaises(ValueError):
            sqld.build_query("ae' OR true", 1000, 'CC-MAIN-2025-33')
        self.assertIn('LIMIT 9000', sqld.build_query('ae', 1000, 'CC-MAIN-2025-33'))


class FakeAthena:
    def __init__(self, state='SUCCEEDED'):
        self.state = state
        self.stopped = False

    def start_query_execution(self, **kwargs):
        return {'QueryExecutionId': 'fake'}

    def get_query_execution(self, **kwargs):
        return {'QueryExecution': {'Status': {'State': self.state}}}

    def stop_query_execution(self, **kwargs):
        self.stopped = True

    def get_paginator(self, name):
        return self

    def paginate(self, **kwargs):
        for values in [['url', 'https://one.ae/?id=1'], ['https://two.ae/?q=2']]:
            yield {'ResultSet': {'Rows': [{'Data': [{'VarCharValue': v}]} for v in values]}}


class Athena(unittest.TestCase):
    def test_pagination(self):
        self.assertEqual(len(list(sqld.athena_urls(FakeAthena(), 'sql', 's3://b/p/', 'primary', 10))), 2)

    def test_failure_and_cancel(self):
        for state in ['FAILED', 'CANCELLED']:
            client = FakeAthena(state)
            with self.assertRaises(RuntimeError):
                list(sqld.athena_urls(client, 'sql', 's3://b/p/', 'primary', 10))
            self.assertTrue(client.stopped)

    def test_timeout_cancels(self):
        client = FakeAthena('RUNNING')
        with patch('sqld.time.monotonic', side_effect=[0, 2]):
            with self.assertRaises(TimeoutError):
                list(sqld.athena_urls(client, 'sql', 's3://b/p/', 'primary', 1))
        self.assertTrue(client.stopped)


if __name__ == '__main__':
    unittest.main()
