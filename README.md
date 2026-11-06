# SQLD — random sites by domain suffix

Extract a random sample of known domains ending in a suffix such as `.ae`, `.sa`, or `.co.uk`. The default mode merges four public domain lists: **no AWS account, API keys, pip dependencies, or target requests**. Python 3.10+.

## Quick start

```bash
git clone https://github.com/rqtnn/SQLD.git
cd SQLD
python3 sqld.py
```

Enter the suffix and the count when prompted. Or provide both directly:

```bash
python3 sqld.py --suffix .ae --count 1000
python3 sqld.py --suffix .co.uk --count 500 --refresh
python3 sqld.py --suffix .sa --count 1000 --input my-domains.txt
```

`--limit` is an alias for `--count`. Random mode accepts 1–100000.
The standalone attached-script entry point is also available:

```bash
python3 random_suffix_sites.py --suffix .ae --count 1000
```

## Domain sources

All four sources run by default, with duplicate hostnames removed across lists:

| Name for --sources | Provider | List size |
| --- | --- | --- |
| tranco | [Tranco](https://tranco-list.eu/) | Top 1 million |
| majestic | [Majestic Million](https://majestic.com/reports/majestic-million) | Top 1 million |
| umbrella | [Cisco Umbrella](https://s3-us-west-1.amazonaws.com/umbrella-static/index.html) | Top 1 million domains/subdomains |
| openpagerank | [Open Page Rank](https://keywordseverywhere.com/openpagerank/top-domains) | Top 10 million |

Majestic data is licensed under [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/); source attribution is retained in the summary. Other lists remain subject to their providers' terms.

```bash
python3 sqld.py --suffix .ir --count 100000 --sources all
python3 sqld.py --suffix .ir --count 100000 --sources tranco,majestic,umbrella
```

First run downloads can be large, especially Majestic and Open Page Rank. Downloads are cached in `~/.cache/random-suffix-sites/`; use `--refresh` to download them again. Sources run sequentially and print their matching count and how many new unique hostnames they add. A failed source produces a visible warning; successful sources still produce results, with `partial_results: true` and per-source errors in `summary.json`. If all sources fail, the command fails. Caches are replaced only after validation; a failed refresh preserves the earlier file but does not silently use it for that run.

A local `--input` uses only that file and works offline. It accepts TXT, CSV with a Domain column or rank/domain rows, and ZIP containing TXT/CSV. URL inputs are reduced to hostnames. Subdomains count separately. Neither popularity lists nor their union guarantee coverage of all registered domains.

Results are saved in a new `results/suffix-*` folder:

- `domains.txt`: sampled domains
- `urls.txt`: the same domains formatted with `https://`
- `summary.json`: requested, available, and returned counts plus source and timestamp

Use `--output NEW_DIRECTORY` for a custom location. Existing output directories are never overwritten.

**Coverage:** this is a sample from the supplied list, not an index of all registered domains. If only 200 matching domains exist in the list, requesting 1000 returns 200. Domains are not checked for reachability, PHP, query parameters, or vulnerabilities; HTTPS is formatting only. A suffix does not prove a site's location.

## Existing Common Crawl mode

The original archived-URL workflow remains available using `--source commoncrawl`. Its AWS dependencies and setup only apply to this mode.

#### Archived URL discovery

Search Common Crawl for archived PHP pages with GET query parameters by hostname suffix, such as `.ae`, `.sa`, or `.co.uk`. For example, selecting `.ae` finds patterns such as `https://shop.example.ae/item.php?id=123` or `https://blog.example.ae/search.php?q=hello`. Default: 1,000 different hostnames; maximum: 10,000. One URL is saved per hostname. Subdomains count separately.

The default `--page-type php` requires a path ending in `.php` plus a useful query parameter (`id`, `q`, `category`, or other names). Use `--page-type any` to also include paths such as `/product?id=123`, `.aspx`, and `.asp`. Neither a PHP extension nor an `id` parameter is evidence of a vulnerability.

Automatic search uses AWS Athena and requires the one-time setup below. Running with `--input` only processes your existing file; it does not search the web.

SQLD prepares candidates for manual review. Query parameters do not establish SQL injection, a database, or current availability. SQLD does not contact target websites, send attack payloads, or scan vulnerabilities. Only scan assets you own or have permission to test. A suffix does not establish a website's country or hosting location.

### Quick start (Python 3.10+)

No dependencies or accounts needed for a local file:

```bash
python3 sqld.py --source commoncrawl --input example-urls.txt
```

Enter `.ae`, then press Enter for 1,000. The included fictional sample returns only two eligible hostnames. SQLD never invents entries to meet the count.

```bash
python3 sqld.py --source commoncrawl --suffix .sa --limit 1000 --input my-urls.txt
```

Input accepts one URL per line or JSONL objects containing `url`. Filters exclude tracking-only queries, common static files, embedded credentials, and query field names suggesting secrets or sessions. This is a best-effort filter, not a comprehensive secret detector. Fragments are removed; query values are preserved. Local files use their original order when choosing one URL per hostname.

Results go in a new timestamped `results/sqld-*` directory:

- `urls.txt`: URLs for later review
- `sites.txt`: distinct hostnames
- `candidates.csv`: hostname, URL, parameter names
- `summary.json`: source, requested count, actual count, and timestamp

Use `--output NEW_DIRECTORY` to choose the location. Existing directories are never overwritten.

### Collect directly from Common Crawl

The bulk source is the Common Crawl URL Index through AWS Athena. The tool does not scrape search engines or use the CDX API to enumerate a whole suffix.

One-time setup:

1. Configure an AWS account and credentials (for example, a named AWS CLI profile). Do not put credentials in this project.
2. In `us-east-1`, create the `ccindex` database/table with the official flat schema and run `MSCK REPAIR TABLE ccindex` using the official instructions below.
3. Configure Athena/Glue permissions, read access to Common Crawl data, and an S3 location for query results.
4. Set a bytes-scanned cutoff in your Athena workgroup to control spending. **Athena and S3 can cost money. SQL LIMIT does not cap bytes scanned.**
5. Select a crawl that actually exists in your table's partitions. The example below is historical, not a claim about the newest crawl.

```bash
python3 -m pip install -r requirements.txt
python3 sqld.py --source commoncrawl --suffix .ae --limit 1000 --crawl CC-MAIN-2025-33 --s3-output s3://YOUR-BUCKET/athena-results/ --profile YOUR-PROFILE
```

Omit `--suffix` and `--limit` for interactive prompts. Use `--database`, `--table`, or `--workgroup` for custom settings. Queries time out after 600 seconds by default (`--timeout` changes this); interruption and polling failures request cancellation. Check the printed query ID in AWS if cancellation fails.

Preview the exact SQL without contacting AWS or installing boto3:

```bash
python3 sqld.py --source commoncrawl --suffix .ae --limit 1000 --crawl CC-MAIN-2025-33 --print-query
```

SQL requests at most three archived URLs per hostname and up to nine times the requested URL count before local filtering. It may return fewer eligible hostnames than requested, especially if sampled URLs are filtered out. No retry queries run automatically. Archive fetch status 200 describes the time of archival, not the live website today. No vulnerability ranking or probability is assigned.

### Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use fictional URLs and mocked Athena responses. Live AWS collection is not tested during development.

### Official setup references

- [Common Crawl URL Index and Athena setup](https://commoncrawl.org/url-index)
- [Official flat table schema](https://github.com/commoncrawl/cc-index-table/blob/master/src/sql/athena/cc-index-create-table-flat.sql)

