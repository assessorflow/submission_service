# Assessment Submission Service — Manual Testing Guide

> **Service:** Assessment Submission Service
> **Base URL (REST):** `http://localhost:8060`
> **gRPC:** `localhost:9060`
> **Database:** `af_submission` on `localhost:15433`
> **Postman-friendly:** All requests below can be imported directly into Postman.

---

## Prerequisites

1. Service is running: `uvicorn submission_service.main:app --host 0.0.0.0 --port 8060`
2. PostgreSQL is running with `af_submission` database and all tables created
3. Identity Service is running (for JWT validation) at `localhost:8081`
4. Obtain a valid JWT token by logging in via Identity Service:
   ```
   POST http://localhost:8081/api/v1/auth/login
   { "email": "assessor@email.com", "password": "password" }
   ```
   Save the `access_token` from the response.

### Postman Setup

- Create an environment variable `{{base_url}}` = `http://localhost:8060`
- Create an environment variable `{{token}}` = the JWT access_token
- Set `Authorization` header globally: `Bearer {{token}}`

---

## Part 1: REST API — Assessor Endpoints (JWT Auth)

All assessor endpoints require:
```
Authorization: Bearer {{token}}
Content-Type: application/json
```

---

### T-1. Create Assessment

**Contract:** api_contract.md Section 2.1.1

```
POST {{base_url}}/api/v1/assessments

{
  "assessment_title": "OOP Mid-Term Quiz",
  "purpose": "topic_revision",
  "duration_minutes": 60,
  "difficulty_level": "easy",
  "structured_question_count": 6,
  "non_structured_question_count": 2,
  "deadline": "2026-05-25T23:59:00Z",
  "web_research_mode": "manual",
  "participants": [
    "student1@email.com",
    "student2@email.com",
    "student3@email.com",
    "student4@email.com"
  ],
  "groups": [
    {
      "group_name": "Group A",
      "members": ["student1@email.com", "student2@email.com"]
    },
    {
      "group_name": "Group B",
      "members": ["student3@email.com", "student4@email.com"]
    }
  ]
}
```

**Expected:** 200 with `assessment.id` (UUID). Save as `{{assessment_id}}`.

**Validation tests:**
- [ ] Response has `assessment.id`, `assessment.status` = `"draft"`
- [ ] `assessment.assessor_id` matches JWT `sub` claim (not from body)
- [ ] Participants created (check T-3)
- [ ] Groups created with correct members

**Error cases:**
- [ ] Invalid purpose -> 422 (`"purpose": "invalid"`)
- [ ] Invalid difficulty -> 422 (`"difficulty_level": "extreme"`)
- [ ] Invalid web_research_mode -> 422 (`"web_research_mode": "turbo"`)

---

### T-2. List Assessments

**Contract:** api_contract.md Section 2.1.3

```
GET {{base_url}}/api/v1/assessments?page=1&page_size=10
```

```
GET {{base_url}}/api/v1/assessments?status=draft&page=1&page_size=10
```

**Expected:** 200 with `{items: [...], total, page, page_size}`.

**Validation tests:**
- [ ] Only returns assessments owned by the JWT user
- [ ] `total` reflects correct count
- [ ] Status filter works (only returns matching status)
- [ ] Pagination works (try `page=2` with enough data)

---

### T-3. Get Assessment Details

**Contract:** api_contract.md Section 2.1.2

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}
```

**Expected:** 200 with `{assessment, participants, materials}`.

**Validation tests:**
- [ ] `assessment.id` matches
- [ ] `participants` array has 4 entries with correct emails
- [ ] `materials` array is empty (nothing uploaded yet)

**Error cases:**
- [ ] Non-existent ID -> 404

---

### T-4. Get Assessment Status

**Contract:** api_contract.md Section 2.8.1

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/status
```

**Expected:** 200 with `{assessment_id, status, workflow_id, updated_at}`.

**Validation tests:**
- [ ] `status` = `"draft"` (workflow not started yet)
- [ ] `workflow_id` = `null` (not assigned yet)

---

### T-5. Upload Material

**Contract:** api_contract.md Section 2.2.1

```
POST {{base_url}}/api/v1/assessments/{{assessment_id}}/materials
Content-Type: multipart/form-data

Form field: file = [select a PDF or DOCX file]
```

**Postman:** Body tab -> form-data -> key `file` (type: File) -> select file.

**Expected:** 200 with `{material.id, material.file_name, material.storage_path}`. Save `material.id` as `{{material_id}}`.

**Validation tests:**
- [ ] `material.source` = `"upload"`
- [ ] `material.readiness_status` = `null` (not yet validated)
- [ ] File exists in GCS at the returned `storage_path`

**Error cases:**
- [ ] Non-existent assessment_id -> 404

---

### T-6. List Materials

**Contract:** api_contract.md Section 2.2.2

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/materials
```

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/materials?source=upload
```

**Expected:** 200 with array of materials.

**Validation tests:**
- [ ] Contains the material from T-5
- [ ] Source filter works

---

### T-7. Get Material Validation Status

**Contract:** api_contract.md Section 2.2.4

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/materials/validation-status
```

**Expected:** 200 with `{status, results, all_passed, failed_count}`.

**Validation tests:**
- [ ] `status` = `"in_progress"` (materials not yet validated)
- [ ] `all_passed` = `false`
- [ ] Each material in `results` has `readiness_status: null`

---

### T-8. Upload Rubric

**Contract:** api_contract.md Section 2.2b.1

```
POST {{base_url}}/api/v1/assessments/{{assessment_id}}/rubrics
Content-Type: multipart/form-data

Form field: file = [select a PDF or DOCX file]
```

**Expected:** 200 with `{rubric.id, rubric.file_name}`.

---

### T-9. List Rubrics

**Contract:** api_contract.md Section 2.2b.2

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/rubrics
```

**Expected:** 200 with `{items: [...]}` containing the rubric from T-8.

---

### T-10. Start Workflow

**Contract:** api_contract.md Section 2.2.5

```
POST {{base_url}}/api/v1/assessments/{{assessment_id}}/start
```

**Expected:** 200 with `{workflow_id, assessment_id, status, correlation_id}`. Save `{{workflow_id}}`.

**Validation tests:**
- [ ] `status` = `"material_validation"`
- [ ] `workflow_id` starts with `"wf_"` and is 15 chars
- [ ] Assessment status updated (check T-4: status should now be `"material_validation"`)
- [ ] Pub/Sub event `assessorflow.workflow.start` published (check Pub/Sub console or logs)

**Error cases:**
- [ ] Start again -> 400 (status is no longer `"draft"`)

---

### T-11. Get Generated Questions (Draft)

**Contract:** api_contract.md Section 2.3.3

> **Prerequisite:** Q&A Generation Agent must have written questions via gRPC (see gRPC T-G4). Use `{{question_set_id}}` from that step.

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/generated-questions?question_set_id={{question_set_id}}
```

**Expected:** 200 with `{question_set_id, questions[], count}`.

**Validation tests:**
- [ ] `structured_answer` is NOT present in any question
- [ ] `non_structured_model_answer` is NOT present in any question
- [ ] Questions have `content`, `question_type`, `metadata`

---

### T-12. Get Review Page

**Contract:** api_contract.md Section 2.4.1

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/review
```

**Expected:** 200 with `{assessment_id, question_set_id, status, questions[]}`.

**Validation tests:**
- [ ] Answers are stripped
- [ ] `question_set_id` is populated
- [ ] `status` reflects question set status

---

### T-13. Get Topics

**Contract:** api_contract.md Section 2.4.4

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/topics
```

**Expected:** 200 with `{assessment_id, subtopics[], total_subtopics}`.

**Note:** Currently returns empty stub. Will be populated when Knowledge Service gRPC integration is complete.

---

### T-14. Approve Questions

**Contract:** api_contract.md Section 2.4.2

```
POST {{base_url}}/api/v1/assessments/{{assessment_id}}/review/approve

{
  "removed_question_ids": ["{{question_id_to_remove}}"]
}
```

To approve ALL questions (remove none):
```
{
  "removed_question_ids": []
}
```

**Expected:** 200 with `{approved_question_set_id, original_question_set_id, questions_approved, questions_removed, approved_at}`.

**Validation tests:**
- [ ] `questions_approved` = total - removed count
- [ ] `questions_removed` = length of `removed_question_ids`
- [ ] Assessment status -> `"ready_for_distribution"` (check T-4)
- [ ] Pub/Sub event `assessorflow.human-review.approved` published

**Error cases:**
- [ ] Remove all questions -> 400

---

### T-15. Get Approved Questions

**Contract:** api_contract.md Section 2.4.3

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/approved-questions
```

**Expected:** 200 with `{questions[], count}`.

**Validation tests:**
- [ ] Only approved questions (removed ones not present)
- [ ] Answers stripped (`structured_answer`, `non_structured_model_answer` absent)
- [ ] `option_explanations` stripped
- [ ] `source_chunk_ids` stripped
- [ ] Each question has `sort_order`

---

### T-16. Send Invitations

**Contract:** api_contract.md Section 2.4.5

Send to specific participants:
```
POST {{base_url}}/api/v1/assessments/{{assessment_id}}/invite

{
  "participant_ids": ["{{participant_id_1}}", "{{participant_id_2}}"]
}
```

Send to all:
```
{
  "participant_ids": []
}
```

**Expected:** 200 with `{sent_to, count, status}`.

**Validation tests:**
- [ ] `status` = `"assessment_active"`
- [ ] `count` matches expected
- [ ] Assessment status -> `"assessment_active"` (check T-4)
- [ ] Pub/Sub event `assessorflow.invitation.sent` published

---

### T-17. Get Assessment Reports (Assessor)

**Contract:** api_contract.md Section 2.7.3

> **Prerequisite:** Reporting Agent must have created reports via gRPC (see T-G11).

```
GET {{base_url}}/api/v1/assessments/{{assessment_id}}/reports
```

**Expected:** 200 with `{assessment_id, reports[]}`.

**Validation tests:**
- [ ] Each report has `participant_email`, `total_score`, `max_score`, `report_content`

---

### T-18. Distribute Reports

**Contract:** api_contract.md Section 2.7.4

```
POST {{base_url}}/api/v1/assessments/{{assessment_id}}/reports/distribute
```

**Expected:** 200 with `{sent_to, count}`.

---

### T-19. Launch Assessment (Combined Endpoint)

**Not in contract** — convenience endpoint for frontend.

```
POST {{base_url}}/api/v1/assessments/launch
Content-Type: multipart/form-data

Form fields:
  config = {"assessment_title":"Quick Quiz","purpose":"exam_prep","duration_minutes":30,"difficulty_level":"medium","structured_question_count":5,"non_structured_question_count":0,"participants":["alice@test.com","bob@test.com"]}
  materials = [select file(s)]
  rubric = [select file, optional]
```

**Postman:** Body -> form-data:
- `config` (type: Text) = JSON string
- `materials` (type: File) = select file(s)
- `rubric` (type: File) = optional

**Expected:** 200 with `{assessment, workflow_id, materials_uploaded, participants_added, status}`.

**Validation tests:**
- [ ] Assessment created with correct config
- [ ] Materials uploaded to GCS
- [ ] Workflow started (`status` = `"material_validation"`)
- [ ] Pub/Sub event published

---

## Part 2: REST API — Participant Endpoints (Token Auth)

Participant endpoints use `?token=` query parameter instead of JWT.

**For testing:** The token is currently a placeholder — pass the `participant_id` directly as the token value.

---

### T-20. Access Assessment (Participant)

**Contract:** api_contract.md Section 2.5.1

```
GET {{base_url}}/api/v1/participate/{{assessment_id}}?token={{participant_id}}
```

**Expected:** 200 with assessment metadata.

**Validation tests:**
- [ ] `assessment_title`, `purpose`, `duration_minutes` populated
- [ ] `participant_id` matches token
- [ ] `participant_email` populated
- [ ] `total_questions` = structured + non_structured count
- [ ] `status` = `"ready"`

**Error cases:**
- [ ] Assessment not active -> 403
- [ ] Non-existent assessment -> 404
- [ ] Empty token -> 400

---

### T-21. Get Questions (Participant)

**Contract:** api_contract.md Section 2.5.2

```
GET {{base_url}}/api/v1/participate/{{assessment_id}}/questions?token={{participant_id}}
```

**Expected:** 200 with `{assessment_id, questions[]}`.

**Validation tests:**
- [ ] Each question has `id`, `question_type`, `content`, `sort_order`
- [ ] MCQ questions have `options` (A/B/C/D)
- [ ] NO `structured_answer` in any question
- [ ] NO `non_structured_model_answer` in any question
- [ ] NO `source_chunk_ids` in any question
- [ ] NO `metadata` in any question

---

### T-22. Submit Assessment (Participant)

**Contract:** api_contract.md Section 2.5.3

```
POST {{base_url}}/api/v1/participate/{{assessment_id}}/submit?token={{participant_id}}

{
  "answers": [
    {
      "question_id": "{{approved_question_id_1}}",
      "answer_content": "A"
    },
    {
      "question_id": "{{approved_question_id_2}}",
      "answer_content": "Encapsulation bundles data with methods and uses access modifiers to restrict direct access..."
    }
  ]
}
```

**Expected:** 200 with `{submission_id, status, answers_recorded}`.

**Validation tests:**
- [ ] `status` = `"submitted"`
- [ ] `answers_recorded` = number of answers sent
- [ ] Pub/Sub event `assessorflow.participant.submission-completed` published
- [ ] `participant_submissions.status` = `"submitted"` in DB

**Error cases:**
- [ ] Empty answers -> 400
- [ ] Invalid token -> 400

---

### T-23. View Report (Participant)

**Contract:** api_contract.md Section 2.7.2

> **Prerequisite:** Report must exist (created by Reporting Agent via gRPC T-G11).

```
GET {{base_url}}/api/v1/reports/{{report_id}}?token={{participant_id}}
```

**Expected:** 200 with full report data including `report_content` (per-question feedback + overall summary).

---

## Part 3: gRPC — Internal Agent Endpoints

**Host:** `localhost:9060`
**Tool:** Use [grpcurl](https://github.com/fullstorydev/grpcurl), [Postman gRPC](https://learning.postman.com/docs/sending-requests/grpc/grpc-request-interface/), or [BloomRPC](https://github.com/bloomrpc/bloomrpc).
**Proto:** `proto/assessorflow/submission/v1/submission.proto`

---

### T-G1. GetAssessmentConfig

**Caller:** Classification Agent, Q&A Gen Agent, Evaluator Agent

By assessment_id:
```json
{
  "assessment_id": "{{assessment_id}}"
}
```

By workflow_id:
```json
{
  "workflow_id": "{{workflow_id}}"
}
```

**Expected:** AssessmentConfig with all fields (id, title, purpose, difficulty, question counts, status).

---

### T-G2. GetMaterials

**Caller:** Validator Agent, Classification Agent

All materials:
```json
{
  "assessment_id": "{{assessment_id}}",
  "unvalidated_only": false
}
```

Unvalidated only:
```json
{
  "assessment_id": "{{assessment_id}}",
  "unvalidated_only": true
}
```

**Expected:** Array of MaterialInfo with file_name, storage_path, file_type, readiness_status, source.

---

### T-G3. CreateQuestionSet

**Caller:** Q&A Generation Agent (Phase 7)

```json
{
  "workflow_id": "{{workflow_id}}"
}
```

**Expected:** `{question_set_id, status: "generated"}`. Save as `{{question_set_id}}`.

---

### T-G4. WriteGeneratedQuestions

**Caller:** Q&A Generation Agent (Phase 7)

```json
{
  "question_set_id": "{{question_set_id}}",
  "questions": [
    {
      "question_type": "structured",
      "content": "Which OOP concept involves bundling data with the methods that operate on it?",
      "structured_answer": "A",
      "metadata_json": "{\"options\":{\"A\":\"Encapsulation\",\"B\":\"Polymorphism\",\"C\":\"Compilation\",\"D\":\"Iteration\"},\"source_chunk_ids\":[\"chunk_101\"],\"difficulty\":\"easy\",\"topic\":\"OOP\"}",
      "iteration": 1
    },
    {
      "question_type": "non_structured",
      "content": "Explain the key differences between encapsulation and abstraction with examples.",
      "non_structured_model_answer": "Encapsulation bundles data with methods and restricts access. Abstraction hides complexity...",
      "metadata_json": "{\"rubric\":\"Award marks for 2+ key differences\",\"max_marks\":10,\"source_chunk_ids\":[\"chunk_101\"],\"difficulty\":\"medium\",\"topic\":\"OOP\"}",
      "iteration": 1
    }
  ]
}
```

**Expected:** `{questions_written: 2, status: "success"}`.

---

### T-G5. GetGeneratedQuestionsWithAnswers

**Caller:** Evaluator Agent (Phase 7a)

```json
{
  "question_set_id": "{{question_set_id}}"
}
```

**Expected:** Array of questions WITH `structured_answer` and `non_structured_model_answer` included.

---

### T-G6. IncrementQuestionSetIteration

**Caller:** Q&A Generation Agent (Phase 7a feedback loop)

```json
{
  "question_set_id": "{{question_set_id}}"
}
```

**Expected:** `{question_set_id, iteration_count: 2, status: "generated"}`.

---

### T-G7. GetApprovedQuestionsWithAnswers

**Caller:** Evaluator Agent (Phase 10), Reporting Agent (Phase 11)

```json
{
  "assessment_id": "{{assessment_id}}"
}
```

**Expected:** Array of approved questions WITH answers and sort_order. Only questions that survived HITL.

---

### T-G8. CreateEvaluation

**Caller:** Evaluator Agent (Phase 10)

```json
{
  "workflow_id": "{{workflow_id}}",
  "participant_id": "{{participant_id}}",
  "submission_id": "{{submission_id}}",
  "total_score": 85.5,
  "max_score": 100.0,
  "details": [
    {
      "question_id": "{{approved_question_id_1}}",
      "score": 10.0,
      "max_score": 10.0,
      "evaluation_method": "deterministic"
    },
    {
      "question_id": "{{approved_question_id_2}}",
      "score": 15.0,
      "max_score": 20.0,
      "reasoning": "Good identification of two differences. Example of abstraction was incomplete.",
      "evaluation_method": "llm_based",
      "group_evaluation_id": "{{group_evaluation_id}}"
    }
  ]
}
```

**Expected:** `{evaluation_id, status: "completed"}`. Save as `{{evaluation_id}}`.

---

### T-G9. CreateGroupEvaluation

**Caller:** Evaluator Agent (Phase 10, for non-structured questions)

```json
{
  "workflow_id": "{{workflow_id}}",
  "group_id": "{{group_id}}",
  "question_id": "{{approved_question_id_2}}",
  "group_score": 15.0,
  "max_score": 20.0,
  "reasoning": "Group demonstrated strong understanding of encapsulation vs abstraction."
}
```

**Expected:** `{group_evaluation_id, status: "success"}`. Save as `{{group_evaluation_id}}`.

**Idempotency test:**
- [ ] Call again with same group_id + question_id -> returns existing record (no duplicate)

---

### T-G10. GetEvaluation

**Caller:** Reporting Agent (Phase 11)

```json
{
  "workflow_id": "{{workflow_id}}",
  "participant_id": "{{participant_id}}"
}
```

**Expected:** Evaluation with `{evaluation_id, total_score, max_score, status, details[]}`.

**Validation tests:**
- [ ] `details` array has one entry per question
- [ ] Each detail has `score`, `max_score`, `evaluation_method`

---

### T-G11. CreateReport

**Caller:** Reporting Agent (Phase 11)

```json
{
  "workflow_id": "{{workflow_id}}",
  "participant_id": "{{participant_id}}",
  "evaluation_id": "{{evaluation_id}}",
  "report_content_json": "{\"total_score\":85,\"max_score\":100,\"per_question_feedback\":[{\"question_id\":\"uuid\",\"question_type\":\"structured\",\"score\":10,\"max_score\":10,\"feedback\":\"Correct. Encapsulation bundles data with methods.\"}],\"overall_summary\":\"Strong understanding of OOP concepts.\"}"
}
```

**Expected:** `{report_id, status: "completed"}`. Save as `{{report_id}}`.

---

### T-G12. UploadWebResearchMaterials

**Caller:** Web Research Agent (Phase 5)

```json
{
  "assessment_id": "{{assessment_id}}",
  "files": [
    {
      "file_name": "web_research_oop.md",
      "storage_path": "gs://bucket/assessments/uuid/web_research_oop.md",
      "file_type": "md",
      "source_url": "https://example.com/oop-guide"
    }
  ]
}
```

**Expected:** `{materials_registered: 1, status: "success"}`.

**Validation test:**
- [ ] New material row in DB with `source = "web_research"` and `source_url` populated

---

### T-G13. GetGroupMemberSubmissions

**Caller:** Evaluator Agent (Phase 10, group-level evaluation)

```json
{
  "group_id": "{{group_id}}",
  "question_id": "{{approved_question_id_2}}"
}
```

**Expected:** `{group_id, group_name, question_id, question_text, submissions[]}`.

**Validation tests:**
- [ ] `submissions` contains one entry per group member
- [ ] Each entry has `participant_id`, `participant_email`, `answer_content`

---

### T-G14. UpdateAssessmentStatus

**Caller:** Orchestrator Agent

```json
{
  "assessment_id": "{{assessment_id}}",
  "status": "processing"
}
```

With workflow_id:
```json
{
  "assessment_id": "{{assessment_id}}",
  "status": "material_validation",
  "workflow_id": "wf_abc123def456"
}
```

**Expected:** `{status: "updated"}`.

**Validation test:**
- [ ] `assessment_configs.status` updated in DB

---

### T-G15. UpdateMaterialValidation

**Caller:** Validator Agent (Phase 3)

```json
{
  "material_id": "{{material_id}}",
  "readiness_status": "PROCEED",
  "validation_reason_code": "VALIDATION_PASSED",
  "validation_message": "Document is readable. Text extracted successfully."
}
```

Rejection:
```json
{
  "material_id": "{{material_id}}",
  "readiness_status": "TERMINATE",
  "validation_reason_code": "BLURRY_UNREADABLE",
  "validation_message": "Document is too blurry to extract text."
}
```

**Expected:** `{status: "updated"}`.

**Validation test:**
- [ ] `assessment_materials.readiness_status` updated in DB
- [ ] Validation polling (T-7) now shows updated status

---

### T-G16. StartWorkflow (gRPC)

**Caller:** Internal (alternative to REST T-10)

```json
{
  "assessment_id": "{{assessment_id}}"
}
```

**Expected:** `{workflow_id, correlation_id, status: "material_validation"}`.

---

## Part 4: End-to-End Workflow Test

Run these in order to simulate a complete assessment lifecycle:

| Step | Action | Test | Phase |
|------|--------|------|-------|
| 1 | Create assessment | T-1 | 2 |
| 2 | Upload material(s) | T-5 (repeat per file) | 2 |
| 3 | Upload rubric (optional) | T-8 | 2 |
| 4 | Start workflow | T-10 | 2->3 |
| 5 | Verify status = `material_validation` | T-4 | 3 |
| 6 | (Validator Agent) Update material validation | T-G15 | 3 |
| 7 | Verify validation status | T-7 | 3 |
| 8 | (Orchestrator) Update status -> `processing` | T-G14 | 4 |
| 9 | (Q&A Gen Agent) Create question set | T-G3 | 7 |
| 10 | (Q&A Gen Agent) Write generated questions | T-G4 | 7 |
| 11 | (Evaluator Agent) Read questions for validation | T-G5 | 7a |
| 12 | (Orchestrator) Update status -> `under_review` | T-G14 | 8 |
| 13 | Assessor reviews questions | T-12 | 8 |
| 14 | Assessor views topics | T-13 | 8 |
| 15 | Assessor approves questions | T-14 | 8 |
| 16 | Verify status = `ready_for_distribution` | T-4 | 8 |
| 17 | Verify approved questions | T-15 | 8 |
| 18 | Assessor sends invitations | T-16 | 8->9 |
| 19 | Verify status = `assessment_active` | T-4 | 9 |
| 20 | Participant accesses assessment | T-20 | 9 |
| 21 | Participant gets questions | T-21 | 9 |
| 22 | Participant submits answers | T-22 | 9 |
| 23 | (Evaluator) Get group submissions | T-G13 | 10 |
| 24 | (Evaluator) Create group evaluation | T-G9 | 10 |
| 25 | (Evaluator) Create evaluation | T-G8 | 10 |
| 26 | (Evaluator) Verify evaluation | T-G10 | 10 |
| 27 | (Reporting Agent) Create report | T-G11 | 11 |
| 28 | Assessor views reports | T-17 | 12 |
| 29 | Assessor distributes reports | T-18 | 12 |
| 30 | Participant views report | T-23 | 12 |

---

## Part 5: Health and Observability

### Health Check

```
GET {{base_url}}/health
```

**Expected:** 200 with `{status: "ok"}`.

### Readiness Check

```
GET {{base_url}}/ready
```

**Expected:** 200 with `{status: "ready"}` if DB is connected. 503 if not.

### Prometheus Metrics

```
GET {{base_url}}/metrics
```

**Expected:** 200 with Prometheus text format. Look for:
- `http_requests_total` — request counts by endpoint
- `http_request_duration_seconds` — latency histogram

---

## Part 6: Variable Reference

Save these Postman variables as you progress through testing:

| Variable | Source | Set At |
|----------|--------|--------|
| `{{token}}` | Identity Service login response | Prerequisites |
| `{{assessment_id}}` | T-1 response `assessment.id` | T-1 |
| `{{material_id}}` | T-5 response `material.id` | T-5 |
| `{{workflow_id}}` | T-10 response `workflow_id` | T-10 |
| `{{question_set_id}}` | T-G3 response `question_set_id` | T-G3 |
| `{{participant_id}}` | T-3 response `participants[0].id` | T-3 |
| `{{group_id}}` | T-3 response (query DB for group) | T-1 |
| `{{approved_question_id_1}}` | T-15 response `questions[0].id` | T-15 |
| `{{approved_question_id_2}}` | T-15 response `questions[1].id` | T-15 |
| `{{submission_id}}` | T-22 response `submission_id` | T-22 |
| `{{group_evaluation_id}}` | T-G9 response `group_evaluation_id` | T-G9 |
| `{{evaluation_id}}` | T-G8 response `evaluation_id` | T-G8 |
| `{{report_id}}` | T-G11 response `report_id` | T-G11 |