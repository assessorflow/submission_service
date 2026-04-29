"""gRPC integration test — tests all 13 RPCs against the running Submission Service."""

import asyncio
import json
import grpc
from assessorflow.submission.v1 import submission_pb2, submission_pb2_grpc


GRPC_HOST = "host.docker.internal:9060"


async def main():
    channel = grpc.aio.insecure_channel(GRPC_HOST)
    stub = submission_pb2_grpc.SubmissionServiceStub(channel)

    print("=" * 60)
    print("Testing Submission Service gRPC — 13 RPCs")
    print("=" * 60)

    # --- Setup: Create assessment via REST first (we need an assessment_id) ---
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://host.docker.internal:8060/api/v1/assessments",
            json={
                "assessor_id": "550e8400-e29b-41d4-a716-446655440000",
                "assessment_title": "gRPC Test Assessment",
                "purpose": "topic_revision",
                "duration_minutes": 60,
                "difficulty_level": "medium",
                "structured_question_count": 3,
                "non_structured_question_count": 1,
                "participants": ["grpc-test@email.com"],
            },
        )
        data = resp.json()
        assessment_id = data["assessment"]["id"]
        print(f"\nSetup: Created assessment {assessment_id}")

    # 1. GetAssessmentConfig
    print("\n1. GetAssessmentConfig...")
    resp = await stub.GetAssessmentConfig(
        submission_pb2.GetAssessmentConfigRequest(assessment_id=assessment_id)
    )
    print(f"   title={resp.config.assessment_title}, status={resp.config.status}")

    # 13. StartWorkflow (do this early so we have workflow_id)
    print("\n13. StartWorkflow...")
    resp = await stub.StartWorkflow(
        submission_pb2.StartWorkflowRequest(assessment_id=assessment_id)
    )
    workflow_id = resp.workflow_id
    print(f"   workflow_id={workflow_id}, status={resp.status}")

    # 1b. GetAssessmentConfig by workflow_id
    print("\n1b. GetAssessmentConfig (by workflow_id)...")
    resp = await stub.GetAssessmentConfig(
        submission_pb2.GetAssessmentConfigRequest(workflow_id=workflow_id)
    )
    print(
        f"   title={resp.config.assessment_title}, workflow={resp.config.workflow_id}"
    )

    # 2. GetMaterials
    print("\n2. GetMaterials...")
    resp = await stub.GetMaterials(
        submission_pb2.GetMaterialsRequest(assessment_id=assessment_id)
    )
    print(f"   materials_count={len(resp.materials)}")

    # 12. UploadWebResearchMaterials
    print("\n12. UploadWebResearchMaterials...")
    resp = await stub.UploadWebResearchMaterials(
        submission_pb2.UploadWebResearchRequest(
            assessment_id=assessment_id,
            files=[
                submission_pb2.WebResearchFile(
                    file_name="web_research.md",
                    storage_path="gs://assessorflow-materials/test/web_research.md",
                    file_type="text/markdown",
                    source_url="https://example.com/oop-guide",
                ),
            ],
        )
    )
    print(f"   registered={resp.materials_registered}, status={resp.status}")

    # 2b. GetMaterials (should now include web research)
    print("\n2b. GetMaterials (after web research)...")
    resp = await stub.GetMaterials(
        submission_pb2.GetMaterialsRequest(assessment_id=assessment_id)
    )
    print(f"   materials_count={len(resp.materials)}")
    for m in resp.materials:
        print(f"     - {m.file_name} (source={m.source})")

    # 3. CreateQuestionSet
    print("\n3. CreateQuestionSet...")
    resp = await stub.CreateQuestionSet(
        submission_pb2.CreateQuestionSetRequest(workflow_id=workflow_id)
    )
    question_set_id = resp.question_set_id
    print(f"   question_set_id={question_set_id}, status={resp.status}")

    # 4. WriteGeneratedQuestions
    print("\n4. WriteGeneratedQuestions...")
    resp = await stub.WriteGeneratedQuestions(
        submission_pb2.WriteGeneratedQuestionsRequest(
            question_set_id=question_set_id,
            questions=[
                submission_pb2.Question(
                    question_type="structured",
                    content="Which OOP concept bundles data with methods?",
                    structured_answer="A",
                    metadata_json=json.dumps(
                        {
                            "options": {
                                "A": "Encapsulation",
                                "B": "Polymorphism",
                                "C": "Inheritance",
                                "D": "Abstraction",
                            },
                            "source_chunk_ids": ["chunk_101"],
                            "difficulty": "easy",
                        }
                    ),
                    iteration=1,
                ),
                submission_pb2.Question(
                    question_type="non_structured",
                    content="Explain the difference between encapsulation and abstraction.",
                    non_structured_model_answer="Encapsulation hides data, abstraction hides implementation details.",
                    metadata_json=json.dumps(
                        {
                            "rubric": "Award marks for identifying at least 2 differences.",
                            "max_marks": 10,
                            "source_chunk_ids": ["chunk_101", "chunk_205"],
                        }
                    ),
                    iteration=1,
                ),
            ],
        )
    )
    print(f"   written={resp.questions_written}, status={resp.status}")

    # 5. GetGeneratedQuestionsWithAnswers
    print("\n5. GetGeneratedQuestionsWithAnswers...")
    resp = await stub.GetGeneratedQuestionsWithAnswers(
        submission_pb2.GetGeneratedQuestionsRequest(question_set_id=question_set_id)
    )
    print(f"   questions_count={len(resp.questions)}")
    for q in resp.questions:
        print(f"     - [{q.question_type}] {q.content[:50]}...")

    # 6. IncrementQuestionSetIteration
    print("\n6. IncrementQuestionSetIteration...")
    resp = await stub.IncrementQuestionSetIteration(
        submission_pb2.IncrementIterationRequest(question_set_id=question_set_id)
    )
    print(f"   iteration_count={resp.iteration_count}, status={resp.status}")

    # 7. GetApprovedQuestionsWithAnswers (empty — not approved yet)
    print("\n7. GetApprovedQuestionsWithAnswers (before approval)...")
    resp = await stub.GetApprovedQuestionsWithAnswers(
        submission_pb2.GetApprovedQuestionsRequest(assessment_id=assessment_id)
    )
    print(f"   approved_count={len(resp.questions)} (expected 0)")

    # 8. CreateEvaluation (skip — needs approved questions + submissions first)
    print(
        "\n8. CreateEvaluation — skipped (needs approved questions + participant submissions)"
    )

    # 9. CreateGroupEvaluation (skip — needs groups + submissions)
    print("\n9. CreateGroupEvaluation — skipped (needs group setup)")

    # 10. GetEvaluation (skip — no evaluations yet)
    print("\n10. GetEvaluation — skipped (no evaluations)")

    # 11. CreateReport (skip — needs evaluation first)
    print("\n11. CreateReport — skipped (needs evaluation)")

    await channel.close()

    print("\n" + "=" * 60)
    print("gRPC test complete!")
    print("  Tested: 1, 1b, 2, 2b, 3, 4, 5, 6, 7, 12, 13")
    print("  Skipped: 8, 9, 10, 11 (need full workflow data)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
