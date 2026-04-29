"""End-to-end workflow test — simulates the full assessment lifecycle.

Tests the complete flow from assessment creation through question generation,
approval, participant submission, and evaluation.

Uses:
- Real PostgreSQL (Docker container on port 15433)
- Real Google Pub/Sub (project aflow-491809)
- Mock JWT auth (no Identity Service needed)

Agent-side operations (gRPC) are simulated via direct repo calls.
"""

import pytest
from httpx import AsyncClient

from submission_service.db import repository as repo


# ===========================================================================
# Helper
# ===========================================================================


async def _create_assessment(client: AsyncClient) -> dict:
    resp = await client.post(
        "/api/v1/assessments",
        json={
            "assessment_title": "E2E Workflow Test",
            "purpose": "topic_revision",
            "duration_minutes": 60,
            "difficulty_level": "easy",
            "structured_question_count": 2,
            "non_structured_question_count": 1,
            "web_research_mode": "manual",
            "participants": ["student1@e2e.com", "student2@e2e.com"],
            "groups": [
                {
                    "group_name": "Group A",
                    "members": ["student1@e2e.com", "student2@e2e.com"],
                },
            ],
        },
    )
    assert resp.status_code == 200
    return resp.json()


# ===========================================================================
# Phase 2-8: Create → Generate → Approve
# ===========================================================================


@pytest.mark.asyncio
async def test_create_and_verify(client: AsyncClient):
    data = await _create_assessment(client)
    assessment_id = data["assessment"]["id"]

    # Verify participants + status
    get_resp = await client.get(f"/api/v1/assessments/{assessment_id}")
    assert len(get_resp.json()["participants"]) == 2

    status_resp = await client.get(f"/api/v1/assessments/{assessment_id}/status")
    assert status_resp.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_generate_and_review(client: AsyncClient):
    data = await _create_assessment(client)
    assessment_id = data["assessment"]["id"]

    # Simulate agent: create question set + write questions
    workflow_id = "wf_e2ereview0001"
    await repo.update_assessment_status(
        assessment_id, "processing", workflow_id=workflow_id
    )

    qs = await repo.create_question_set(workflow_id)
    await repo.write_generated_questions(
        qs["id"],
        [
            {
                "question_type": "structured",
                "content": "What is encapsulation?",
                "structured_answer": "A",
                "metadata": {"options": {"A": "Bundling", "B": "Looping"}},
                "iteration": 1,
            },
            {
                "question_type": "non_structured",
                "content": "Explain OOP.",
                "non_structured_model_answer": "OOP uses objects.",
                "metadata": {"max_marks": 10},
                "iteration": 1,
            },
        ],
    )

    # Review page — answers stripped
    review_resp = await client.get(f"/api/v1/assessments/{assessment_id}/review")
    assert review_resp.status_code == 200
    for q in review_resp.json()["questions"]:
        assert "structured_answer" not in q
        assert "non_structured_model_answer" not in q

    # Generated questions endpoint
    gen_resp = await client.get(
        f"/api/v1/assessments/{assessment_id}/generated-questions?question_set_id={qs['id']}"
    )
    assert gen_resp.status_code == 200
    assert gen_resp.json()["count"] == 2


@pytest.mark.asyncio
async def test_approve_all(client: AsyncClient):
    data = await _create_assessment(client)
    assessment_id = data["assessment"]["id"]

    workflow_id = "wf_e2eapprove01"
    await repo.update_assessment_status(
        assessment_id, "processing", workflow_id=workflow_id
    )
    qs = await repo.create_question_set(workflow_id)
    await repo.write_generated_questions(
        qs["id"],
        [
            {
                "question_type": "structured",
                "content": "Q1?",
                "structured_answer": "A",
                "metadata": {},
                "iteration": 1,
            },
            {
                "question_type": "structured",
                "content": "Q2?",
                "structured_answer": "B",
                "metadata": {},
                "iteration": 1,
            },
        ],
    )

    # Approve all — publishes to real Pub/Sub
    approve_resp = await client.post(
        f"/api/v1/assessments/{assessment_id}/review/approve",
        json={"removed_question_ids": []},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["questions_approved"] == 2
    assert approve_resp.json()["questions_removed"] == 0

    # Verify approved questions
    approved_resp = await client.get(
        f"/api/v1/assessments/{assessment_id}/approved-questions"
    )
    assert approved_resp.json()["count"] == 2

    # Status should be ready_for_distribution
    status_resp = await client.get(f"/api/v1/assessments/{assessment_id}/status")
    assert status_resp.json()["status"] == "ready_for_distribution"


@pytest.mark.asyncio
async def test_approve_with_removal(client: AsyncClient):
    data = await _create_assessment(client)
    assessment_id = data["assessment"]["id"]

    workflow_id = "wf_e2eremoval01"
    await repo.update_assessment_status(
        assessment_id, "processing", workflow_id=workflow_id
    )
    qs = await repo.create_question_set(workflow_id)
    written = await repo.write_generated_questions(
        qs["id"],
        [
            {
                "question_type": "structured",
                "content": "Good Q",
                "structured_answer": "A",
                "metadata": {},
                "iteration": 1,
            },
            {
                "question_type": "structured",
                "content": "Bad Q",
                "structured_answer": "B",
                "metadata": {},
                "iteration": 1,
            },
        ],
    )
    bad_id = written[1]["id"]

    approve_resp = await client.post(
        f"/api/v1/assessments/{assessment_id}/review/approve",
        json={"removed_question_ids": [bad_id]},
    )
    assert approve_resp.json()["questions_approved"] == 1
    assert approve_resp.json()["questions_removed"] == 1

    approved_resp = await client.get(
        f"/api/v1/assessments/{assessment_id}/approved-questions"
    )
    assert approved_resp.json()["count"] == 1


# ===========================================================================
# Phase 9: Invite → Participate → Submit (publishes to real Pub/Sub)
# ===========================================================================


@pytest.mark.asyncio
async def test_invite_and_participate(client: AsyncClient):
    data = await _create_assessment(client)
    assessment_id = data["assessment"]["id"]

    # Setup: generate + approve
    workflow_id = "wf_e2esubmit001"
    await repo.update_assessment_status(
        assessment_id, "processing", workflow_id=workflow_id
    )
    qs = await repo.create_question_set(workflow_id)
    await repo.write_generated_questions(
        qs["id"],
        [
            {
                "question_type": "structured",
                "content": "What is OOP?",
                "structured_answer": "A",
                "metadata": {"options": {"A": "Object-Oriented", "B": "Other"}},
                "iteration": 1,
            },
        ],
    )
    await client.post(
        f"/api/v1/assessments/{assessment_id}/review/approve",
        json={"removed_question_ids": []},
    )

    approved = await client.get(
        f"/api/v1/assessments/{assessment_id}/approved-questions"
    )
    question_id = approved.json()["questions"][0]["id"]

    # Invite — publishes assessorflow.invitation.sent
    invite_resp = await client.post(
        f"/api/v1/assessments/{assessment_id}/invite",
        json={"participant_ids": []},
    )
    assert invite_resp.json()["status"] == "assessment_active"

    # Get participant ID
    assessment = await client.get(f"/api/v1/assessments/{assessment_id}")
    participant_id = assessment.json()["participants"][0]["id"]

    # Participant accesses assessment
    access_resp = await client.get(
        f"/api/v1/participate/{assessment_id}?token={participant_id}"
    )
    assert access_resp.status_code == 200
    assert access_resp.json()["status"] == "ready"
    assert (
        access_resp.json()["total_questions"] == 3
    )  # from assessment config (2 structured + 1 non-structured)

    # Participant gets questions — no answers exposed
    q_resp = await client.get(
        f"/api/v1/participate/{assessment_id}/questions?token={participant_id}"
    )
    assert q_resp.status_code == 200
    questions = q_resp.json()["questions"]
    assert len(questions) == 1
    assert "options" in questions[0]
    assert "structured_answer" not in questions[0]

    # Participant submits — publishes assessorflow.participant.submission-completed
    submit_resp = await client.post(
        f"/api/v1/participate/{assessment_id}/submit?token={participant_id}",
        json={"answers": [{"question_id": question_id, "answer_content": "A"}]},
    )
    assert submit_resp.status_code == 200
    assert submit_resp.json()["status"] == "submitted"
    assert submit_resp.json()["answers_recorded"] == 1


# ===========================================================================
# Phase 10-12: Evaluation → Report
# ===========================================================================


@pytest.mark.asyncio
async def test_evaluation_and_reports(client: AsyncClient):
    data = await _create_assessment(client)
    assessment_id = data["assessment"]["id"]

    # Full setup: generate → approve → invite → submit
    workflow_id = "wf_e2ereport001"
    await repo.update_assessment_status(
        assessment_id, "processing", workflow_id=workflow_id
    )
    qs = await repo.create_question_set(workflow_id)
    await repo.write_generated_questions(
        qs["id"],
        [
            {
                "question_type": "structured",
                "content": "Q1?",
                "structured_answer": "A",
                "metadata": {"options": {"A": "Yes", "B": "No"}},
                "iteration": 1,
            },
        ],
    )
    await client.post(
        f"/api/v1/assessments/{assessment_id}/review/approve",
        json={"removed_question_ids": []},
    )
    approved = await client.get(
        f"/api/v1/assessments/{assessment_id}/approved-questions"
    )
    question_id = approved.json()["questions"][0]["id"]

    await client.post(
        f"/api/v1/assessments/{assessment_id}/invite", json={"participant_ids": []}
    )

    assessment = await client.get(f"/api/v1/assessments/{assessment_id}")
    participant_id = assessment.json()["participants"][0]["id"]

    await client.post(
        f"/api/v1/participate/{assessment_id}/submit?token={participant_id}",
        json={"answers": [{"question_id": question_id, "answer_content": "A"}]},
    )

    # Phase 10: Evaluator creates evaluation (simulating gRPC)
    submission = await repo.create_submission(assessment_id, participant_id)
    evaluation = await repo.create_evaluation(
        workflow_id, participant_id, submission["id"]
    )
    await repo.write_evaluation_details(
        evaluation["id"],
        [
            {
                "question_id": question_id,
                "score": 10.0,
                "max_score": 10.0,
                "evaluation_method": "deterministic",
            }
        ],
        total_score=10.0,
        max_score=10.0,
    )

    # Phase 11: Reporting Agent creates report (simulating gRPC)
    report = await repo.create_report(
        workflow_id=workflow_id,
        participant_id=participant_id,
        evaluation_id=evaluation["id"],
        report_content={
            "total_score": 10,
            "max_score": 10,
            "per_question_feedback": [
                {
                    "question_id": question_id,
                    "score": 10,
                    "max_score": 10,
                    "feedback": "Correct!",
                },
            ],
            "overall_summary": "Perfect score.",
        },
    )

    # Phase 12: Assessor views reports
    reports_resp = await client.get(f"/api/v1/assessments/{assessment_id}/reports")
    assert reports_resp.status_code == 200
    assert len(reports_resp.json()["reports"]) >= 1

    # Participant views their report
    report_resp = await client.get(
        f"/api/v1/reports/{report['id']}?token={participant_id}"
    )
    assert report_resp.status_code == 200
    assert report_resp.json()["status"] == "completed"

    # Distribute reports
    dist_resp = await client.post(
        f"/api/v1/assessments/{assessment_id}/reports/distribute"
    )
    assert dist_resp.status_code == 200
    assert dist_resp.json()["count"] == 2
