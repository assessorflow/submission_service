-- Assessment Submission Service — DDL
-- Database: af_submission
-- Source of truth: /reference/schema.md Section 2
-- PostgreSQL 16+

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- assessment_configs
-- ============================================================================

CREATE TABLE assessment_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id VARCHAR(50) UNIQUE DEFAULT NULL,
    assessor_id UUID NOT NULL,
    assessment_title VARCHAR(255) NOT NULL,
    purpose VARCHAR(100) NOT NULL,
    duration_minutes INTEGER NOT NULL,
    difficulty_level VARCHAR(50) NOT NULL,
    structured_question_count INTEGER NOT NULL DEFAULT 0,
    non_structured_question_count INTEGER NOT NULL DEFAULT 0,
    deadline TIMESTAMPTZ DEFAULT NULL,
    web_research_mode VARCHAR(20) NOT NULL DEFAULT 'manual',
    status VARCHAR(50) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- assessment_participants
-- ============================================================================

CREATE TABLE assessment_participants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assessment_id UUID NOT NULL REFERENCES assessment_configs(id),
    email VARCHAR(255) NOT NULL,
    invitation_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(assessment_id, email)
);

-- ============================================================================
-- participant_groups
-- ============================================================================

CREATE TABLE participant_groups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assessment_id UUID NOT NULL REFERENCES assessment_configs(id),
    group_name VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(assessment_id, group_name)
);

-- ============================================================================
-- participant_group_members
-- ============================================================================

CREATE TABLE participant_group_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    group_id UUID NOT NULL REFERENCES participant_groups(id),
    participant_id UUID NOT NULL REFERENCES assessment_participants(id),
    UNIQUE(group_id, participant_id)
);

-- ============================================================================
-- assessment_materials
-- ============================================================================

CREATE TABLE assessment_materials (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assessment_id UUID NOT NULL REFERENCES assessment_configs(id),
    file_name VARCHAR(255) NOT NULL,
    storage_path VARCHAR(512) NOT NULL,
    file_type VARCHAR(50) NOT NULL,
    readiness_status VARCHAR(20) DEFAULT NULL,
    validation_reason_code VARCHAR(100) DEFAULT NULL,
    validation_message TEXT DEFAULT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'upload',
    source_url VARCHAR(1024) DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- assessment_rubrics
-- ============================================================================

CREATE TABLE assessment_rubrics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assessment_id UUID NOT NULL REFERENCES assessment_configs(id),
    file_name VARCHAR(255) NOT NULL,
    storage_path VARCHAR(512) NOT NULL,
    file_type VARCHAR(50) NOT NULL,
    readiness_status VARCHAR(20) DEFAULT NULL,
    validation_reason_code VARCHAR(100) DEFAULT NULL,
    validation_message TEXT DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- question_sets
-- ============================================================================

CREATE TABLE question_sets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id VARCHAR(50) NOT NULL,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'generated',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- generated_questions
-- ============================================================================

CREATE TABLE generated_questions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_set_id UUID NOT NULL REFERENCES question_sets(id),
    iteration INTEGER NOT NULL DEFAULT 1,
    question_type VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    structured_answer CHAR(1),
    non_structured_model_answer TEXT,
    metadata JSONB,
    topic_id UUID,
    was_approved BOOLEAN DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- approved_question_sets
-- ============================================================================

CREATE TABLE approved_question_sets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assessment_id UUID NOT NULL REFERENCES assessment_configs(id) UNIQUE,
    original_question_set_id UUID NOT NULL REFERENCES question_sets(id),
    approved_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- approved_questions
-- ============================================================================

CREATE TABLE approved_questions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_set_id UUID NOT NULL REFERENCES approved_question_sets(id),
    question_type VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    structured_answer CHAR(1),
    non_structured_model_answer TEXT,
    metadata JSONB,
    sort_order INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- participant_submissions
-- ============================================================================

CREATE TABLE participant_submissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assessment_id UUID NOT NULL REFERENCES assessment_configs(id),
    participant_id UUID NOT NULL REFERENCES assessment_participants(id),
    started_at TIMESTAMPTZ,
    submitted_at TIMESTAMPTZ,
    status VARCHAR(50) NOT NULL DEFAULT 'in_progress',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(assessment_id, participant_id)
);

-- ============================================================================
-- participant_answers
-- ============================================================================

CREATE TABLE participant_answers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    submission_id UUID NOT NULL REFERENCES participant_submissions(id),
    question_id UUID NOT NULL REFERENCES approved_questions(id),
    answer_content TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(submission_id, question_id)
);

-- ============================================================================
-- evaluations
-- ============================================================================

CREATE TABLE evaluations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id VARCHAR(50) NOT NULL,
    participant_id UUID NOT NULL,
    submission_id UUID NOT NULL REFERENCES participant_submissions(id),
    total_score DECIMAL(5,2),
    max_score DECIMAL(5,2),
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- group_evaluations
-- ============================================================================

CREATE TABLE group_evaluations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id VARCHAR(50) NOT NULL,
    group_id UUID NOT NULL REFERENCES participant_groups(id),
    question_id UUID NOT NULL REFERENCES approved_questions(id),
    group_score DECIMAL(5,2) NOT NULL,
    max_score DECIMAL(5,2) NOT NULL,
    reasoning TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(group_id, question_id)
);

-- ============================================================================
-- evaluation_details
-- ============================================================================

CREATE TABLE evaluation_details (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    evaluation_id UUID NOT NULL REFERENCES evaluations(id),
    question_id UUID NOT NULL REFERENCES approved_questions(id),
    group_evaluation_id UUID REFERENCES group_evaluations(id),
    score DECIMAL(5,2) NOT NULL,
    max_score DECIMAL(5,2) NOT NULL,
    reasoning TEXT,
    evaluation_method VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- participant_reports
-- ============================================================================

CREATE TABLE participant_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id VARCHAR(50) NOT NULL,
    participant_id UUID NOT NULL,
    evaluation_id UUID NOT NULL REFERENCES evaluations(id),
    report_content JSONB NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'generating',
    generated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- Indexes for common query patterns
-- ============================================================================

CREATE INDEX idx_assessment_configs_assessor ON assessment_configs(assessor_id);
CREATE INDEX idx_assessment_configs_status ON assessment_configs(status);
CREATE INDEX idx_assessment_configs_workflow ON assessment_configs(workflow_id);
CREATE INDEX idx_assessment_participants_assessment ON assessment_participants(assessment_id);
CREATE INDEX idx_assessment_materials_assessment ON assessment_materials(assessment_id);
CREATE INDEX idx_question_sets_workflow ON question_sets(workflow_id);
CREATE INDEX idx_generated_questions_set ON generated_questions(question_set_id);
CREATE INDEX idx_approved_questions_set ON approved_questions(question_set_id);
CREATE INDEX idx_participant_submissions_assessment ON participant_submissions(assessment_id);
CREATE INDEX idx_participant_answers_submission ON participant_answers(submission_id);
CREATE INDEX idx_evaluations_workflow ON evaluations(workflow_id);
CREATE INDEX idx_evaluation_details_evaluation ON evaluation_details(evaluation_id);
CREATE INDEX idx_participant_reports_workflow ON participant_reports(workflow_id);