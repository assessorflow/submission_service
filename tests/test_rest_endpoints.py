"""Automated REST endpoint tests — covers all assessor + participant endpoints.

Runs against real PostgreSQL (Docker container).
JWT auth is mocked via conftest.py.
Pub/Sub and GCS calls are skipped (would need mocking for full isolation).
"""

import pytest
from httpx import AsyncClient


# ===========================================================================
# T-1: Create Assessment
# ===========================================================================

@pytest.mark.asyncio
async def test_create_assessment(client: AsyncClient):
    resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "OOP Mid-Term Quiz",
        "purpose": "topic_revision",
        "duration_minutes": 60,
        "difficulty_level": "easy",
        "structured_question_count": 6,
        "non_structured_question_count": 2,
        "web_research_mode": "manual",
        "participants": ["student1@email.com", "student2@email.com"],
        "groups": [
            {"group_name": "Group A", "members": ["student1@email.com", "student2@email.com"]},
        ],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["assessment"]["status"] == "draft"
    assert data["assessment"]["assessor_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert data["assessment"]["purpose"] == "topic_revision"


@pytest.mark.asyncio
async def test_create_assessment_invalid_purpose(client: AsyncClient):
    resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Bad Quiz",
        "purpose": "invalid_purpose",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_assessment_invalid_difficulty(client: AsyncClient):
    resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Bad Quiz",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "extreme",
    })
    assert resp.status_code == 400


# ===========================================================================
# T-2: List Assessments
# ===========================================================================

@pytest.mark.asyncio
async def test_list_assessments_empty(client: AsyncClient):
    resp = await client.get("/api/v1/assessments")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_assessments_with_data(client: AsyncClient):
    # Create two assessments
    await client.post("/api/v1/assessments", json={
        "assessment_title": "Quiz 1",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    await client.post("/api/v1/assessments", json={
        "assessment_title": "Quiz 2",
        "purpose": "exam_prep",
        "duration_minutes": 60,
        "difficulty_level": "hard",
    })

    resp = await client.get("/api/v1/assessments")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_list_assessments_with_status_filter(client: AsyncClient):
    await client.post("/api/v1/assessments", json={
        "assessment_title": "Draft Quiz",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })

    resp = await client.get("/api/v1/assessments?status=draft")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await client.get("/api/v1/assessments?status=completed")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_assessments_pagination(client: AsyncClient):
    for i in range(3):
        await client.post("/api/v1/assessments", json={
            "assessment_title": f"Quiz {i}",
            "purpose": "topic_revision",
            "duration_minutes": 30,
            "difficulty_level": "easy",
        })

    resp = await client.get("/api/v1/assessments?page=1&page_size=2")
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2


# ===========================================================================
# T-3: Get Assessment
# ===========================================================================

@pytest.mark.asyncio
async def test_get_assessment(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Get Test",
        "purpose": "exam_prep",
        "duration_minutes": 45,
        "difficulty_level": "medium",
        "participants": ["alice@test.com"],
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    resp = await client.get(f"/api/v1/assessments/{assessment_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["assessment"]["assessment_title"] == "Get Test"
    assert len(data["participants"]) == 1
    assert data["participants"][0]["email"] == "alice@test.com"


@pytest.mark.asyncio
async def test_get_assessment_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/assessments/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# ===========================================================================
# T-4: Get Assessment Status
# ===========================================================================

@pytest.mark.asyncio
async def test_get_assessment_status(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Status Test",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    resp = await client.get(f"/api/v1/assessments/{assessment_id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"
    assert data["workflow_id"] is None


# ===========================================================================
# T-6: List Materials (empty)
# ===========================================================================

@pytest.mark.asyncio
async def test_list_materials_empty(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Materials Test",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    resp = await client.get(f"/api/v1/assessments/{assessment_id}/materials")
    assert resp.status_code == 200
    assert resp.json() == []


# ===========================================================================
# T-7: Validation Status
# ===========================================================================

@pytest.mark.asyncio
async def test_validation_status_no_materials(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Validation Test",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    resp = await client.get(f"/api/v1/assessments/{assessment_id}/materials/validation-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["all_passed"] is False
    assert data["failed_count"] == 0


# ===========================================================================
# T-9: List Rubrics (empty)
# ===========================================================================

@pytest.mark.asyncio
async def test_list_rubrics_empty(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Rubrics Test",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    resp = await client.get(f"/api/v1/assessments/{assessment_id}/rubrics")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ===========================================================================
# T-13: Topics (stub)
# ===========================================================================

@pytest.mark.asyncio
async def test_get_topics_stub(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Topics Test",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    resp = await client.get(f"/api/v1/assessments/{assessment_id}/topics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["subtopics"] == []
    assert data["total_subtopics"] == 0


# ===========================================================================
# T-15: Approved Questions (empty before approval)
# ===========================================================================

@pytest.mark.asyncio
async def test_approved_questions_empty(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Approved Q Test",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    resp = await client.get(f"/api/v1/assessments/{assessment_id}/approved-questions")
    assert resp.status_code == 404


# ===========================================================================
# T-17: Reports (empty)
# ===========================================================================

@pytest.mark.asyncio
async def test_get_reports_empty(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Reports Test",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    resp = await client.get(f"/api/v1/assessments/{assessment_id}/reports")
    assert resp.status_code == 200
    assert resp.json()["reports"] == []


# ===========================================================================
# Participant Endpoints
# ===========================================================================

@pytest.mark.asyncio
async def test_participant_access_not_active(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Participant Test",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
        "participants": ["participant@test.com"],
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    # Assessment is still 'draft', not 'assessment_active'
    participants = (await client.get(f"/api/v1/assessments/{assessment_id}")).json()["participants"]
    pid = participants[0]["id"]

    resp = await client.get(f"/api/v1/participate/{assessment_id}?token={pid}")
    assert resp.status_code == 403  # not active


@pytest.mark.asyncio
async def test_participant_submit_invalid_token(client: AsyncClient):
    create_resp = await client.post("/api/v1/assessments", json={
        "assessment_title": "Submit Test",
        "purpose": "topic_revision",
        "duration_minutes": 30,
        "difficulty_level": "easy",
    })
    assessment_id = create_resp.json()["assessment"]["id"]

    resp = await client.post(
        f"/api/v1/participate/{assessment_id}/submit?token=",
        json={"answers": [{"question_id": "fake", "answer_content": "A"}]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_participant_report_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/reports/00000000-0000-0000-0000-000000000000?token=some-id")
    assert resp.status_code == 404


# ===========================================================================
# Health / Ready
# ===========================================================================

@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_ready(client: AsyncClient):
    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
