"""Google Cloud Storage service for material/rubric file uploads.

Async-safe: blocking GCS calls run in thread pool executor (H-4 fix).
"""

from __future__ import annotations

import asyncio
from functools import partial

from google.cloud import storage
import structlog

from submission_service import config

logger = structlog.get_logger(__name__)

_client: storage.Client | None = None


def _get_client() -> storage.Client:
    global _client
    if _client is None:
        if config.GCS_CREDENTIALS_PATH:
            _client = storage.Client.from_service_account_json(
                config.GCS_CREDENTIALS_PATH
            )
        else:
            _client = storage.Client()
        logger.info("gcs_client_created", bucket=config.GCS_BUCKET)
    return _client


def _upload_sync(blob_path: str, file_content: bytes) -> str:
    """Synchronous upload — called via run_in_executor."""
    client = _get_client()
    bucket = client.bucket(config.GCS_BUCKET)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(file_content)
    return f"gs://{config.GCS_BUCKET}/{blob_path}"


def _download_sync(blob_path: str) -> bytes:
    """Synchronous download — called via run_in_executor."""
    client = _get_client()
    bucket = client.bucket(config.GCS_BUCKET)
    blob = bucket.blob(blob_path)
    return blob.download_as_bytes()


async def upload_file(
    assessment_id: str,
    file_name: str,
    file_content: bytes,
    folder: str = "materials",
) -> str:
    """Upload a file to GCS and return the storage path.

    Path format: gs://assessorflow-materials/{assessment_id}/{folder}/{file_name}
    """
    blob_path = f"{assessment_id}/{folder}/{file_name}"
    loop = asyncio.get_event_loop()
    storage_path = await loop.run_in_executor(
        None, partial(_upload_sync, blob_path, file_content)
    )
    logger.info("file_uploaded", path=storage_path)
    return storage_path


async def download_file(storage_path: str) -> bytes:
    """Download a file from GCS by its full gs:// path."""
    blob_path = storage_path.replace(f"gs://{config.GCS_BUCKET}/", "")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_download_sync, blob_path))
