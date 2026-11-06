#!/usr/bin/env python3
"""SQLD: passive discovery of archived URLs with GET parameters."""
import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit


def suffix_value(value):
    value = value.strip().lower().lstrip('.')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+(?:-[a-z0-9]+)*)*', value):
        raise ValueError('Enter a suffix such as .ae, .sa, or .co.uk')
    return value


def limit_value(value):
    value = int(value)
    if not 1 <= value <= 10000:
        raise ValueError('Count must be between 1 and 10000')
    return value


SECRET = re.compile(r'(password|passwd|secret|token|session|api.?key|authorization|credential)', re.I)
STATIC = re.compile(r'\.(?:jpg|jpeg|png|gif|svg|webp|ico|css|js|woff2?|ttf|pdf|zip|mp[34]|exe)$', re.I)
TRACKING = {'gclid', 'fbclid', 'msclkid', '_ga', 'ref', 'referrer'}


def candidate(raw, suffix, page_type='any'):
    try:
        if len(raw) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in raw):
            return None
        p = urlsplit(raw.strip())
        host = (p.hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
        if p.scheme.lower() not in {'http', 'https'} or p.username is not None or p.password is not None:
            return None
        if not host.endswith('.' + suffix) or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in host.split('.')):
            return None
        port = p.port
        if page_type == 'php' and not unquote(p.path).lower().endswith('.php'):
            return None
        pairs = parse_qsl(p.query, keep_blank_values=True, max_num_fields=200)
        if not pairs or STATIC.search(unquote(p.path)) or any(SECRET.search(k) for k, _ in pairs):
            return None
        names = sorted({k for k, _ in pairs if k and not k.lower().startswith('utm_') and k.lower() not in TRACKING})
        if not names:
            return None
        netloc = host + (f':{port}' if port is not None else '')
        return {'hostname': host, 'url': urlunsplit((p.scheme.lower(), netloc, p.path, p.query, '')), 'parameters': names}
    except (ValueError, UnicodeError):
        return None


def input_urls(path):
    with Path(path).open(encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('{'):
                try:
                    line = json.loads(line).get('url', '')
                except (ValueError, AttributeError):
                    continue
            if isinstance(line, str):
                yield line


def build_query(suffix, limit, crawl, database='ccindex', table='ccindex', page_type='php'):
    suffix = suffix_value(suffix)
    limit = limit_value(limit)
    if not re.fullmatch(r'CC-MAIN-\d{4}-\d{2}', crawl or ''):
        raise ValueError('Provide an explicit crawl, e.g. CC-MAIN-2025-33')
    for name in (database, table):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError('Database/table names must be simple SQL identifiers')
    if page_type not in {'php', 'any'}:
        raise ValueError('Page type must be php or any')
    page_filter = "AND ends_with(lower(url_path), '.php')" if page_type == 'php' else ''
    return f'''SELECT url FROM (
  SELECT url, url_host_name,
         row_number() OVER (PARTITION BY url_host_name ORDER BY url) AS rn
  FROM "{database}"."{table}"
  WHERE crawl = '{crawl}' AND subset = 'warc'
    AND url_host_tld = '{suffix.split('.')[-1]}'
    AND ends_with(lower(url_host_name), '.{suffix}')
    AND url_protocol IN ('http', 'https') AND fetch_status = 200
    AND url_query IS NOT NULL AND url_query <> ''
    {page_filter}
) WHERE rn <= 3
ORDER BY url_host_name, rn
LIMIT {limit * 9}'''


def athena_urls(client, query, output, workgroup, timeout):
    execution = client.start_query_execution(QueryString=query, WorkGroup=workgroup,
        ResultConfiguration={'OutputLocation': output})['QueryExecutionId']
    print(f'Athena query ID: {execution}', flush=True)
    started = time.monotonic()
    try:
        while True:
            result = client.get_query_execution(QueryExecutionId=execution)['QueryExecution']
            status = result['Status']
            state = status['State']
            if state == 'SUCCEEDED':
                break
            if state in {'FAILED', 'CANCELLED'}:
                raise RuntimeError(f"Athena {state}: {status.get('StateChangeReason', 'No reason provided')}")
            if time.monotonic() - started >= timeout:
                raise TimeoutError('Athena timed out; cancellation requested')
            time.sleep(2)
    except BaseException:
        try:
            client.stop_query_execution(QueryExecutionId=execution)
        except Exception:
            print(f'Could not cancel query {execution}; check AWS console.')
        raise
    print(f"Bytes scanned: {result.get('Statistics', {}).get('DataScannedInBytes', 'unknown')}")
    first = True
    for page in client.get_paginator('get_query_results').paginate(QueryExecutionId=execution):
        for row in page['ResultSet']['Rows']:
            if first:
                first = False
                continue
            data = row.get('Data', [])
            if data and data[0].get('VarCharValue'):
                yield data[0]['VarCharValue']


def collect(urls, suffix, limit, page_type='any'):
    found = {}
    for raw in urls:
        item = candidate(raw, suffix, page_type)
        if item and item['hostname'] not in found:
            found[item['hostname']] = item
    return list(found.values())[:limit]


def save(items, output, suffix, limit, source, page_type='any'):
    output.mkdir(parents=True, exist_ok=False)
    for filename, key in [('urls.txt', 'url'), ('sites.txt', 'hostname')]:
        (output / filename).write_text(''.join(x[key] + '\n' for x in items), encoding='utf-8')
    with (output / 'candidates.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['hostname', 'url', 'parameters'])
        writer.writeheader()
        writer.writerows({**x, 'parameters': ','.join(x['parameters'])} for x in items)
    summary = {'project': 'SQLD', 'suffix': '.' + suffix, 'requested': limit, 'returned': len(items),
        'source': source, 'page_type': page_type, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'notice': 'Query parameters do not prove SQL injection. No target sites were contacted.'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['random', 'commoncrawl'], default='random',
        help='random: sample domains without AWS (default); commoncrawl: archived URL candidates')
    parser.add_argument('--refresh', action='store_true', help='Refresh the cached Tranco domain list')
    parser.add_argument('--suffix', help='.ae, .sa, .co.uk, etc.')
    parser.add_argument('--limit', '--count', type=int, help='Interactive default: 1000; random max: 100000; commoncrawl max: 10000')
    parser.add_argument('--input', type=Path, help='Local URL list or JSONL with a url field')
    parser.add_argument('--page-type', choices=['php', 'any'], default='php',
        help='php: pages such as item.php?id=1 (default); any: all page paths with GET parameters')
    parser.add_argument('--crawl', help='Existing Common Crawl partition')
    parser.add_argument('--print-query', action='store_true', help='Print SQL without AWS requests')
    parser.add_argument('--s3-output')
    parser.add_argument('--profile')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--database', default='ccindex')
    parser.add_argument('--table', default='ccindex')
    parser.add_argument('--workgroup', default='primary')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--output', type=Path, help='New directory; existing directories are never overwritten')
    args = parser.parse_args()
    if args.source == 'random':
        if args.crawl or args.print_query or args.s3_output or args.profile:
            parser.error('AWS/query options require --source commoncrawl')
        from random_suffix_sites import main as random_main
        random_args = []
        for flag, value in [('--suffix', args.suffix), ('--count', args.limit),
                            ('--input', args.input), ('--output', args.output)]:
            if value is not None:
                random_args.extend([flag, str(value)])
        if args.refresh:
            random_args.append('--refresh')
        return random_main(random_args)
    if args.refresh:
        parser.error('--refresh is only available with --source random')
    try:
        suffix = suffix_value(args.suffix or input('Suffix (.ae / .sa): '))
        limit = limit_value(args.limit if args.limit is not None else (input('Unique hostnames [1000]: ') or '1000'))
        if args.timeout <= 0:
            raise ValueError('Timeout must be positive')
        output = args.output or Path('results') / datetime.now(timezone.utc).strftime('sqld-%Y%m%d-%H%M%S-%f')
        if args.input and args.print_query:
            raise ValueError('--input and --print-query cannot be combined')
        if args.print_query:
            print(build_query(suffix, limit, args.crawl, args.database, args.table, args.page_type))
            return
        if output.exists():
            raise ValueError('Output directory already exists; choose a new path')
        if args.input:
            urls = input_urls(args.input)
            source = 'local file'
        else:
            query = build_query(suffix, limit, args.crawl, args.database, args.table, args.page_type)
            if not args.s3_output or not re.fullmatch(r's3://[^/]+/.+', args.s3_output):
                raise ValueError('Live collection requires --s3-output s3://BUCKET/PREFIX/')
            try:
                import boto3
            except ImportError:
                raise ValueError('Install AWS support: python -m pip install -r requirements.txt')
            print('AWS Athena/S3 can incur charges. LIMIT does not cap bytes scanned.')
            client = boto3.Session(profile_name=args.profile, region_name=args.region).client('athena')
            urls = athena_urls(client, query, args.s3_output, args.workgroup, args.timeout)
            source = f'Common Crawl {args.crawl}'
        items = collect(urls, suffix, limit, args.page_type)
        save(items, output, suffix, limit, source, args.page_type)
        print(f'Saved {len(items)}/{limit} unique hostnames to {output.resolve()}')
        print('These are review candidates, not confirmed vulnerabilities.')
    except (ValueError, OSError, RuntimeError, EOFError) as exc:
        parser.exit(1, f'Error: {exc}\n')
    except KeyboardInterrupt:
        parser.exit(130, 'Cancelled.\n')


if __name__ == '__main__':
    main()
