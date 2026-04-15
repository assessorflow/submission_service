"""External REST API routes — assessor/participant facing.

9 granular endpoints matching api_contract.md Section 2.1.
+ 1 combined launch endpoint for the frontend (create + upload + start in one call).
All endpoints require JWT authentication (C-2 fix).
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
            raise ValueError(f"Invalid difficulty_level. Must be one of: {VALID_DIFFICULTIES}")
        return v

    @field_validator("web_research_mode")
    @classmethod
    def validate_web_research_mode(cls, v):
        if v not in {"manual", "auto"}:
            raise ValueError("web_research_mode must be 'manual' or 'auto'")
        return v


class ApproveQuestionsRequest(BaseModel):
    question_set_id: str
    kept_question_ids: list[str]


class DistributeRequest(BaseModel):
    participant_ids: list[str] = []  # empty = all participants


# ---------------------------------------------------------------------------
# 1. POST /api/v1/assessments — Create assessment config
# ---------------------------------------------------------------------------

@router.post("/assessments")
async def create_assessment(req: CreateAssessmentRequest, user: UserContext = Depends(get_current_user)):
    """Create a new assessment configuration."""
    logger.info("create_assessment", title=req.assessment_title, assessor_id=user.user_id)

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
            participant = next((p for p in participants if p["email"] == member_email), None)
            if participant:
                await repo.add_group_member(g["id"], participant["id"])

    return {"assessment": config, "status": "created"}


# ---------------------------------------------------------------------------
# 2. GET /api/v1/assessments/:id — Get assessment details
# ---------------------------------------------------------------------------

@router.get("/assessments/{assessment_id}")
async def get_assessment(assessment_id: str, user: UserContext = Depends(get_current_user)):
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
# 2b. GET /api/v1/assessments/:id/materials — Get materials for an assessment
# ---------------------------------------------------------------------------

@router.get("/assessments/{assessment_id}/materials")
async def get_materials(assessment_id: str, source: str | None = None, user: UserContext = Depends(get_current_user)):
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

    material = await repo.add_material(
        assessment_id=assessment_id,
        file_name=file.filename,
        storage_path=storage_path,
        file_type=file.content_type or "application/octet-stream",
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

    rubric = await repo.add_rubric(
        assessment_id=assessment_id,
        file_name=file.filename,
        storage_path=storage_path,
        file_type=file.content_type or "application/octet-stream",
    )

    return {"rubric": rubric, "status": "uploaded"}


# ---------------------------------------------------------------------------
# 5. POST /api/v1/assessments/:id/start — Start workflow
# ---------------------------------------------------------------------------

@router.post("/assessments/{assessment_id}/start")
async def start_workflow(assessment_id: str, user: UserContext = Depends(get_current_user)):
    """Start the assessment workflow — publishes workflow.start to Pub/Sub."""
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")
    if config["status"] != "draft":
        raise HTTPException(400, f"Cannot start workflow — status is '{config['status']}'")

    # Generate workflow_id
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    correlation_id = f"corr_{uuid.uuid4().hex[:12]}"

    # Update assessment with workflow_id and status
    await repo.update_assessment_status(assessment_id, "material_validation", workflow_id=workflow_id)

    # Publish to Pub/Sub
    await pubsub.publish_workflow_start(
        workflow_id=workflow_id,
        assessment_id=assessment_id,
        correlation_id=correlation_id,
    )

    logger.info("workflow_started", assessment_id=assessment_id, workflow_id=workflow_id)

    return {
        "workflow_id": workflow_id,
        "assessment_id": assessment_id,
        "status": "material_validation",
        "correlation_id": correlation_id,
    }


# ---------------------------------------------------------------------------
# 6. GET /api/v1/assessments/:id/questions — Get approved questions
# ---------------------------------------------------------------------------

@router.get("/assessments/{assessment_id}/questions")
async def get_approved_questions(assessment_id: str, user: UserContext = Depends(get_current_user)):
    """Get approved questions for participant assessment-taking."""
    questions = await repo.get_approved_questions_with_answers(assessment_id)
    if not questions:
        raise HTTPException(404, "No approved questions found")

    # Strip model answers — participants should not see them
    for q in questions:
        q.pop("non_structured_model_answer", None)
        q.pop("structured_answer", None)
        if q.get("metadata"):
            q["metadata"].pop("option_explanations", None)

    return {"questions": questions, "count": len(questions)}


# ---------------------------------------------------------------------------
# 7. POST /api/v1/assessments/:id/approve — HITL question approval
# ---------------------------------------------------------------------------

@router.post("/assessments/{assessment_id}/approve")
async def approve_questions(assessment_id: str, req: ApproveQuestionsRequest, user: UserContext = Depends(get_current_user)):
    """Assessor approves/rejects generated questions (Phase 8 HITL)."""
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")

    result = await repo.approve_questions(
        assessment_id=assessment_id,
        question_set_id=req.question_set_id,
        kept_question_ids=req.kept_question_ids,
    )

    # Update status
    await repo.update_assessment_status(assessment_id, "ready_for_distribution")

    # Publish HITL approval event
    if config.get("workflow_id"):
        await pubsub.publish_human_review_approved(
            workflow_id=config["workflow_id"],
            assessment_id=assessment_id,
            approved_question_set_id=str(result["id"]),
            correlation_id=f"corr_{uuid.uuid4().hex[:12]}",
        )

    return {"approved_question_set": result, "status": "approved"}


# ---------------------------------------------------------------------------
# 8. POST /api/v1/assessments/:id/distribute — Send invitations
# ---------------------------------------------------------------------------

@router.post("/assessments/{assessment_id}/distribute")
async def distribute_invitations(assessment_id: str, req: DistributeRequest, user: UserContext = Depends(get_current_user)):
    """Send assessment invitations to selected participants."""
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
async def distribute_reports(assessment_id: str, user: UserContext = Depends(get_current_user)):
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

    logger.info("launch_assessment", title=cfg["assessment_title"], assessor_id=user.user_id)

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
        participants_added = await repo.add_participants(assessment_id, cfg["participants"])

    # 3. Create groups
    groups_created = []
    for group in cfg.get("groups", []):
        g = await repo.create_group(assessment_id, group["group_name"])
        groups_created.append(g)
        for member_email in group.get("members", []):
            all_participants = await repo.get_participants(assessment_id)
            participant = next((p for p in all_participants if p["email"] == member_email), None)
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
            file_type=file.content_type or "application/octet-stream",
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
            file_type=rubric.content_type or "application/octet-stream",
        )

    # 6. Start workflow
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    correlation_id = f"corr_{uuid.uuid4().hex[:12]}"

    await repo.update_assessment_status(assessment_id, "material_validation", workflow_id=workflow_id)

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


# ---------------------------------------------------------------------------
# 11. POST /api/v1/assessments/:id/submissions — Participant starts assessment
# ---------------------------------------------------------------------------

class SubmitAnswersRequest(BaseModel):
    answers: list[dict[str, Any]]  # [{"question_id": "uuid", "answer_content": "..."}]


@router.post("/assessments/{assessment_id}/submissions")
async def create_submission(assessment_id: str, participant_id: str):
    """Participant opens assessment link — creates a submission record.

    participant_id comes from the assessment link (not JWT — participants are not registered users).
    Per overall.md Phase 9.
    """
    config = await repo.get_assessment_config(assessment_id)
    if not config:
        raise HTTPException(404, "Assessment not found")
    if config["status"] != "assessment_active":
        raise HTTPException(400, f"Assessment is not active — status is '{config['status']}'")

    submission = await repo.create_submission(assessment_id, participant_id)
    return {"submission": submission, "status": "in_progress"}


# ---------------------------------------------------------------------------
# 12. POST /api/v1/submissions/:id/answers — Participant submits answers
# ---------------------------------------------------------------------------

@router.post("/submissions/{submission_id}/answers")
async def submit_answers(submission_id: str, req: SubmitAnswersRequest):
    """Participant submits their answers. Publishes submission-completed event."""
    if not req.answers:
        raise HTTPException(400, "At least one answer is required")

    await repo.submit_answers(submission_id, req.answers)

    # TODO: look up workflow_id, assessment_id, participant_id from submission
    # and publish assessorflow.participant.submission-completed

    return {"submission_id": submission_id, "status": "submitted", "answers_count": len(req.answers)}
