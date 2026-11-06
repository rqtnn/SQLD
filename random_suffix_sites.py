#!/usr/bin/env python3
"""Randomly sample known domains matching a suffix (no target contact).

Sources: Tranco, Majestic, Cisco Umbrella, and Open Page Rank, cached locally. This is NOT a comprehensive index of Internet domains.
"""

import argparse
import csv
import io
import json
import re
import secrets
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

TRANCO_URL = 'https://tranco-list.eu/top-1m.csv.zip'
CACHE = Path.home() / '.cache' / 'random-suffix-sites' / 'tranco-top-1m.csv.zip'
DOMAIN_LABEL = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')


def valid_suffix(s):
    s = s.strip().lower().removeprefix('.')
    try:
        s = s.encode('idna').decode('ascii')
    except UnicodeError:
        raise argparse.ArgumentTypeError('Use a suffix like .ae, .sa, or .co.uk')
    if not s or len(s) > 253 or not all(DOMAIN_LABEL.fullmatch(x) for x in s.split('.')):
        raise argparse.ArgumentTypeError('Use a suffix like .ae, .sa, or .co.uk')
    return s


def clean_domain(value):
    value = value.strip().lower().lstrip('\ufeff')
    if not value or value.startswith('#'):
        return None
    if value.startswith(('https://', 'http://')):
        try:
            value = urlsplit(value).hostname or ''
        except ValueError:
            return None
    value = value.rstrip('.')
    try:
        value = value.encode('idna').decode('ascii')
    except UnicodeError:
        return None
    if len(value) > 253 or not all(DOMAIN_LABEL.fullmatch(x) for x in value.split('.')):
        return None
    return value


SOURCES = {
    'tranco': ('https://tranco-list.eu/top-1m.csv.zip', 'tranco-top-1m.csv.zip'),
    'majestic': ('https://downloads.majestic.com/majestic_million.csv', 'majestic.csv'),
    'umbrella': ('https://s3-us-west-1.amazonaws.com/umbrella-static/top-1m.csv.zip', 'umbrella.zip'),
    'openpagerank': ('https://openpagerank.keywordseverywhere.com/downloads/top10milliondomains.csv.zip', 'openpagerank.zip'),
}


def source_names(value):
    names = list(SOURCES) if value == 'all' else list(dict.fromkeys(value.split(',')))
    if not names or any(name not in SOURCES for name in names):
        raise argparse.ArgumentTypeError('Use all or comma-separated tranco,majestic,umbrella,openpagerank')
    return names


def parse_rows(rows):
    column = None
    for row in rows:
        if not row:
            continue
        labels = [cell.strip().lower().lstrip('\ufeff') for cell in row]
        if 'domain' in labels:
            column = labels.index('domain')
            continue
        index = column if column is not None else (1 if len(row) >= 2 and row[0].strip().isdigit() else 0)
        if index < len(row):
            domain = clean_domain(row[index])
            if domain and '.' in domain:
                yield domain


def iter_rows(content):
    yield from parse_rows(csv.reader(io.StringIO(content)))


def read_source(path):
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.namelist() if m.lower().endswith(('.csv', '.txt'))]
            if not members:
                raise ValueError('ZIP needs a .csv or .txt domain list')
            with archive.open(members[0]) as raw:
                yield from parse_rows(csv.reader(io.TextIOWrapper(raw, encoding='utf-8-sig', errors='replace')))
    else:
        with path.open(encoding='utf-8-sig', errors='replace') as raw:
            yield from parse_rows(csv.reader(raw))


def download_source(name, refresh=False):
    import tempfile
    url, filename = SOURCES[name]
    path = CACHE.parent / filename
    if path.exists() and not refresh:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f'Downloading {name}...', flush=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'SQLD/2.0'})
    # Preserve the format extension so the same parser validates downloads.
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        with urllib.request.urlopen(request, timeout=45) as response, temporary.open('wb') as output:
            import shutil
            shutil.copyfileobj(response, output)
        # Reject HTML/error pages and empty lists before replacing a valid cache.
        if next(read_source(temporary), None) is None:
            raise ValueError(f'{name} did not return a domain list')
        if path.suffix == '.zip':
            with zipfile.ZipFile(temporary) as archive:
                if archive.testzip() is not None:
                    raise ValueError(f'{name} archive is corrupt')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def collect_sources(names, suffix, refresh=False):
    matching, reports = set(), []
    for name in names:
        report = {'name': name, 'url': SOURCES[name][0]}
        try:
            path = download_source(name, refresh)
            found = {d for d in read_source(path) if d.endswith('.' + suffix)}
            added = len(found - matching)
            matching.update(found)
            report.update(status='ok', matching=len(found), added_unique=added, cache=str(path))
            print(f'{name}: {len(found)} matching; {added} new unique domains', flush=True)
        except (OSError, urllib.error.URLError, zipfile.BadZipFile, ValueError, csv.Error) as exc:
            report.update(status='failed', error=str(exc))
            print(f'Warning: {name} failed: {exc}', file=sys.stderr, flush=True)
        reports.append(report)
    if not any(r['status'] == 'ok' for r in reports):
        raise ValueError('All sources failed; check your connection or use --input with a local list')
    return matching, reports


def main(argv=None):
    p = argparse.ArgumentParser(description='Random domains matching a suffix; no AWS, no scanning.')
    p.add_argument('--suffix', type=valid_suffix, help='.ae / .sa / .co.uk')
    p.add_argument('--count', '--limit', type=int, help='How many domains to sample (default 1000)')
    p.add_argument('--input', type=Path, help='Optional local .txt, .csv, or .zip domain list')
    p.add_argument('--sources', type=source_names, default=list(SOURCES), help='all (default) or comma-separated source names')
    p.add_argument('--refresh', action='store_true', help='Re-download Tranco list')
    p.add_argument('--output', type=Path, help='Optional new output directory')
    args = p.parse_args(argv)
    try:
        if args.suffix is None:
            args.suffix = valid_suffix(input('Suffix (.ae / .sa / .co.uk): '))
        if args.count is None:
            args.count = int(input('Number of domains [1000]: ') or '1000')
    except (ValueError, argparse.ArgumentTypeError, EOFError) as e:
        p.exit(2, f'Error: {e}\n')
    except KeyboardInterrupt:
        p.exit(130, 'Cancelled.\n')
    if not 1 <= args.count <= 100000:
        p.error('--count must be between 1 and 100000')
    try:
        if args.output and args.output.exists():
            raise ValueError('Output directory already exists; choose a new path')
        if args.input:
            matching = {d for d in read_source(args.input) if d.endswith('.' + args.suffix)}
            reports = [{'name': 'local', 'status': 'ok', 'path': str(args.input), 'matching': len(matching)}]
        else:
            matching, reports = collect_sources(args.sources, args.suffix, args.refresh)
        pool = sorted(matching)
        selected = secrets.SystemRandom().sample(pool, k=min(args.count, len(pool)))
        now = datetime.now(timezone.utc)
        folder = args.output or Path('results') / f'suffix-{args.suffix.replace(".", "-")}-{now:%Y%m%d-%H%M%S-%f}'
        folder.mkdir(parents=True, exist_ok=False)
        (folder / 'domains.txt').write_text(''.join(d + '\n' for d in selected), encoding='utf-8')
        # HTTPS strings are formatting only: the program does not test whether sites respond.
        (folder / 'urls.txt').write_text(''.join('https://' + d + '\n' for d in selected), encoding='utf-8')
        summary = {
            'suffix': '.' + args.suffix,
            'requested': args.count,
            'available_matching_domains': len(pool),
            'returned': len(selected),
            'source': str(args.input) if args.input else 'public domain lists',
            'sources': reports,
            'partial_results': any(r['status'] == 'failed' for r in reports),
            'created_utc': now.isoformat(),
            'note': 'Random from source, not all Internet domains. No reachability or vulnerability testing performed.',
        }
        (folder / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(f'Matched {len(pool)} unique domains ending in .{args.suffix}')
        print(f'Saved {len(selected)} randomized domains to: {folder.resolve()}')
        if len(selected) < args.count:
            print('Note: source had fewer matching domains than requested; no entries were fabricated.')
    except (OSError, urllib.error.URLError, zipfile.BadZipFile, ValueError, csv.Error) as e:
        p.exit(1, f'Error: {e}\n')
    except KeyboardInterrupt:
        p.exit(130, 'Cancelled.\n')


if __name__ == '__main__':
    main()
