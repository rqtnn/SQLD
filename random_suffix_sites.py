#!/usr/bin/env python3
"""Randomly sample known domains matching a suffix (no target contact).

Default source: Tranco's public top-1-million domains list, downloaded once
and cached locally. This is NOT a comprehensive index of Internet domains.
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


def iter_rows(content):
    """Accept Tranco rank,domain CSV or a simple one-domain-per-line list."""
    for row in csv.reader(io.StringIO(content)):
        if not row:
            continue
        candidate = row[1] if len(row) >= 2 and row[0].strip().isdigit() else row[0]
        domain = clean_domain(candidate)
        if domain:
            yield domain


def read_source(path):
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.namelist() if m.lower().endswith(('.csv', '.txt'))]
            if not members:
                raise ValueError('ZIP needs a .csv or .txt domain list')
            with archive.open(members[0]) as raw:
                text = io.TextIOWrapper(raw, encoding='utf-8-sig', errors='replace')
                # Stream to avoid keeping the whole file in memory.
                for row in csv.reader(text):
                    if not row:
                        continue
                    entry = row[1] if len(row) >= 2 and row[0].strip().isdigit() else row[0]
                    domain = clean_domain(entry)
                    if domain:
                        yield domain
    else:
        with path.open('r', encoding='utf-8-sig', errors='replace') as raw:
            for row in csv.reader(raw):
                if not row:
                    continue
                entry = row[1] if len(row) >= 2 and row[0].strip().isdigit() else row[0]
                domain = clean_domain(entry)
                if domain:
                    yield domain


def get_default_source(refresh):
    if CACHE.exists() and not refresh:
        print(f'Using saved Tranco list: {CACHE}')
        return CACHE
    print('Downloading public Tranco list (one million domains)...', flush=True)
    req = urllib.request.Request(TRANCO_URL, headers={'User-Agent': 'random-suffix-sites/1.0'})
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE.with_suffix('.download')
    try:
        with urllib.request.urlopen(req, timeout=90) as response, temporary.open('wb') as output:
            import shutil
            shutil.copyfileobj(response, output)
        with zipfile.ZipFile(temporary) as z:
            if z.testzip() is not None:
                raise ValueError('Downloaded archive is corrupt')
        temporary.replace(CACHE)
    finally:
        temporary.unlink(missing_ok=True)
    return CACHE


def main(argv=None):
    p = argparse.ArgumentParser(description='Random domains matching a suffix; no AWS, no scanning.')
    p.add_argument('--suffix', type=valid_suffix, help='.ae / .sa / .co.uk')
    p.add_argument('--count', '--limit', type=int, help='How many domains to sample (default 1000)')
    p.add_argument('--input', type=Path, help='Optional local .txt, .csv, or .zip domain list')
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
        src = args.input or get_default_source(args.refresh)
        if not src.is_file():
            p.error(f'Input file does not exist: {src}')
        matching = set()
        for domain in read_source(src):
            if domain.endswith('.' + args.suffix):
                matching.add(domain)
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
            'source': str(src),
            'source_reference': TRANCO_URL if not args.input else None,
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
