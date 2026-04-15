"""Database repository for af_submission tables.

15 tables covering the entire assessment lifecycle.
Source of truth: schema.md Section 2.
All multi-step operations use asyncpg transactions (C-4 fix).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from submission_service.db.pool import get_pool

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# assessment_configs
# ---------------------------------------------------------------------------

async def create_assessment_config(
    assessor_id: str,
    assessment_title: str,
    purpose: str,
    duration_minutes: int,
    difficulty_level: str,
    structured_question_count: int = 0,
    non_structured_question_count: int = 0,
    deadline: str | None = None,
    web_research_mode: str = "manual",
) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO assessment_configs
            (assessor_id, assessment_title, purpose, duration_minutes,
             difficulty_level, structured_question_count, non_structured_question_count,
             deadline, web_research_mode)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING *
        """,
        UUID(assessor_id),
        assessment_title,
        purpose,
        duration_minutes,
        difficulty_level,
        structured_question_count,
        non_structured_question_count,
        datetime.fromisoformat(deadline) if deadline else None,
        web_research_mode,
    )
    return _row_to_dict(row)


async def get_assessment_config(assessment_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM assessment_configs WHERE id = $1",
        UUID(assessment_id),
    )
    return _row_to_dict(row) if row else None


async def get_assessment_config_by_workflow(workflow_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM assessment_configs WHERE workflow_id = $1",
        workflow_id,
    )
    return _row_to_dict(row) if row else None


async def update_assessment_status(assessment_id: str, status: str, workflow_id: str | None = None) -> None:
    pool = await get_pool()
    if workflow_id:
        await pool.execute(
            "UPDATE assessment_configs SET status = $1, workflow_id = $2, updated_at = now() WHERE id = $3",
            status, workflow_id, UUID(assessment_id),
        )
    else:
        await pool.execute(
            "UPDATE assessment_configs SET status = $1, updated_at = now() WHERE id = $2",
            status, UUID(assessment_id),
        )


# ---------------------------------------------------------------------------
# assessment_participants
# ---------------------------------------------------------------------------

async def add_participants(assessment_id: str, emails: list[str]) -> list[dict[str, Any]]:
    pool = await get_pool()
    results = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for email in emails:
                row = await conn.fetchrow(
                    """
                    INSERT INTO assessment_participants (assessment_id, email)
                    VALUES ($1, $2)
                    ON CONFLICT (assessment_id, email) DO NOTHING
                    RETURNING *
                    """,
                    UUID(assessment_id), email,
                )
                if row:
                    results.append(_row_to_dict(row))
    return results


async def get_participants(assessment_id: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM assessment_participants WHERE assessment_id = $1",
        UUID(assessment_id),
    )
    return [_row_to_dict(r) for r in rows]


async def update_participant_invitation_status(participant_id: str, status: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE assessment_participants SET invitation_status = $1 WHERE id = $2",
        status, UUID(participant_id),
    )


# ---------------------------------------------------------------------------
# participant_groups + members
# ---------------------------------------------------------------------------

async def create_group(assessment_id: str, group_name: str) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO participant_groups (assessment_id, group_name)
        VALUES ($1, $2)
        ON CONFLICT (assessment_id, group_name) DO UPDATE SET group_name = EXCLUDED.group_name
        RETURNING *
        """,
        UUID(assessment_id), group_name,
    )
    return _row_to_dict(row)


async def add_group_member(group_id: str, participant_id: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO participant_group_members (group_id, participant_id)
        VALUES ($1, $2)
        ON CONFLICT (group_id, participant_id) DO NOTHING
        """,
        UUID(group_id), UUID(participant_id),
    )


# ---------------------------------------------------------------------------
# assessment_materials
# ---------------------------------------------------------------------------

async def add_material(
    assessment_id: str,
    file_name: str,
    storage_path: str,
    file_type: str,
    source: str = "upload",
    source_url: str | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO assessment_materials
            (assessment_id, file_name, storage_path, file_type, source, source_url)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        UUID(assessment_id), file_name, storage_path, file_type, source, source_url,
    )
    return _row_to_dict(row)


async def get_materials(assessment_id: str, unvalidated_only: bool = False) -> list[dict[str, Any]]:
    pool = await get_pool()
    if unvalidated_only:
        rows = await pool.fetch(
            "SELECT * FROM assessment_materials WHERE assessment_id = $1 AND readiness_status IS NULL",
            UUID(assessment_id),
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM assessment_materials WHERE assessment_id = $1",
            UUID(assessment_id),
        )
    return [_row_to_dict(r) for r in rows]


async def update_material_validation(
    material_id: str, readiness_status: str,
    reason_code: str | None = None, message: str | None = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE assessment_materials
        SET readiness_status = $1, validation_reason_code = $2, validation_message = $3
        WHERE id = $4
        """,
        readiness_status, reason_code, message, UUID(material_id),
    )


# ---------------------------------------------------------------------------
# assessment_rubrics
# ---------------------------------------------------------------------------

async def add_rubric(
    assessment_id: str, file_name: str, storage_path: str, file_type: str,
) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO assessment_rubrics (assessment_id, file_name, storage_path, file_type)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        UUID(assessment_id), file_name, storage_path, file_type,
    )
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# question_sets
# ---------------------------------------------------------------------------

async def create_question_set(workflow_id: str) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO question_sets (workflow_id) VALUES ($1) RETURNING *",
        workflow_id,
    )
    return _row_to_dict(row)


async def increment_question_set_iteration(question_set_id: str) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE question_sets
        SET iteration_count = iteration_count + 1, status = 'generated', updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        UUID(question_set_id),
    )
    return _row_to_dict(row)


async def update_question_set_status(question_set_id: str, status: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE question_sets SET status = $1, updated_at = now() WHERE id = $2",
        status, UUID(question_set_id),
    )


# ---------------------------------------------------------------------------
# generated_questions
# ---------------------------------------------------------------------------

async def write_generated_questions(
    question_set_id: str, questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pool = await get_pool()
    results = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for q in questions:
                row = await conn.fetchrow(
                    """
                    INSERT INTO generated_questions
                        (question_set_id, iteration, question_type, content,
                         structured_answer, non_structured_model_answer, metadata, topic_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                    RETURNING *
                    """,
                    UUID(question_set_id),
                    q.get("iteration", 1),
                    q["question_type"],
                    q["content"],
                    q.get("structured_answer"),
                    q.get("non_structured_model_answer"),
                    json.dumps(q.get("metadata")) if q.get("metadata") else None,
                    UUID(q["topic_id"]) if q.get("topic_id") else None,
                )
                results.append(_row_to_dict(row))
    return results


async def get_generated_questions_with_answers(question_set_id: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM generated_questions
        WHERE question_set_id = $1 AND was_approved IS NULL
        ORDER BY created_at
        """,
        UUID(question_set_id),
    )
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# approved_question_sets + approved_questions
# ---------------------------------------------------------------------------

async def approve_questions(
    assessment_id: str, question_set_id: str, kept_question_ids: list[str],
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Mark questions as approved/rejected
            await conn.execute(
                "UPDATE generated_questions SET was_approved = true WHERE id = ANY($1::uuid[])",
                [UUID(qid) for qid in kept_question_ids],
            )
            await conn.execute(
                """
                UPDATE generated_questions SET was_approved = false
                WHERE question_set_id = $1 AND was_approved IS NULL
                """,
                UUID(question_set_id),
            )

            # Create approved_question_sets
            aqs_row = await conn.fetchrow(
                """
                INSERT INTO approved_question_sets
                    (assessment_id, original_question_set_id, approved_at)
                VALUES ($1, $2, now())
                RETURNING *
                """,
                UUID(assessment_id), UUID(question_set_id),
            )
            aqs_id = aqs_row["id"]

            # Copy kept questions to approved_questions
            kept = await conn.fetch(
                "SELECT * FROM generated_questions WHERE id = ANY($1::uuid[]) ORDER BY created_at",
                [UUID(qid) for qid in kept_question_ids],
            )
            for i, q in enumerate(kept):
                await conn.execute(
                    """
                    INSERT INTO approved_questions
                        (question_set_id, question_type, content, structured_answer,
                         non_structured_model_answer, metadata, sort_order)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                    """,
                    aqs_id,
                    q["question_type"],
                    q["content"],
                    q["structured_answer"],
                    q["non_structured_model_answer"],
                    json.dumps(q["metadata"]) if q["metadata"] else None,
                    i + 1,
                )

            # Update question_set status
            await conn.execute(
                "UPDATE question_sets SET status = $1, updated_at = now() WHERE id = $2",
                "approved", UUID(question_set_id),
            )

    return _row_to_dict(aqs_row)


async def get_approved_questions_with_answers(assessment_id: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT aq.* FROM approved_questions aq
        JOIN approved_question_sets aqs ON aq.question_set_id = aqs.id
        WHERE aqs.assessment_id = $1
        ORDER BY aq.sort_order
        """,
        UUID(assessment_id),
    )
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# participant_submissions + answers
# ---------------------------------------------------------------------------

async def create_submission(assessment_id: str, participant_id: str) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO participant_submissions (assessment_id, participant_id, started_at)
        VALUES ($1, $2, now())
        ON CONFLICT (assessment_id, participant_id) DO UPDATE SET started_at = now()
        RETURNING *
        """,
        UUID(assessment_id), UUID(participant_id),
    )
    return _row_to_dict(row)


async def submit_answers(
    submission_id: str, answers: list[dict[str, Any]],
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for a in answers:
                await conn.execute(
                    """
                    INSERT INTO participant_answers (submission_id, question_id, answer_content)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (submission_id, question_id) DO UPDATE SET answer_content = EXCLUDED.answer_content
                    """,
                    UUID(submission_id), UUID(a["question_id"]), a.get("answer_content", ""),
                )
            await conn.execute(
                "UPDATE participant_submissions SET status = 'submitted', submitted_at = now() WHERE id = $1",
                UUID(submission_id),
            )


# ---------------------------------------------------------------------------
# evaluations + evaluation_details
# ---------------------------------------------------------------------------

async def create_evaluation(
    workflow_id: str, participant_id: str, submission_id: str,
) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO evaluations (workflow_id, participant_id, submission_id, status)
        VALUES ($1, $2, $3, 'in_progress')
        RETURNING *
        """,
        workflow_id, UUID(participant_id), UUID(submission_id),
    )
    return _row_to_dict(row)


async def write_evaluation_details(
    evaluation_id: str, details: list[dict[str, Any]],
    total_score: float, max_score: float,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for d in details:
                await conn.execute(
                    """
                    INSERT INTO evaluation_details
                        (evaluation_id, question_id, group_evaluation_id,
                         score, max_score, reasoning, evaluation_method)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    UUID(evaluation_id),
                    UUID(d["question_id"]),
                    UUID(d["group_evaluation_id"]) if d.get("group_evaluation_id") else None,
                    d["score"],
                    d["max_score"],
                    d.get("reasoning"),
                    d["evaluation_method"],
                )
            await conn.execute(
                """
                UPDATE evaluations
                SET total_score = $1, max_score = $2, status = 'completed'
                WHERE id = $3
                """,
                total_score, max_score, UUID(evaluation_id),
            )


async def get_evaluation(workflow_id: str, participant_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT e.*, json_agg(ed.*) AS details
        FROM evaluations e
        LEFT JOIN evaluation_details ed ON ed.evaluation_id = e.id
        WHERE e.workflow_id = $1 AND e.participant_id = $2
        GROUP BY e.id
        """,
        workflow_id, UUID(participant_id),
    )
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# group_evaluations
# ---------------------------------------------------------------------------

async def create_group_evaluation(
    workflow_id: str, group_id: str, question_id: str,
    group_score: float, max_score: float, reasoning: str | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO group_evaluations
            (workflow_id, group_id, question_id, group_score, max_score, reasoning)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (group_id, question_id) DO UPDATE
            SET group_score = EXCLUDED.group_score, max_score = EXCLUDED.max_score
        RETURNING *
        """,
        workflow_id, UUID(group_id), UUID(question_id),
        group_score, max_score, reasoning,
    )
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# participant_reports
# ---------------------------------------------------------------------------

async def create_report(
    workflow_id: str, participant_id: str, evaluation_id: str,
    report_content: dict[str, Any],
) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO participant_reports
            (workflow_id, participant_id, evaluation_id, report_content, status, generated_at)
        VALUES ($1, $2, $3, $4::jsonb, 'completed', now())
        RETURNING *
        """,
        workflow_id, UUID(participant_id), UUID(evaluation_id),
        json.dumps(report_content),
    )
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: asyncpg.Record | None) -> dict[str, Any]:
    """Convert asyncpg Record to dict with JSON-serializable values."""
    if row is None:
        return {}
    result = {}
    for key, value in dict(row).items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result
