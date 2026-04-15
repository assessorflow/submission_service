"""Shared constants for the Assessment Submission Service."""


class AssessmentStatus:
    DRAFT = "draft"
    MATERIAL_VALIDATION = "material_validation"
    INSUFFICIENT = "insufficient"
    PROCESSING = "processing"
    READY_FOR_DISTRIBUTION = "ready_for_distribution"
    ASSESSMENT_ACTIVE = "assessment_active"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class InvitationStatus:
    PENDING = "pending"
    SENT = "sent"
    ACCEPTED = "accepted"


class QuestionSetStatus:
    GENERATED = "generated"
    VALIDATING = "validating"
    VALIDATED = "validated"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class SubmissionStatus:
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    EVALUATED = "evaluated"
    REPORTED = "reported"


class EvaluationStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


VALID_PURPOSES = {"topic_revision", "exam_prep", "skill_assessment"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
