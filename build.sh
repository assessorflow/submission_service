#!/bin/bash
# Build script for Assessment Submission Service
# Generates Python gRPC stubs from local proto files (no grpc-registry dependency)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROTO_DIR="$SCRIPT_DIR/proto"
GEN_DIR="$SCRIPT_DIR/grpc-stubs"

# Step 1: Generate Python stubs from local .proto files
echo "Generating Python gRPC stubs..."
rm -rf "$GEN_DIR"
mkdir -p "$GEN_DIR"

python -m grpc_tools.protoc \
  -I"$PROTO_DIR" \
  --python_out="$GEN_DIR" \
  --grpc_python_out="$GEN_DIR" \
  assessorflow/submission/v1/submission.proto

# Add __init__.py files for Python imports
find "$GEN_DIR" -type d -exec touch {}/__init__.py \;

echo "Stubs generated at: $GEN_DIR"

# Step 2: Build Docker image (optional — pass --docker flag)
if [ "$1" = "--docker" ]; then
  echo "Building Docker image..."
  docker build -t submission-service -f "$SCRIPT_DIR/Dockerfile" "$SCRIPT_DIR"
  echo ""
  echo "Done. Run with:"
  echo "  docker run -d --name submission-service -p 8060:8060 -p 9060:9060 --env-file .env -e SS_DB_HOST=host.docker.internal submission-service"
fi
