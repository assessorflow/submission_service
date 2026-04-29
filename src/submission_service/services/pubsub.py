"""Google Cloud Pub/Sub publisher for workflow events.

Publishes to topics defined in pubsub.md.
All messages use the standard envelope format.
Async-safe: blocking publish runs in thread pool executor (H-3 fix).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from google.cloud import pubsub_v1
import structlog

from submission_service import config

logger = structlog.get_logger(__name__)

_publisher: pubsub_v1.PublisherClient | None = None


def _get_publisher() -> pubsub_v1.PublisherClient:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
        logger.info("pubsub_publisher_created", project=config.GCP_PROJECT_ID)
    return _publisher


def _topic_path(topic_name: str) -> str:
    return f"projects/{config.GCP_PROJECT_ID}/topics/{topic_name}"


def _build_envelope(
    event_type: str,
    workflow_id: str,
    correlation_id: str,
    payload: dict,
) -> dict:
    """Build standard Pub/Sub message envelope per pubsub.md §5.1."""
    return {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "event_type": event_type,
        "workflow_id": workflow_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_agent": "assessment-submission-service",
        "correlation_id": correlation_id,
        "payload": payload,
    }


async def publish_workflow_start(
    workflow_id: str,
    assessment_id: str,
    correlation_id: str | None = None,
) -> str:
    """Publish assessorflow.workflow.start event."""
    corr_id = correlation_id or f"corr_{uuid.uuid4().hex[:12]}"
    envelope = _build_envelope(
        event_type="assessorflow.workflow.start",
        workflow_id=workflow_id,
        correlation_id=corr_id,
        payload={"assessment_id": assessment_id},
    )
    return await _publish_async("assessorflow.workflow.start", envelope)


async def publish_human_review_approved(
    workflow_id: str,
    assessment_id: str,
    approved_question_set_id: str,
    correlation_id: str,
) -> str:
    envelope = _build_envelope(
        event_type="assessorflow.human-review.approved",
        workflow_id=workflow_id,
        correlation_id=correlation_id,
        payload={
            "assessment_id": assessment_id,
            "approved_question_set_id": approved_question_set_id,
        },
    )
    return await _publish_async("assessorflow.human-review.approved", envelope)


async def publish_invitation_sent(
    workflow_id: str,
    assessment_id: str,
    participant_ids: list[str],
    correlation_id: str,
) -> str:
    envelope = _build_envelope(
        event_type="assessorflow.invitation.sent",
        workflow_id=workflow_id,
        correlation_id=correlation_id,
        payload={
            "assessment_id": assessment_id,
            "participant_ids": participant_ids,
        },
    )
    return await _publish_async("assessorflow.invitation.sent", envelope)


async def publish_submission_completed(
    workflow_id: str,
    assessment_id: str,
    participant_id: str,
    submission_id: str,
    correlation_id: str,
) -> str:
    envelope = _build_envelope(
        event_type="assessorflow.participant.submission-completed",
        workflow_id=workflow_id,
        correlation_id=correlation_id,
        payload={
            "assessment_id": assessment_id,
            "participant_id": participant_id,
            "submission_id": submission_id,
        },
    )
    return await _publish_async(
        "assessorflow.participant.submission-completed", envelope
    )


async def _publish_async(topic_name: str, envelope: dict, max_retries: int = 3) -> str:
    """Publish a message to Pub/Sub with retry and without blocking the event loop."""
    publisher = _get_publisher()
    topic = _topic_path(topic_name)
    data = json.dumps(envelope).encode("utf-8")
    loop = asyncio.get_event_loop()

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            future = publisher.publish(topic, data)
            message_id = await loop.run_in_executor(None, future.result)
            logger.info(
                "pubsub_published",
                topic=topic_name,
                event_id=envelope["event_id"],
                message_id=message_id,
            )
            return message_id
        except Exception as e:
            last_error = e
            logger.warn(
                "pubsub_publish_retry",
                topic=topic_name,
                attempt=attempt,
                error=str(e),
            )
            if attempt < max_retries:
                await asyncio.sleep(2**attempt)  # exponential backoff: 2s, 4s, 8s

    logger.error(
        "pubsub_publish_failed",
        topic=topic_name,
        event_id=envelope["event_id"],
        error=str(last_error),
    )
    raise last_error
