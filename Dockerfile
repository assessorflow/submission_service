FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY proto/ proto/

# Install dependencies
RUN pip install --no-cache-dir .

# Generate gRPC stubs from local proto files
RUN python -m grpc_tools.protoc \
    -Iproto \
    --python_out=grpc-stubs \
    --grpc_python_out=grpc-stubs \
    assessorflow/submission/v1/submission.proto \
    && find grpc-stubs -type d -exec touch {}/__init__.py \;

# GCS credentials must be mounted at runtime via K8s secret volume
# or use Workload Identity — never bake into the image

ENV PYTHONPATH=/app/src:/app/grpc-stubs
EXPOSE 8060 9060

HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD curl -f http://localhost:8060/health || exit 1

CMD ["uvicorn", "submission_service.main:app", "--host", "0.0.0.0", "--port", "8060"]
