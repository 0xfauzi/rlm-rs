#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
S3_BUCKET="${S3_BUCKET:-rlm-local}"
DDB_TABLE_PREFIX="${DDB_TABLE_PREFIX:-rlm}"
LOCALSTACK_ENDPOINT_URL="${LOCALSTACK_ENDPOINT_URL:-${AWS_ENDPOINT_URL:-http://localhost:4566}}"

export AWS_REGION
export AWS_DEFAULT_REGION="${AWS_REGION}"

if ! command -v awslocal >/dev/null 2>&1 && ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: awslocal or aws CLI is required." >&2
  exit 1
fi

aws_cli() {
  if command -v awslocal >/dev/null 2>&1; then
    awslocal "$@"
  else
    aws --endpoint-url "${LOCALSTACK_ENDPOINT_URL}" "$@"
  fi
}

table_name() {
  local suffix="$1"
  if [[ -n "${DDB_TABLE_PREFIX}" ]]; then
    echo "${DDB_TABLE_PREFIX}_${suffix}"
  else
    echo "${suffix}"
  fi
}

ensure_bucket() {
  if aws_cli s3api head-bucket --bucket "${S3_BUCKET}" >/dev/null 2>&1; then
    echo "S3 bucket exists: ${S3_BUCKET}"
    return
  fi

  aws_cli s3 mb "s3://${S3_BUCKET}" >/dev/null
  echo "Created S3 bucket: ${S3_BUCKET}"
}

ensure_table() {
  local name="$1"

  if aws_cli dynamodb describe-table --table-name "${name}" >/dev/null 2>&1; then
    echo "DynamoDB table exists: ${name}"
    return
  fi

  aws_cli dynamodb create-table \
    --table-name "${name}" \
    --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
    --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST >/dev/null

  aws_cli dynamodb wait table-exists --table-name "${name}"
  echo "Created DynamoDB table: ${name}"
}

ensure_bucket
ensure_table "$(table_name "sessions")"
ensure_table "$(table_name "documents")"
ensure_table "$(table_name "executions")"
ensure_table "$(table_name "execution_state")"
ensure_table "$(table_name "api_keys")"
ensure_table "$(table_name "audit_log")"
