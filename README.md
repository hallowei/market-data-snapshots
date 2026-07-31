# Market Data Snapshots

Public, read-only JSON mirror of the Market Data Service. The production server
publishes it every day at approximately 07:45 Asia/Shanghai; the GitHub workflow
is retained for manual recovery only. Git history provides an audit trail.

## Stable URLs

- Metadata:
  `https://raw.githubusercontent.com/hallowei/market-data-snapshots/main/metadata.json`
- All assets:
  `https://raw.githubusercontent.com/hallowei/market-data-snapshots/main/latest/all-assets.json`
- Asset snapshot:
  `https://raw.githubusercontent.com/hallowei/market-data-snapshots/main/latest/{asset_id}.json`
- Historical analogs:
  `https://raw.githubusercontent.com/hallowei/market-data-snapshots/main/analogs/{asset_id}.json`

Supported asset IDs: `nasdaq100`, `sp500`, `btc`, `nikkei225`, `kospi`,
`csi300`, `wti`, and `gold`.

For an atomic multi-file read, first request
`https://api.github.com/repos/hallowei/market-data-snapshots/commits/main`,
take its `sha`, and replace `main` in every Raw URL with that commit SHA. Verify
each downloaded file against the SHA-256 recorded in `metadata.json`.

## Safety contract

The publisher validates all eight assets before writing any files. A run fails
without committing when:

- any asset is stale;
- an expected asset or required quality field is missing;
- calculation versions differ;
- a partial snapshot does not explain its missing fields or warnings;
- the source does not return standard JSON.

The mirror contains no API keys, environment files, server credentials,
internal logs, administrative API details, or portfolio data.

Market statistics are descriptive only. They are not investment advice,
recommendations, forecasts, or trading instructions.
