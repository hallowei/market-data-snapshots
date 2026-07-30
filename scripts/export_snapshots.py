#!/usr/bin/env python3
"""Export validated, read-only market snapshots for a public GitHub mirror."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ASSET_IDS = (
    "nasdaq100",
    "sp500",
    "btc",
    "nikkei225",
    "kospi",
    "csi300",
    "wti",
    "gold",
)
EXPORT_SCHEMA_VERSION = "1.0"
USER_AGENT = "market-data-snapshot-exporter/1.0"
MAX_FETCH_ATTEMPTS = 4


class SnapshotExportError(RuntimeError):
    """Raised when a source response is unsafe to publish."""


def fetch_json(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    payload: Any = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get_content_type()
                if content_type != "application/json":
                    raise SnapshotExportError(
                        f"{path}: expected application/json, got {content_type}"
                    )
                payload = json.load(response)
            break
        except HTTPError as exc:
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt == MAX_FETCH_ATTEMPTS:
                raise SnapshotExportError(f"{path}: HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            if attempt == MAX_FETCH_ATTEMPTS:
                raise SnapshotExportError(f"{path}: source connection failed") from exc
        time.sleep(2 ** (attempt - 1))
    if not isinstance(payload, dict):
        raise SnapshotExportError(f"{path}: expected a JSON object")
    return payload


def validate_data_quality(asset_id: str, quality: Any) -> dict[str, Any]:
    if not isinstance(quality, dict):
        raise SnapshotExportError(f"{asset_id}: data_quality must be an object")
    for field in ("status", "stale", "missing_fields", "warnings"):
        if field not in quality:
            raise SnapshotExportError(f"{asset_id}: data_quality.{field} is required")
    if not isinstance(quality["stale"], bool):
        raise SnapshotExportError(f"{asset_id}: data_quality.stale must be boolean")
    if quality["stale"]:
        raise SnapshotExportError(f"{asset_id}: stale snapshots are not published")
    if not isinstance(quality["missing_fields"], list):
        raise SnapshotExportError(f"{asset_id}: data_quality.missing_fields must be a list")
    if not isinstance(quality["warnings"], list):
        raise SnapshotExportError(f"{asset_id}: data_quality.warnings must be a list")
    if quality["status"] == "partial" and not (
        quality["missing_fields"] or quality["warnings"]
    ):
        raise SnapshotExportError(f"{asset_id}: partial status requires an explicit reason")
    return quality


def validate_asset(asset_id: str, payload: dict[str, Any]) -> None:
    if payload.get("asset_id") != asset_id:
        raise SnapshotExportError(f"{asset_id}: mismatched asset_id")
    for field in ("as_of", "calculation_version", "upside_quality"):
        if not payload.get(field):
            raise SnapshotExportError(f"{asset_id}: {field} is required")
    if not isinstance(payload["upside_quality"], dict):
        raise SnapshotExportError(f"{asset_id}: upside_quality must be an object")
    validate_data_quality(asset_id, payload.get("data_quality"))


def validate_analogs(
    asset_id: str,
    payload: dict[str, Any],
    calculation_version: str,
) -> None:
    if payload.get("asset_id") != asset_id:
        raise SnapshotExportError(f"{asset_id} analogs: mismatched asset_id")
    if payload.get("calculation_version") != calculation_version:
        raise SnapshotExportError(f"{asset_id} analogs: calculation_version mismatch")
    for field in ("as_of", "status", "similar_dates", "missing_state_fields"):
        if field not in payload:
            raise SnapshotExportError(f"{asset_id} analogs: {field} is required")
    if not isinstance(payload["similar_dates"], list):
        raise SnapshotExportError(f"{asset_id} analogs: similar_dates must be a list")
    if not isinstance(payload["missing_state_fields"], list):
        raise SnapshotExportError(f"{asset_id} analogs: missing_state_fields must be a list")


def source_as_of(payload: dict[str, Any]) -> str:
    value = payload.get("last_session") or payload.get("as_of")
    if not isinstance(value, str) or not value:
        raise SnapshotExportError("source_as_of is unavailable")
    return value[:10]


def json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def export_snapshots(
    *,
    base_url: str,
    source_api: str,
    output_dir: Path,
    timeout: float = 30.0,
    fetcher: Callable[[str, str, float], dict[str, Any]] = fetch_json,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).isoformat()
    all_assets = fetcher(base_url, "/v1/assets/latest", timeout)
    summaries = all_assets.get("assets")
    if not isinstance(summaries, list):
        raise SnapshotExportError("all-assets: assets must be a list")
    summary_ids = {item.get("asset_id") for item in summaries if isinstance(item, dict)}
    if summary_ids != set(ASSET_IDS) or all_assets.get("asset_count") != len(ASSET_IDS):
        raise SnapshotExportError("all-assets: expected exactly the registered 8 assets")

    version = all_assets.get("calculation_version")
    if not isinstance(version, str) or not version:
        raise SnapshotExportError("all-assets: calculation_version is required")
    for summary in summaries:
        asset_id = summary.get("asset_id", "unknown")
        validate_data_quality(asset_id, summary.get("data_quality"))

    documents: dict[str, dict[str, Any]] = {}
    asset_metadata: dict[str, dict[str, Any]] = {}
    for asset_id in ASSET_IDS:
        asset = fetcher(base_url, f"/v1/assets/{asset_id}/latest", timeout)
        validate_asset(asset_id, asset)
        if asset["calculation_version"] != version:
            raise SnapshotExportError(f"{asset_id}: calculation_version mismatch")

        analogs = fetcher(base_url, f"/v1/assets/{asset_id}/analogs", timeout)
        validate_analogs(asset_id, analogs, version)

        as_of = source_as_of(asset)
        quality = asset["data_quality"]
        common = {
            "generated_at": generated_at,
            "source_api": source_api,
            "source_as_of": as_of,
        }
        documents[f"latest/{asset_id}.json"] = {**common, **asset}
        documents[f"analogs/{asset_id}.json"] = {
            **common,
            "data_quality": quality,
            **analogs,
        }
        asset_metadata[asset_id] = {
            "source_as_of": as_of,
            "calculation_version": version,
            "data_quality": quality,
        }

    latest_source_as_of = max(item["source_as_of"] for item in asset_metadata.values())
    documents["latest/all-assets.json"] = {
        "generated_at": generated_at,
        "source_api": source_api,
        "source_as_of": latest_source_as_of,
        **all_assets,
    }
    encoded_documents = {path: json_bytes(payload) for path, payload in documents.items()}
    metadata = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "source_api": source_api,
        "source_as_of": latest_source_as_of,
        "calculation_version": version,
        "asset_count": len(ASSET_IDS),
        "assets": asset_metadata,
        "files": {
            path: {"sha256": sha256_hex(content), "bytes": len(content)}
            for path, content in sorted(encoded_documents.items())
        },
    }
    encoded_documents["metadata.json"] = json_bytes(metadata)

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-export-", dir=output_dir) as temp_name:
        staging = Path(temp_name)
        for relative_path, content in encoded_documents.items():
            target = staging / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for relative_path in encoded_documents:
            source = staging / relative_path
            target = output_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)

    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="https://market-data.expensesnap.online",
        help="API base URL used for fetching",
    )
    parser.add_argument(
        "--source-api",
        default="market-data.expensesnap.online",
        help="Public source identifier written into snapshots",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata = export_snapshots(
        base_url=args.base_url,
        source_api=args.source_api,
        output_dir=args.output_dir,
        timeout=args.timeout,
    )
    print(
        json.dumps(
            {
                "status": "exported",
                "generated_at": metadata["generated_at"],
                "calculation_version": metadata["calculation_version"],
                "asset_count": metadata["asset_count"],
                "file_count": len(metadata["files"]) + 1,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
