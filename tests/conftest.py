"""Shared test fixtures for submission-service automated tests.

Uses the real PostgreSQL database (Docker container on port 15433).
Overrides JWT auth with a mock user so tests don't need Identity Service.
"""

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from submission_service.main import app
from submission_service.services.auth import UserContext, get_current_user
from submission_service.db import pool as db_pool_module


# ---------------------------------------------------------------------------
# Mock auth — bypass JWT validation for tests
# ---------------------------------------------------------------------------

TEST_USER = UserContext(
    user_id="550e8400-e29b-41d4-a716-446655440000",
    email="test-assessor@email.com",
    role="assessor",
    name="Test Assessor",
)


async def mock_get_current_user() -> UserContext:
    return TEST_USER


app.dependency_overrides[get_current_user] = mock_get_current_user


# ---------------------------------------------------------------------------
# Use a single event loop for all tests (session-scoped)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop_policy():
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Database — reset pool + clean tables after each test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def reset_and_clean_db():
    """Reset the singleton DB pool before each test, clean tables after."""
    # Reset pool so it gets created on the current event loop
    db_pool_module._pool = None
    # Clean BEFORE test to handle stale data from crashed/interrupted runs
    pool = await db_pool_module.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM participant_reports")
        await conn.execute("DELETE FROM evaluation_details")
        await conn.execute("DELETE FROM group_evaluations")
        await conn.execute("DELETE FROM evaluations")
        await conn.execute("DELETE FROM participant_answers")
        await conn.execute("DELETE FROM participant_submissions")
        await conn.execute("DELETE FROM approved_questions")
        await conn.execute("DELETE FROM approved_question_sets")
        await conn.execute("DELETE FROM generated_questions")
        await conn.execute("DELETE FROM question_sets")
        await conn.execute("DELETE FROM assessment_rubrics")
        await conn.execute("DELETE FROM assessment_materials")
        await conn.execute("DELETE FROM participant_group_members")
        await conn.execute("DELETE FROM participant_groups")
        await conn.execute("DELETE FROM assessment_participants")
        await conn.execute("DELETE FROM assessment_configs")
    yield
    # Clean tables after test
    pool = await db_pool_module.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM participant_reports")
        await conn.execute("DELETE FROM evaluation_details")
        await conn.execute("DELETE FROM group_evaluations")
        await conn.execute("DELETE FROM evaluations")
        await conn.execute("DELETE FROM participant_answers")
        await conn.execute("DELETE FROM participant_submissions")
        await conn.execute("DELETE FROM approved_questions")
        await conn.execute("DELETE FROM approved_question_sets")
        await conn.execute("DELETE FROM generated_questions")
        await conn.execute("DELETE FROM question_sets")
        await conn.execute("DELETE FROM assessment_rubrics")
        await conn.execute("DELETE FROM assessment_materials")
        await conn.execute("DELETE FROM participant_group_members")
        await conn.execute("DELETE FROM participant_groups")
        await conn.execute("DELETE FROM assessment_participants")
        await conn.execute("DELETE FROM assessment_configs")
    # Close pool so next test gets fresh one
    await db_pool_module.close_pool()


# ---------------------------------------------------------------------------
# HTTP client — uses ASGI transport (no real server needed)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
