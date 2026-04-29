"""Configuration for the Assessment Submission Service."""

import os

# Database (af_submission)
DB_HOST = os.environ.get("SS_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("SS_DB_PORT", "15433"))
DB_NAME = os.environ.get("SS_DB_NAME", "af_submission")
DB_USER = os.environ.get("SS_DB_USER", "assessorflow")
DB_PASSWORD = os.environ.get("SS_DB_PASSWORD", "assessorflow_prod")

# Server
SERVICE_PORT = int(os.environ.get("SS_PORT", "8060"))
GRPC_PORT = int(os.environ.get("SS_GRPC_PORT", "9060"))

# Google Cloud Storage
GCS_BUCKET = os.environ.get("GCS_BUCKET", "assessorflow-materials")
GCS_CREDENTIALS_PATH = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

# Google Cloud Pub/Sub
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "aflow-491809")

# Identity Service (for JWT validation)
IDENTITY_GRPC_HOST = os.environ.get("IDENTITY_GRPC_HOST", "localhost:9090")
IDENTITY_JWKS_URL = os.environ.get(
    "IDENTITY_JWKS_URL", "http://localhost:8081/.well-known/jwks.json"
)
JWT_ISSUER = os.environ.get("JWT_ISSUER", "assessorflow")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "assessorflow-api")
