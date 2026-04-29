"""External REST API routes — assessor/participant facing.

Endpoints matching api_contract.md Section 2:
- Assessor endpoints (JWT auth): create, list, get, materials, rubrics, start,
  review, approve, invite, reports, status, topics
- Participant endpoints (token auth): access, questions, submit, report
- Combined launch endpoint for frontend convenience
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, field_validator
import structlog

from submission_service.db import repository as repo
from submission_service.services import storage, pubsub
from submission_service.services.auth import UserContext, get_current_user

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

VALID_PURPOSES = {"topic_revision", "exam_prep", "skill_assessment"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}


class CreateAssessmentRequest(BaseModel):
    assessment_title: str
    purpose: str
    duration_minutes: int
    difficulty_level: str
    structured_question_count: int = 0
    non_structured_question_count: int = 0
    deadline: str | None = None
    web_research_mode: str = "manual"
    participants: list[str] = []
    groups: list[dict[str, Any]] = []

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, v):
        if v not in VALID_PURPOSES:
            raise ValueError(f"Invalid purpose. Must be one of: {VALID_PURPOSES}")
        return v

    @field_validator("difficulty_level")
    @classmethod
    def validate_difficulty(cls, v):
        if v not in VALID_DIFFICULTIES:
            raise ValueError(
                f"Invalid difficulty_level. Must be one of: {VALID_DIFFICULTIES}"
            )
        return v

    @field_validator("web_research_mode")
    @classmethod
    def validate_web_research_mode(cls, v):
        if v not in {"manual", "auto"}:
            raise ValueError("web_research_mode must be 'manual' or 'auto'")
        return v


class ApproveQuestionsRequest(BaseModel):
    removed_question_ids: list[str] = []


class DistributeRequest(BaseModel):
    participant_ids: list[str] = []  # empty = all participants


# ---------------------------------------------------------------------------
# 1. POST /api/v1/assessments — Create assessment config
# ---------------------------------------------------------------------------


@router.post("/assessments")
async def create_assessment(
    req: CreateAssessmentRequest, user: UserContext = Depends(get_current_user)
):
    """Create a new assessment configuration."""
    logger.info(
        "create_assessment", title=req.assessment_title, assessor_id=user.user_id
    )

    config = await repo.create_assessment_config(
        assessor_id=user.user_id,
        assessment_title=req.assessment_title,
        purpose=req.purpose,
        duration_minutes=req.duration_minutes,
        difficulty_level=req.difficulty_level,
        structured_question_count=req.structured_question_count,
        non_structured_question_count=req.non_structured_question_count,
        deadline=req.deadline,
        web_research_mode=req.web_research_mode,
    )
    assessment_id = config["id"]

    # Add participants if provided
    if req.participants:
        await repo.add_participants(assessment_id, req.participants)

    # Create groups if provided
    for group in req.groups:
        g = await repo.create_group(assessment_id, group["group_name"])
        for member_email in group.get("members", []):
            # Find participant by email
            participants = await repo.get_participants(assessment_id)
            participant = next(
                (p for p in participants if p["email"] == member_email), None
            )
            if participant:
                await repo.add_group_member(g["id"], participant["id"])

    return {"assessment": config, "status": "created"}


# ---------------------------------------------------------------------------
# 1b. GET /api/v1/assessments — List assessments (assessor dashboard)
# ---------------------------------------------------------------------------


@router.get("/assessments")
async def list_assessments(
    status: str | None = None,
    page: int = 1,
    page_size: int = 10,
    user: UserContext = Depends(get_current_user),
):
    """List assessments for the current assessor with pagination.

    Per api_contract.md Section 2.1.3.
    """
    items, total = await repo.list_assessments(
        assessor_id=user.user_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# 2. GET /api/v1/assessments/:id — Get assessment details
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}")
async def get_assessment(
    assessment_id: str, user: UserContext = Depends(get_current_user)
):
    """Get assessment details including participants and materials."""
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    participants = await repo.get_participants(assessment_id)
    materials = await repo.get_materials(assessment_id)

    return {
        "assessment": config,
        "participants": participants,
        "materials": materials,
    }


# ---------------------------------------------------------------------------
# 2c. GET /api/v1/assessments/:id/status — Workflow status polling
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/status")
async def get_assessment_status(
    assessment_id: str, user: UserContext = Depends(get_current_user)
):
    """Get assessment workflow status for frontend polling.

    Per api_contract.md Section 2.8.1.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    return {
        "assessment_id": assessment_id,
        "status": config["status"],
        "workflow_id": config.get("workflow_id"),
        "updated_at": config.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# 2b. GET /api/v1/assessments/:id/materials — Get materials for an assessment
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/materials")
async def get_materials(
    assessment_id: str,
    source: str | None = None,
    user: UserContext = Depends(get_current_user),
):
    """Get materials for an assessment. Used by Validator Agent to fetch file metadata."""
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    materials = await repo.get_materials(assessment_id)

    # Filter by source if specified (e.g. web_research)
    if source:
        materials = [m for m in materials if m.get("source") == source]

    return materials


# ---------------------------------------------------------------------------
# 3. POST /api/v1/assessments/:id/materials — Upload material file
# ---------------------------------------------------------------------------


@router.post("/assessments/{assessment_id}/materials")
async def upload_material(
    assessment_id: str,
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
):
    """Upload a learning material file to Cloud Storage."""
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    content = await file.read()
    storage_path = await storage.upload_file(
        assessment_id=assessment_id,
        file_name=file.filename,
        file_content=content,
        folder="materials",
    )

    # Extract file extension (schema.md expects: pdf, docx, png, jpg — not full MIME type)
    ext = (
        file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "unknown"
    )

    material = await repo.add_material(
        assessment_id=assessment_id,
        file_name=file.filename,
        storage_path=storage_path,
        file_type=ext,
        source="upload",
    )

    return {"material": material, "status": "uploaded"}


# ---------------------------------------------------------------------------
# 4. POST /api/v1/assessments/:id/rubrics — Upload rubric file
# ---------------------------------------------------------------------------


@router.post("/assessments/{assessment_id}/rubrics")
async def upload_rubric(
    assessment_id: str,
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
):
    """Upload a marking rubric file to Cloud Storage."""
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    content = await file.read()
    storage_path = await storage.upload_file(
        assessment_id=assessment_id,
        file_name=file.filename,
        file_content=content,
        folder="rubrics",
    )

    ext = (
        file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "unknown"
    )

    rubric = await repo.add_rubric(
        assessment_id=assessment_id,
        file_name=file.filename,
        storage_path=storage_path,
        file_type=ext,
    )

    return {"rubric": rubric, "status": "uploaded"}


# ---------------------------------------------------------------------------
# 4b. GET /api/v1/assessments/:id/rubrics — List rubrics
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/rubrics")
async def list_rubrics(
    assessment_id: str, user: UserContext = Depends(get_current_user)
):
    """List uploaded rubrics for an assessment. Per api_contract.md Section 2.2b.2."""
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    rubrics = await repo.get_rubrics(assessment_id)
    return {"items": rubrics}


# ---------------------------------------------------------------------------
# 4c. GET /api/v1/assessments/:id/materials/validation-status — Validation polling
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/materials/validation-status")
async def get_validation_status(
    assessment_id: str, user: UserContext = Depends(get_current_user)
):
    """Get validation status for all materials. Frontend polls this during Phase 3.

    Per api_contract.md Section 2.2.4.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    materials = await repo.get_materials(assessment_id)
    results = {}
    failed_count = 0
    pending_count = 0
    for m in materials:
        status = m.get("readiness_status")
        results[m["id"]] = {
            "file_name": m["file_name"],
            "readiness_status": status,
            "validation_reason_code": m.get("validation_reason_code"),
            "validation_message": m.get("validation_message"),
        }
        if status == "TERMINATE":
            failed_count += 1
        elif status is None:
            pending_count += 1

    all_passed = failed_count == 0 and pending_count == 0 and len(materials) > 0
    overall = "complete" if pending_count == 0 else "in_progress"

    return {
        "status": overall,
        "results": results,
        "all_passed": all_passed,
        "failed_count": failed_count,
    }


# ---------------------------------------------------------------------------
# 5. POST /api/v1/assessments/:id/start — Start workflow
# ---------------------------------------------------------------------------


@router.post("/assessments/{assessment_id}/start")
async def start_workflow(
    assessment_id: str, user: UserContext = Depends(get_current_user)
):
    """Start the assessment workflow — publishes workflow.start to Pub/Sub."""
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")
    if config["status"] != "draft":
        raise HTTPException(
            400, f"Cannot start workflow — status is '{config['status']}'"
        )

    # Generate workflow_id
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    correlation_id = f"corr_{uuid.uuid4().hex[:12]}"

    # Update assessment with workflow_id and status
    await repo.update_assessment_status(
        assessment_id, "material_validation", workflow_id=workflow_id
    )

    # Publish to Pub/Sub
    await pubsub.publish_workflow_start(
        workflow_id=workflow_id,
        assessment_id=assessment_id,
        correlation_id=correlation_id,
    )

    logger.info(
        "workflow_started", assessment_id=assessment_id, workflow_id=workflow_id
    )

    return {
        "workflow_id": workflow_id,
        "assessment_id": assessment_id,
        "status": "material_validation",
        "correlation_id": correlation_id,
    }


# ---------------------------------------------------------------------------
# 6. GET /api/v1/assessments/:id/generated-questions — Get draft questions
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/generated-questions")
async def get_generated_questions(
    assessment_id: str,
    question_set_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Get generated (draft) questions for assessor review (Phase 8).

    Returns questions without answers — assessor reviews question quality only.
    Per api_contract.md Section 2.3.3.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    questions = await repo.get_generated_questions_with_answers(question_set_id)
    if not questions:
        raise HTTPException(404, "No generated questions found for this question set")

    # Strip answers — assessor sees questions only, not answers
    for q in questions:
        q.pop("structured_answer", None)
        q.pop("non_structured_model_answer", None)

    return {
        "question_set_id": question_set_id,
        "questions": questions,
        "count": len(questions),
    }


# ---------------------------------------------------------------------------
# 6b. GET /api/v1/assessments/:id/approved-questions — Get approved questions
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/approved-questions")
async def get_approved_questions(
    assessment_id: str, user: UserContext = Depends(get_current_user)
):
    """Get approved questions (post-HITL). Per api_contract.md Section 2.4.3.

    Strips answers — assessor views the approved question list only.
    """
    questions = await repo.get_approved_questions_with_answers(assessment_id)
    if not questions:
        raise HTTPException(404, "No approved questions found")

    # Strip answers
    for q in questions:
        q.pop("non_structured_model_answer", None)
        q.pop("structured_answer", None)
        meta = q.get("metadata")
        if meta:
            # Handle double-encoded JSON from DB
            while isinstance(meta, str):
                meta = json.loads(meta)
            q["metadata"] = meta
            meta.pop("option_explanations", None)
            meta.pop("source_chunk_ids", None)

    return {"questions": questions, "count": len(questions)}


# ---------------------------------------------------------------------------
# 7. POST /api/v1/assessments/:id/review/approve — HITL question approval
# ---------------------------------------------------------------------------


@router.post("/assessments/{assessment_id}/review/approve")
async def approve_questions(
    assessment_id: str,
    req: ApproveQuestionsRequest,
    user: UserContext = Depends(get_current_user),
):
    """Assessor approves/rejects generated questions (Phase 8 HITL).

    Per api_contract.md Section 2.4.2: accepts removed_question_ids.
    All other questions are automatically approved.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    # Find the latest question_set for this assessment via workflow_id
    workflow_id = config.get("workflow_id")
    if not workflow_id:
        raise HTTPException(
            400, "Assessment has no workflow — cannot approve questions"
        )

    question_set = await repo.get_question_set_by_workflow(workflow_id)
    if not question_set:
        raise HTTPException(404, "No question set found for this workflow")

    question_set_id = question_set["id"]

    # Get all generated question IDs, derive kept from removed
    all_questions = await repo.get_generated_questions_with_answers(question_set_id)
    all_ids = {q["id"] for q in all_questions}
    removed_ids = set(req.removed_question_ids)
    kept_question_ids = [qid for qid in all_ids if qid not in removed_ids]

    if not kept_question_ids:
        raise HTTPException(
            400, "Cannot remove all questions — at least one must be kept"
        )

    result = await repo.approve_questions(
        assessment_id=assessment_id,
        question_set_id=question_set_id,
        kept_question_ids=kept_question_ids,
    )

    # Update status
    await repo.update_assessment_status(assessment_id, "ready_for_distribution")

    # Publish HITL approval event
    if workflow_id:
        await pubsub.publish_human_review_approved(
            workflow_id=workflow_id,
            assessment_id=assessment_id,
            approved_question_set_id=str(result["id"]),
            correlation_id=f"corr_{uuid.uuid4().hex[:12]}",
        )

    return {
        "approved_question_set_id": str(result["id"]),
        "original_question_set_id": question_set_id,
        "questions_approved": len(kept_question_ids),
        "questions_removed": len(removed_ids),
        "approved_at": result.get("approved_at"),
    }


# ---------------------------------------------------------------------------
# 8. POST /api/v1/assessments/:id/invite — Send invitations
# ---------------------------------------------------------------------------


@router.post("/assessments/{assessment_id}/invite")
async def send_invitations(
    assessment_id: str,
    req: DistributeRequest,
    user: UserContext = Depends(get_current_user),
):
    """Send assessment invitations to selected participants.

    Per api_contract.md Section 2.4.5.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    participants = await repo.get_participants(assessment_id)

    # Filter to selected participants if specified
    if req.participant_ids:
        participants = [p for p in participants if p["id"] in req.participant_ids]

    # Update invitation status
    sent = []
    for p in participants:
        await repo.update_participant_invitation_status(p["id"], "sent")
        sent.append(p["email"])

    # Update assessment status
    await repo.update_assessment_status(assessment_id, "assessment_active")

    # Publish invitation.sent → Orchestrator
    # Orchestrator then publishes email.request.assessment-link → Email Service
    if config.get("workflow_id"):
        await pubsub.publish_invitation_sent(
            workflow_id=config["workflow_id"],
            assessment_id=assessment_id,
            participant_ids=[p["id"] for p in participants],
            correlation_id=f"corr_{uuid.uuid4().hex[:12]}",
        )

    return {"sent_to": sent, "count": len(sent), "status": "assessment_active"}


# ---------------------------------------------------------------------------
# 9. POST /api/v1/assessments/:id/reports/distribute — Send reports
# ---------------------------------------------------------------------------


@router.post("/assessments/{assessment_id}/reports/distribute")
async def distribute_reports(
    assessment_id: str, user: UserContext = Depends(get_current_user)
):
    """Trigger report distribution to participants.

    This endpoint marks reports for distribution. The Orchestrator
    publishes email.request.participant-report → Email Service.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    participants = await repo.get_participants(assessment_id)
    emails = [p["email"] for p in participants]

    # TODO: Orchestrator handles email publishing via assessorflow.email.request.participant-report

    return {"sent_to": emails, "count": len(emails)}


# ---------------------------------------------------------------------------
# 10. POST /api/v1/assessments/launch — Combined create + upload + start
# ---------------------------------------------------------------------------


@router.post("/assessments/launch")
async def launch_assessment(
    config: str = Form(...),
    materials: list[UploadFile] = File(default=[]),
    rubric: UploadFile | None = File(default=None),
    user: UserContext = Depends(get_current_user),
):
    """One-shot endpoint for the frontend 'Launch Assessment Workflow' button.

    Accepts multipart/form-data with:
    - config: JSON string with assessment configuration + participants + groups
    - materials: One or more material files (PDF, DOCX, TXT, images)
    - rubric: Optional marking rubric file (PDF, DOCX)

    Does everything in one call:
    1. Creates assessment config
    2. Adds participants
    3. Creates groups
    4. Uploads material files to GCS
    5. Uploads rubric file to GCS (if provided)
    6. Starts workflow (publishes Pub/Sub event)
    """
    # Parse config JSON
    try:
        cfg = json.loads(config)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON in 'config' field")

    # Validate required fields
    required = ["assessment_title", "purpose", "duration_minutes", "difficulty_level"]
    for field in required:
        if field not in cfg:
            raise HTTPException(400, f"Missing required field: {field}")

    if not materials:
        raise HTTPException(400, "At least one material file is required")

    logger.info(
        "launch_assessment", title=cfg["assessment_title"], assessor_id=user.user_id
    )

    # 1. Create assessment config (assessor_id from JWT, not body)
    assessment = await repo.create_assessment_config(
        assessor_id=user.user_id,
        assessment_title=cfg["assessment_title"],
        purpose=cfg["purpose"],
        duration_minutes=cfg["duration_minutes"],
        difficulty_level=cfg["difficulty_level"],
        structured_question_count=cfg.get("structured_question_count", 0),
        non_structured_question_count=cfg.get("non_structured_question_count", 0),
        deadline=cfg.get("deadline"),
        web_research_mode=cfg.get("web_research_mode", "manual"),
    )
    assessment_id = assessment["id"]

    # 2. Add participants
    participants_added = []
    if cfg.get("participants"):
        participants_added = await repo.add_participants(
            assessment_id, cfg["participants"]
        )

    # 3. Create groups
    groups_created = []
    for group in cfg.get("groups", []):
        g = await repo.create_group(assessment_id, group["group_name"])
        groups_created.append(g)
        for member_email in group.get("members", []):
            all_participants = await repo.get_participants(assessment_id)
            participant = next(
                (p for p in all_participants if p["email"] == member_email), None
            )
            if participant:
                await repo.add_group_member(g["id"], participant["id"])

    # 4. Upload material files to GCS
    materials_uploaded = []
    for file in materials:
        content = await file.read()
        storage_path = await storage.upload_file(
            assessment_id=assessment_id,
            file_name=file.filename,
            file_content=content,
            folder="materials",
        )
        material = await repo.add_material(
            assessment_id=assessment_id,
            file_name=file.filename,
            storage_path=storage_path,
            file_type=file.filename.rsplit(".", 1)[-1].lower()
            if "." in file.filename
            else "unknown",
            source="upload",
        )
        materials_uploaded.append(material)

    # 5. Upload rubric file to GCS (optional)
    rubric_uploaded = None
    if rubric:
        content = await rubric.read()
        storage_path = await storage.upload_file(
            assessment_id=assessment_id,
            file_name=rubric.filename,
            file_content=content,
            folder="rubrics",
        )
        rubric_uploaded = await repo.add_rubric(
            assessment_id=assessment_id,
            file_name=rubric.filename,
            storage_path=storage_path,
            file_type=rubric.filename.rsplit(".", 1)[-1].lower()
            if "." in rubric.filename
            else "unknown",
        )

    # 6. Start workflow
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    correlation_id = f"corr_{uuid.uuid4().hex[:12]}"

    await repo.update_assessment_status(
        assessment_id, "material_validation", workflow_id=workflow_id
    )

    await pubsub.publish_workflow_start(
        workflow_id=workflow_id,
        assessment_id=assessment_id,
        correlation_id=correlation_id,
    )

    logger.info(
        "assessment_launched",
        assessment_id=assessment_id,
        workflow_id=workflow_id,
        materials=len(materials_uploaded),
        participants=len(participants_added),
    )

    return {
        "assessment": assessment,
        "workflow_id": workflow_id,
        "correlation_id": correlation_id,
        "materials_uploaded": len(materials_uploaded),
        "rubric_uploaded": rubric_uploaded is not None,
        "participants_added": len(participants_added),
        "groups_created": len(groups_created),
        "status": "material_validation",
    }


# ===========================================================================
# Participant-facing endpoints (token-based auth, no JWT)
# Per api_contract.md Section 2.5
# ===========================================================================


class SubmitAnswersRequest(BaseModel):
    answers: list[dict[str, Any]]  # [{"question_id": "uuid", "answer_content": "..."}]


# ---------------------------------------------------------------------------
# 11. GET /api/v1/participate/:id — Participant accesses assessment via signed link
# ---------------------------------------------------------------------------


@router.get("/participate/{assessment_id}")
async def access_assessment(assessment_id: str, token: str):
    """Participant clicks assessment link from email.

    Token encodes participant_id + assessment_id + expiry.
    Per api_contract.md Section 2.5.1.
    """
    # TODO: validate signed token (verify signature, expiry, extract participant_id)
    # For now, extract participant_id from token as placeholder
    participant_id = _extract_participant_from_token(token)
    if not participant_id:
        raise HTTPException(400, "Invalid or expired token")

    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")
    if config["status"] != "assessment_active":
        raise HTTPException(403, "Assessment is not active")

    # Check deadline
    if config.get("deadline"):
        from datetime import datetime, timezone

        deadline = (
            config["deadline"]
            if isinstance(config["deadline"], str)
            else config["deadline"].isoformat()
        )
        if datetime.now(timezone.utc).isoformat() > deadline:
            raise HTTPException(403, "Assessment deadline has passed")

    # Create submission record (idempotent via ON CONFLICT)
    await repo.create_submission(assessment_id, participant_id)

    # Update invitation status
    await repo.update_participant_invitation_status(participant_id, "accepted")

    # TODO: create participant:{email} session in Redis with TTL

    participants = await repo.get_participants(assessment_id)
    participant = next((p for p in participants if p["id"] == participant_id), None)

    return {
        "assessment_id": assessment_id,
        "assessment_title": config.get("assessment_title"),
        "purpose": config.get("purpose"),
        "participant_id": participant_id,
        "participant_email": participant["email"] if participant else None,
        "duration_minutes": config.get("duration_minutes"),
        "deadline": config.get("deadline"),
        "structured_question_count": config.get("structured_question_count"),
        "non_structured_question_count": config.get("non_structured_question_count"),
        "total_questions": (
            config.get("structured_question_count", 0)
            + config.get("non_structured_question_count", 0)
        ),
        "status": "ready",
    }


# ---------------------------------------------------------------------------
# 12. GET /api/v1/participate/:id/questions — Participant gets questions
# ---------------------------------------------------------------------------


@router.get("/participate/{assessment_id}/questions")
async def get_participant_questions(assessment_id: str, token: str):
    """Get approved questions for participant to answer (no answers shown).

    Per api_contract.md Section 2.5.2.
    """
    participant_id = _extract_participant_from_token(token)
    if not participant_id:
        raise HTTPException(400, "Invalid or expired token")

    questions = await repo.get_approved_questions_with_answers(assessment_id)
    if not questions:
        raise HTTPException(404, "No questions found")

    # Strip all answers and internal metadata
    clean_questions = []
    for q in questions:
        clean_q = {
            "id": q["id"],
            "question_type": q["question_type"],
            "content": q["content"],
            "sort_order": q.get("sort_order"),
        }
        # Include MCQ options only
        meta = q.get("metadata")
        if meta:
            while isinstance(meta, str):
                meta = json.loads(meta)
            if meta.get("options"):
                clean_q["options"] = meta["options"]
        clean_questions.append(clean_q)

    # TODO: calculate time_remaining_seconds from Redis session TTL
    return {
        "assessment_id": assessment_id,
        "questions": clean_questions,
    }


# ---------------------------------------------------------------------------
# 13. POST /api/v1/participate/:id/submit — Participant submits assessment
# ---------------------------------------------------------------------------


@router.post("/participate/{assessment_id}/submit")
async def submit_assessment(assessment_id: str, token: str, req: SubmitAnswersRequest):
    """Participant submits their answers. Single endpoint per api_contract.md Section 2.5.3.

    Side effects:
    1. Writes participant_answers rows
    2. Updates participant_submissions.status -> 'submitted'
    3. Publishes assessorflow.participant.submission-completed
    """
    participant_id = _extract_participant_from_token(token)
    if not participant_id:
        raise HTTPException(400, "Invalid or expired token")

    if not req.answers:
        raise HTTPException(400, "At least one answer is required")

    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    # Ensure submission exists
    submission = await repo.create_submission(assessment_id, participant_id)
    submission_id = submission["id"]

    # Write answers and update status
    await repo.submit_answers(submission_id, req.answers)

    # TODO: evict participant:{email} from Redis

    # Publish submission-completed event
    if config.get("workflow_id"):
        await pubsub.publish_submission_completed(
            workflow_id=config["workflow_id"],
            assessment_id=assessment_id,
            participant_id=participant_id,
            submission_id=submission_id,
            correlation_id=f"corr_{uuid.uuid4().hex[:12]}",
        )

    return {
        "submission_id": submission_id,
        "status": "submitted",
        "submitted_at": submission.get("submitted_at"),
        "answers_recorded": len(req.answers),
    }


# ===========================================================================
# Report endpoints
# ===========================================================================

# ---------------------------------------------------------------------------
# 14. GET /api/v1/reports/:id — Participant views their report (token-based)
# ---------------------------------------------------------------------------


@router.get("/reports/{report_id}")
async def get_participant_report(report_id: str, token: str):
    """Participant views their report with per-question feedback.

    Per api_contract.md Section 2.7.2. Token-based access, no JWT.
    """
    participant_id = _extract_participant_from_token(token)
    if not participant_id:
        raise HTTPException(400, "Invalid or expired token")

    report = await repo.get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")

    return report


# ---------------------------------------------------------------------------
# 15. GET /api/v1/assessments/:id/reports — Assessor views all reports
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/reports")
async def get_assessment_reports(
    assessment_id: str, user: UserContext = Depends(get_current_user)
):
    """Assessor views all participant reports before distributing.

    Per api_contract.md Section 2.7.3.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    reports = await repo.get_reports_for_assessment(assessment_id)
    return {"assessment_id": assessment_id, "reports": reports}


# ===========================================================================
# HITL review endpoints (Phase 8)
# ===========================================================================

# ---------------------------------------------------------------------------
# 16. GET /api/v1/assessments/:id/review — Assessor review page
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/review")
async def get_review(assessment_id: str, user: UserContext = Depends(get_current_user)):
    """Assessor reviews generated questions before approval (Phase 8 HITL).

    Returns questions without answers. Includes grounding citation stub
    (source_chunk_ids from metadata). Full citation resolution requires
    calling Knowledge Service GetChunksByIds.
    Per api_contract.md Section 2.4.1.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    workflow_id = config.get("workflow_id")
    if not workflow_id:
        raise HTTPException(400, "Assessment has no workflow")

    question_set = await repo.get_question_set_by_workflow(workflow_id)
    if not question_set:
        raise HTTPException(404, "No question set found")

    questions = await repo.get_generated_questions_with_answers(question_set["id"])

    # Strip answers, keep metadata for review
    for q in questions:
        q.pop("structured_answer", None)
        q.pop("non_structured_model_answer", None)

    return {
        "assessment_id": assessment_id,
        "question_set_id": question_set["id"],
        "status": question_set.get("status", "generated"),
        "questions": questions,
    }


# ---------------------------------------------------------------------------
# 17. GET /api/v1/assessments/:id/topics — Extracted subtopics
# ---------------------------------------------------------------------------


@router.get("/assessments/{assessment_id}/topics")
async def get_topics(assessment_id: str, user: UserContext = Depends(get_current_user)):
    """Get extracted subtopics for the HITL review sidebar.

    Per api_contract.md Section 2.4.4.
    Topic data lives in Knowledge Service. This endpoint returns a stub
    that the frontend can use. Full implementation requires gRPC call
    to Knowledge Service GetTopics.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    # TODO: call Knowledge Service gRPC GetTopics(workflow_id) to fetch topics
    # and join with generated_questions.topic_id to compute question counts.
    # For now, return empty structure matching the contract.
    return {
        "assessment_id": assessment_id,
        "subtopics": [],
        "total_subtopics": 0,
    }


# ===========================================================================
# Helpers
# ===========================================================================


def _extract_participant_from_token(token: str) -> str | None:
    """Extract participant_id from a signed assessment link token.

    TODO: implement proper JWT/HMAC token verification with expiry.
    For now, treats the token as the participant_id directly.
    """
    if not token:
        return None
    # Placeholder — in production, verify HMAC signature + expiry
    return token
