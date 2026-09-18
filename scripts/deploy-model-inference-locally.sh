#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/deploy-model-inference-locally.sh [pulumi-stack-ref]

The stack may also be supplied through VI_MODEL_INFERENCE_PULUMI_STACK.

Example:
  AWS_PROFILE=cfa-bootstrap \
    scripts/deploy-model-inference-locally.sh \
    tech-codeforafrica-org/cfa-platform-infra-vi-model-inference/prod

Environment overrides:
  AWS_PROFILE          AWS CLI profile. Defaults to cfa-bootstrap.
  APP_IMAGE            Existing image reference; skips the local image build.
  FORCE_REBUILD        true to ignore an existing source-fingerprint image.
  DOCKER_PLATFORM      Must match the host; defaults to linux/amd64.
  SKIP_HEALTH_CHECK    true to skip /healthz and /readyz checks.
  READY_ATTEMPTS       Readiness attempts. Defaults to 120 (30 minutes).
USAGE
}

log() { printf '[vi-deploy] %s\n' "$*" >&2; }
fail() { log "ERROR - $*"; exit 1; }
require_tool() { command -v "$1" >/dev/null 2>&1 || fail "required tool not found: $1"; }

require_docker_daemon() {
  local output="$WORK_DIR/docker-info.txt" pid
  docker info >"$output" 2>&1 &
  pid="$!"
  for _ in $(seq 1 15); do
    if ! kill -0 "$pid" 2>/dev/null; then
      if wait "$pid"; then
        return 0
      fi
      cat "$output" >&2
      fail "Docker is installed but its daemon is unavailable."
    fi
    sleep 1
  done
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  fail "Docker did not respond within 15 seconds. Start Docker Desktop and retry."
}

output_value() {
  jq -r --arg key "$1" '.[$key] // ""' <<<"$PULUMI_OUTPUTS"
}

required_output() {
  local key="$1" value
  value="$(output_value "$key")"
  [[ -n "$value" && "$value" != "null" ]] || fail "Pulumi output '$key' is required."
  printf '%s' "$value"
}

optional_output() {
  local value
  value="$(output_value "$1")"
  [[ "$value" != "null" ]] || value=""
  printf '%s' "$value"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

sha256_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    shasum -a 256 | awk '{print $1}'
  fi
}

compute_build_fingerprint() {
  local input="$WORK_DIR/build-fingerprint.txt"
  (
    cd "$REPO_ROOT"
    {
      printf 'dockerfile=%s\n' "$DOCKERFILE_PATH"
      printf 'build_context=%s\n' "$BUILD_CONTEXT"
      printf 'docker_platform=%s\n' "$DOCKER_PLATFORM"
      git ls-files -z --cached --modified --others --exclude-standard -- \
        "$BUILD_CONTEXT" "$DOCKERFILE_PATH" .dockerignore |
        sort -z |
        while IFS= read -r -d '' path; do
          [[ -f "$path" ]] && printf 'file:%s:%s\n' "$path" "$(sha256_file "$path")"
        done
    } > "$input"
  )
  sha256_stdin < "$input"
}

resolve_host() {
  local resolved=""
  if [[ -n "$DOKKU_HOST_NAME_TAG" ]]; then
    log "Resolving the Dokku host with Name tag '$DOKKU_HOST_NAME_TAG'."
    resolved="$(aws ec2 describe-instances \
      --region "$AWS_REGION" \
      --filters "Name=tag:Name,Values=$DOKKU_HOST_NAME_TAG" "Name=instance-state-name,Values=running" \
      --query 'sort_by(Reservations[].Instances[], &LaunchTime)[-1].InstanceId' \
      --output text 2>/dev/null || true)"
  fi
  if [[ -z "$resolved" || "$resolved" == "None" ]]; then
    resolved="$DOKKU_HOST_INSTANCE_ID"
  fi
  [[ -n "$resolved" && "$resolved" != "None" ]] || fail "no running Dokku host could be resolved."
  printf '%s' "$resolved"
}

render_host_release() {
  local body="$WORK_DIR/release-body.sh"
  HOST_SCRIPT="$WORK_DIR/release.sh"

  cat > "$body" <<'HOST_SCRIPT_BODY'
log() { printf '[host] %s\n' "$*"; }
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

for tool in aws docker dokku; do
  command -v "$tool" >/dev/null 2>&1 || { log "ERROR - required host tool missing: $tool"; exit 1; }
done

log "Logging into ECR for the release."
ecr_password="$(aws ecr get-login-password --region "$AWS_REGION")"
printf '%s' "$ecr_password" | docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null
if id dokku >/dev/null 2>&1; then
  dokku_home="$(getent passwd dokku | cut -d: -f6)"
  install -d -m 700 -o dokku -g dokku "$dokku_home/.docker"
  printf '%s' "$ecr_password" | sudo -H -u dokku docker login \
    --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null
fi
unset ecr_password

local_image="dokku-local-${DOKKU_APP_NAME}:release-$(date +%s)"
log "Pulling immutable image $APP_IMAGE."
docker pull "$APP_IMAGE" >/dev/null
docker tag "$APP_IMAGE" "$local_image"
docker rmi "$APP_IMAGE" >/dev/null 2>&1 || true

log "Deploying $DOKKU_APP_NAME from the prepared local image."
set +e
deploy_output="$(dokku --app "$DOKKU_APP_NAME" git:from-image "$local_image" 2>&1)"
deploy_status="$?"
set -e
if [[ "$deploy_status" -ne 0 ]] && grep -q 'No changes detected' <<<"$deploy_output"; then
  log "The image is already deployed; rebuilding the existing Dokku app."
  dokku --app "$DOKKU_APP_NAME" ps:rebuild
elif [[ "$deploy_status" -ne 0 ]]; then
  printf '%s\n' "$deploy_output" >&2
  dokku --app "$DOKKU_APP_NAME" ps:report || true
  dokku --app "$DOKKU_APP_NAME" logs --num 150 || true
  exit "$deploy_status"
else
  printf '%s\n' "$deploy_output" | tail -n 120
fi

log "Deployment command completed for $DOKKU_APP_NAME."
HOST_SCRIPT_BODY

  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    for variable in APP_IMAGE AWS_REGION DOKKU_APP_NAME ECR_REGISTRY; do
      printf 'export %s=%q\n' "$variable" "${!variable}"
    done
    cat "$body"
  } > "$HOST_SCRIPT"
  chmod +x "$HOST_SCRIPT"
  bash -n "$HOST_SCRIPT"
}

run_host_release() {
  local parameters="$WORK_DIR/ssm-parameters.json" encoded command_id invocation status
  encoded="$(base64 < "$HOST_SCRIPT" | tr -d '\n')"
  jq -n --arg script "$encoded" \
    '{commands:["printf '\''%s'\'' \"" + $script + "\" | base64 -d >/tmp/vi-model-inference-release.sh","chmod +x /tmp/vi-model-inference-release.sh","/tmp/vi-model-inference-release.sh"]}' \
    > "$parameters"

  log "Releasing through SSM on host $RESOLVED_HOST_ID."
  command_id="$(aws ssm send-command \
    --region "$AWS_REGION" \
    --instance-ids "$RESOLVED_HOST_ID" \
    --document-name AWS-RunShellScript \
    --comment 'Deploy VI model inference to Dokku' \
    --parameters "file://$parameters" \
    --query 'Command.CommandId' \
    --output text)"
  log "SSM command accepted: $command_id"

  for attempt in $(seq 1 360); do
    invocation="$(aws ssm get-command-invocation \
      --region "$AWS_REGION" --command-id "$command_id" \
      --instance-id "$RESOLVED_HOST_ID" --output json 2>/dev/null || true)"
    if [[ -z "$invocation" ]]; then
      sleep 5
      continue
    fi
    status="$(jq -r '.Status' <<<"$invocation")"
    case "$status" in
      Success)
        jq -r '.StandardOutputContent' <<<"$invocation"
        jq -r '.StandardErrorContent' <<<"$invocation" >&2
        return 0
        ;;
      Failed|Cancelled|TimedOut|Cancelling)
        jq -r '.StandardOutputContent' <<<"$invocation"
        jq -r '.StandardErrorContent' <<<"$invocation" >&2
        fail "host release failed with SSM status $status."
        ;;
      Pending|InProgress|Delayed)
        (( attempt % 12 == 0 )) && log "Host release still running (~$((attempt * 5 / 60))m)."
        ;;
    esac
    [[ "$attempt" -lt 360 ]] || fail "timed out waiting for the host release."
    sleep 5
  done
}

wait_for_endpoint() {
  local name="$1" url="$2" attempts="$3" delay="$4" status
  for attempt in $(seq 1 "$attempts"); do
    status="$(curl -sS -o "$WORK_DIR/${name}.json" -w '%{http_code}' --max-time 15 "$url" || true)"
    if [[ "$status" == "200" ]]; then
      log "OK - $name returned 200: $(tr -d '\n' < "$WORK_DIR/${name}.json")"
      return 0
    fi
    (( attempt % 4 == 0 || attempt == 1 )) && \
      log "Waiting for $name (attempt $attempt/$attempts, HTTP ${status:-unreachable})."
    sleep "$delay"
  done
  [[ ! -f "$WORK_DIR/${name}.json" ]] || cat "$WORK_DIR/${name}.json" >&2
  fail "$name did not become healthy: $url"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then usage; exit 0; fi
STACK_REF="${1:-${VI_MODEL_INFERENCE_PULUMI_STACK:-}}"
[[ -n "$STACK_REF" ]] || { usage; fail "a Pulumi stack reference is required."; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

export AWS_PROFILE="${AWS_PROFILE:-cfa-bootstrap}"
BUILD_CONTEXT="${BUILD_CONTEXT:-.}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-Dockerfile.classifier}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
FORCE_REBUILD="${FORCE_REBUILD:-false}"
SKIP_HEALTH_CHECK="${SKIP_HEALTH_CHECK:-false}"
READY_ATTEMPTS="${READY_ATTEMPTS:-120}"
APP_IMAGE="${APP_IMAGE:-}"

for tool in aws base64 curl git jq pulumi; do require_tool "$tool"; done
[[ -f "$REPO_ROOT/$DOCKERFILE_PATH" ]] || fail "Dockerfile not found: $DOCKERFILE_PATH"
[[ -d "$REPO_ROOT/$BUILD_CONTEXT" ]] || fail "build context not found: $BUILD_CONTEXT"
[[ "$DOCKER_PLATFORM" == "linux/amd64" ]] || fail "the provisioned host requires DOCKER_PLATFORM=linux/amd64."

log "Reading live infrastructure values from Pulumi stack '$STACK_REF'."
PULUMI_OUTPUTS="$(pulumi stack output --json --stack "$STACK_REF")"
[[ -n "$PULUMI_OUTPUTS" ]] || fail "Pulumi returned no stack outputs."
[[ "$(required_output viModelInferenceDeploymentMode)" == "ec2-dokku" ]] || \
  fail "the selected stack is not an EC2 Dokku deployment."

AWS_REGION="$(required_output awsRegion)"
ECR_REPOSITORY="$(required_output viModelInferenceRepositoryName)"
DOKKU_APP_NAME="$(required_output viModelInferenceDokkuAppName)"
DOKKU_APP_URL="$(required_output viModelInferenceDokkuAppUrl)"
DOKKU_HOST_NAME_TAG="$(optional_output viModelInferenceDokkuHostNameTag)"
DOKKU_HOST_INSTANCE_ID="$(optional_output viModelInferenceDokkuHostInstanceId)"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

RESOLVED_HOST_ID="$(resolve_host)"
host_arch="$(aws ec2 describe-instances --region "$AWS_REGION" --instance-ids "$RESOLVED_HOST_ID" \
  --query 'Reservations[0].Instances[0].Architecture' --output text)"
[[ "$host_arch" == "x86_64" ]] || fail "host architecture '$host_arch' does not match linux/amd64."

account_id="$(aws sts get-caller-identity --query Account --output text)"
ECR_REGISTRY="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com"

if [[ -z "$APP_IMAGE" ]]; then
  require_tool docker
  require_docker_daemon
  log "Logging into ECR $ECR_REGISTRY."
  aws ecr get-login-password --region "$AWS_REGION" |
    docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null
  fingerprint="$(compute_build_fingerprint)"
  image_tag="source-${fingerprint:0:32}"
  existing_digest=""
  if [[ "$FORCE_REBUILD" != "true" ]]; then
    existing_digest="$(aws ecr describe-images --region "$AWS_REGION" \
      --repository-name "$ECR_REPOSITORY" --image-ids "imageTag=$image_tag" \
      --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)"
  fi
  if [[ -n "$existing_digest" && "$existing_digest" != "None" ]]; then
    APP_IMAGE="${ECR_REGISTRY}/${ECR_REPOSITORY}@${existing_digest}"
    log "Reusing existing source-fingerprint image $APP_IMAGE."
  else
    [[ "$FORCE_REBUILD" != "true" ]] || image_tag="${image_tag}-forced-$(date -u +%Y%m%d%H%M%S)"
    tagged_image="${ECR_REGISTRY}/${ECR_REPOSITORY}:${image_tag}"
    log "Building and pushing $tagged_image. This model image is large and may take a while."
    docker buildx build --platform "$DOCKER_PLATFORM" --file "$REPO_ROOT/$DOCKERFILE_PATH" \
      --tag "$tagged_image" --push "$REPO_ROOT/$BUILD_CONTEXT"
    image_digest="$(aws ecr describe-images --region "$AWS_REGION" \
      --repository-name "$ECR_REPOSITORY" --image-ids "imageTag=$image_tag" \
      --query 'imageDetails[0].imageDigest' --output text)"
    [[ -n "$image_digest" && "$image_digest" != "None" ]] || fail "ECR did not return the pushed image digest."
    APP_IMAGE="${ECR_REGISTRY}/${ECR_REPOSITORY}@${image_digest}"
  fi
else
  log "Using caller-supplied image $APP_IMAGE."
fi

export APP_IMAGE ECR_REGISTRY DOKKU_APP_NAME
render_host_release
run_host_release

if [[ "$SKIP_HEALTH_CHECK" != "true" ]]; then
  wait_for_endpoint liveness "${DOKKU_APP_URL%/}/healthz" 60 10
  wait_for_endpoint readiness "${DOKKU_APP_URL%/}/readyz" "$READY_ATTEMPTS" 15
fi

log "Deployment complete: $DOKKU_APP_URL"
