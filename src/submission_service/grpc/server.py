"""gRPC server for Assessment Submission Service.

All 13 internal RPCs delegate to the same repository that REST endpoints use.
"""

from __future__ import annotations

import json

import grpc
import structlog

from submission_service import config
from assessorflow.submission.v1 import submission_pb2, submission_pb2_grpc
from submission_service.db import repository as repo
from submission_service.grpc.interceptor import LoggingInterceptor

logger = structlog.get_logger(__name__)


class SubmissionServiceServicer(submission_pb2_grpc.SubmissionServiceServicer):
    """Implements all 13 gRPC RPCs defined in submission.proto."""

    # 1. GetAssessmentConfig
    async def GetAssessmentConfig(self, request, context):
        if request.workflow_id:
            result = await repo.get_assessment_config_by_workflow(request.workflow_id)
        else:
            result = await repo.get_assessment_config(request.assessment_id)

        if not result:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Assessment not found")
            return submission_pb2.GetAssessmentConfigResponse()

        return submission_pb2.GetAssessmentConfigResponse(
            config=_dict_to_config_proto(result)
        )

    # 2. GetMaterials
    async def GetMaterials(self, request, context):
        materials = await repo.get_materials(
            request.assessment_id, unvalidated_only=request.unvalidated_only
        )
        return submission_pb2.GetMaterialsResponse(
            materials=[_dict_to_material_proto(m) for m in materials]
        )

    # 3. CreateQuestionSet
    async def CreateQuestionSet(self, request, context):
        result = await repo.create_question_set(request.workflow_id)
        return submission_pb2.CreateQuestionSetResponse(
            question_set_id=result["id"],
            status=result.get("status", "generated"),
        )

    # 4. WriteGeneratedQuestions
    async def WriteGeneratedQuestions(self, request, context):
        questions = [
            {
                "question_type": q.question_type,
                "content": q.content,
                "structured_answer": q.structured_answer or None,
                "non_structured_model_answer": q.non_structured_model_answer or None,
                "metadata": json.loads(q.metadata_json) if q.metadata_json else None,
                "topic_id": q.topic_id or None,
                "iteration": q.iteration or 1,
            }
            for q in request.questions
        ]
        results = await repo.write_generated_questions(request.question_set_id, questions)
        return submission_pb2.WriteGeneratedQuestionsResponse(
            questions_written=len(results),
            status="success",
        )

    # 5. GetGeneratedQuestionsWithAnswers
    async def GetGeneratedQuestionsWithAnswers(self, request, context):
        results = await repo.get_generated_questions_with_answers(request.question_set_id)
        return submission_pb2.GetGeneratedQuestionsResponse(
            questions=[_dict_to_question_proto(q) for q in results]
        )

    # 6. IncrementQuestionSetIteration
    async def IncrementQuestionSetIteration(self, request, context):
        result = await repo.increment_question_set_iteration(request.question_set_id)
        return submission_pb2.IncrementIterationResponse(
            question_set_id=result["id"],
            iteration_count=result.get("iteration_count", 0),
            status=result.get("status", "generated"),
        )

    # 7. GetApprovedQuestionsWithAnswers
    async def GetApprovedQuestionsWithAnswers(self, request, context):
        results = await repo.get_approved_questions_with_answers(request.assessment_id)
        return submission_pb2.GetApprovedQuestionsResponse(
            questions=[_dict_to_question_proto(q) for q in results]
        )

    # 8. CreateEvaluation
    async def CreateEvaluation(self, request, context):
        result = await repo.create_evaluation(
            workflow_id=request.workflow_id,
            participant_id=request.participant_id,
            submission_id=request.submission_id,
        )
        if request.details:
            details = [
                {
                    "question_id": d.question_id,
                    "group_evaluation_id": d.group_evaluation_id or None,
                    "score": d.score,
                    "max_score": d.max_score,
                    "reasoning": d.reasoning or None,
                    "evaluation_method": d.evaluation_method,
                }
                for d in request.details
            ]
            await repo.write_evaluation_details(
                result["id"], details, request.total_score, request.max_score
            )
        return submission_pb2.CreateEvaluationResponse(
            evaluation_id=result["id"],
            status="completed" if request.details else "in_progress",
        )

    # 9. CreateGroupEvaluation
    async def CreateGroupEvaluation(self, request, context):
        result = await repo.create_group_evaluation(
            workflow_id=request.workflow_id,
            group_id=request.group_id,
            question_id=request.question_id,
            group_score=request.group_score,
            max_score=request.max_score,
            reasoning=request.reasoning or None,
        )
        return submission_pb2.CreateGroupEvaluationResponse(
            group_evaluation_id=result["id"],
            status="success",
        )

    # 10. GetEvaluation
    async def GetEvaluation(self, request, context):
        result = await repo.get_evaluation(request.workflow_id, request.participant_id)
        if not result:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Evaluation not found")
            return submission_pb2.GetEvaluationResponse()

        details = []
        raw_details = result.get("details")
        if raw_details and isinstance(raw_details, list):
            for d in raw_details:
                if isinstance(d, dict) and d.get("id"):
                    details.append(submission_pb2.EvaluationDetail(
                        question_id=str(d.get("question_id", "")),
                        group_evaluation_id=str(d["group_evaluation_id"]) if d.get("group_evaluation_id") else "",
                        score=float(d.get("score", 0)),
                        max_score=float(d.get("max_score", 0)),
                        reasoning=d.get("reasoning") or "",
                        evaluation_method=d.get("evaluation_method", ""),
                    ))

        return submission_pb2.GetEvaluationResponse(
            evaluation_id=result["id"],
            total_score=float(result.get("total_score") or 0),
            max_score=float(result.get("max_score") or 0),
            status=result.get("status", ""),
            details=details,
        )

    # 11. CreateReport
    async def CreateReport(self, request, context):
        report_content = json.loads(request.report_content_json) if request.report_content_json else {}
        result = await repo.create_report(
            workflow_id=request.workflow_id,
            participant_id=request.participant_id,
            evaluation_id=request.evaluation_id,
            report_content=report_content,
        )
        return submission_pb2.CreateReportResponse(
            report_id=result["id"],
            status="completed",
        )

    # 12. UploadWebResearchMaterials
    async def UploadWebResearchMaterials(self, request, context):
        registered = 0
        for f in request.files:
            await repo.add_material(
                assessment_id=request.assessment_id,
                file_name=f.file_name,
                storage_path=f.storage_path,
                file_type=f.file_type,
                source="web_research",
                source_url=f.source_url or None,
            )
            registered += 1
        return submission_pb2.UploadWebResearchResponse(
            materials_registered=registered,
            status="success",
        )

    # 13. StartWorkflow
    async def StartWorkflow(self, request, context):
        from submission_service.services import pubsub
        import uuid

        assessment = await repo.get_assessment_config(request.assessment_id)
        if not assessment:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Assessment not found")
            return submission_pb2.StartWorkflowResponse()

        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        correlation_id = f"corr_{uuid.uuid4().hex[:12]}"

        await repo.update_assessment_status(
            request.assessment_id, "material_validation", workflow_id=workflow_id
        )
        await pubsub.publish_workflow_start(
            workflow_id=workflow_id,
            assessment_id=request.assessment_id,
            correlation_id=correlation_id,
        )

        return submission_pb2.StartWorkflowResponse(
            workflow_id=workflow_id,
            correlation_id=correlation_id,
            status="material_validation",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dict_to_config_proto(d: dict) -> submission_pb2.AssessmentConfig:
    return submission_pb2.AssessmentConfig(
        assessment_id=str(d.get("id", "")),
        workflow_id=d.get("workflow_id") or "",
        assessor_id=str(d.get("assessor_id", "")),
        assessment_title=d.get("assessment_title", ""),
        purpose=d.get("purpose", ""),
        duration_minutes=d.get("duration_minutes", 0),
        difficulty_level=d.get("difficulty_level", ""),
        structured_question_count=d.get("structured_question_count", 0),
        non_structured_question_count=d.get("non_structured_question_count", 0),
        web_research_mode=d.get("web_research_mode", "manual"),
        status=d.get("status", "draft"),
        deadline=str(d["deadline"]) if d.get("deadline") else "",
    )


def _dict_to_material_proto(d: dict) -> submission_pb2.MaterialInfo:
    return submission_pb2.MaterialInfo(
        material_id=str(d.get("id", "")),
        file_name=d.get("file_name", ""),
        storage_path=d.get("storage_path", ""),
        file_type=d.get("file_type", ""),
        readiness_status=d.get("readiness_status") or "",
        source=d.get("source", "upload"),
        source_url=d.get("source_url") or "",
        validation_reason_code=d.get("validation_reason_code") or "",
        validation_message=d.get("validation_message") or "",
    )


def _dict_to_question_proto(d: dict) -> submission_pb2.Question:
    metadata = d.get("metadata")
    metadata_json = json.dumps(metadata) if metadata else ""
    return submission_pb2.Question(
        question_id=str(d.get("id", "")),
        question_type=d.get("question_type", ""),
        content=d.get("content", ""),
        structured_answer=d.get("structured_answer") or "",
        non_structured_model_answer=d.get("non_structured_model_answer") or "",
        metadata_json=metadata_json,
        topic_id=str(d["topic_id"]) if d.get("topic_id") else "",
        iteration=d.get("iteration", 1),
        sort_order=d.get("sort_order", 0),
    )


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def start_grpc_server() -> grpc.aio.Server:
    server = grpc.aio.server(interceptors=[LoggingInterceptor()])
    submission_pb2_grpc.add_SubmissionServiceServicer_to_server(
        SubmissionServiceServicer(), server
    )
    server.add_insecure_port(f"0.0.0.0:{config.GRPC_PORT}")
    await server.start()
    logger.info("grpc_server_started", port=config.GRPC_PORT)
    return server


async def stop_grpc_server(server: grpc.aio.Server) -> None:
    await server.stop(grace=30)
    logger.info("grpc_server_stopped")
