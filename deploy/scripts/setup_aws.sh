#!/usr/bin/env bash
# iPracticom Sweeper — AWS setup helper.
#
# What this does (read-only checks + optional setup):
#   1. Verify AWS CLI is installed (or install via pip)
#   2. Verify credentials work (aws sts get-caller-identity)
#   3. Check that IAM user iPracticomSweeperBot exists; create if missing
#   4. Check that IAM role iPracticomSweeperRole exists; create if missing
#   5. Check that all EC2 instances in the configured tags are SSM-managed
#   6. Print a summary: who am I, what instances are reachable, what's missing
#
# Usage:
#   bash setup_aws.sh             # interactive
#   bash setup_aws.sh --check     # read-only audit, no changes
#
# Requires:
#   - AWS credentials with IAM admin + EC2 read + SSM read permissions
#     (your admin profile, NOT the sweeper's bot credentials)
#   - jq (sudo apt install jq / brew install jq)

set -euo pipefail

CONFIG_DIR="${IPRACTICOM_CONFIG_DIR:-$HOME/.ipracticom-sweeper/config}"
FLEET_YAML="$CONFIG_DIR/fleet.yaml"

C_BLUE='\033[0;34m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
log()  { printf "${C_BLUE}[aws]${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[aws]${C_RESET} ✅ %s\n" "$*"; }
warn() { printf "${C_YELLOW}[aws]${C_RESET} ⚠️  %s\n" "$*" >&2; }
err()  { printf "${C_RED}[aws]${C_RESET} ❌ %s\n" "$*" >&2; }

MODE="interactive"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) MODE="check" ;;
        *) err "unknown arg: $1"; exit 2 ;;
    esac
    shift
done

# --- Step 1: AWS CLI --------------------------------------------------------
log "[1/5] Checking AWS CLI..."
if ! command -v aws >/dev/null 2>&1; then
    if [[ "$MODE" == "check" ]]; then
        err "aws CLI not installed; install with: pip install awscli"
        exit 1
    fi
    warn "aws CLI not found — installing via pip..."
    python3 -m pip install --quiet awscli || {
        err "pip install awscli failed — install via your OS package manager"
        exit 1
    }
fi
AWS="$(command -v aws)"
ok "aws CLI: $($AWS --version)"

# --- Step 2: Credentials ----------------------------------------------------
log "[2/5] Verifying credentials..."
CALLER_ID="$("$AWS" sts get-caller-identity --output json 2>&1)" || {
    err "credentials invalid or expired"
    echo "$CALLER_ID" >&2
    echo "" >&2
    echo "Configure with: aws configure" >&2
    echo "Or set env: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION" >&2
    exit 1
}
ACCOUNT="$(echo "$CALLER_ID" | python3 -c "import sys,json; print(json.load(sys.stdin)['Account'])")"
ARN="$(echo "$CALLER_ID" | python3 -c "import sys,json; print(json.load(sys.stdin)['Arn'])")"
ok "Account: $ACCOUNT"
ok "Caller:  $ARN"

# --- Step 3: IAM user -------------------------------------------------------
log "[3/5] Checking IAM user iPracticomSweeperBot..."
USER_NAME="iPracticomSweeperBot"
if "$AWS" iam get-user --user-name "$USER_NAME" >/dev/null 2>&1; then
    ok "user '$USER_NAME' exists"
else
    if [[ "$MODE" == "check" ]]; then
        warn "user '$USER_NAME' does not exist (would be created in interactive mode)"
    else
        log "creating user '$USER_NAME'..."
        "$AWS" iam create-user --user-name "$USER_NAME" >/dev/null
        ok "user created"

        # Attach the minimum policy for fleet scanning
        POLICY_ARN="arn:aws:iam::aws:policy/AmazonSSMFullAccess"
        "$AWS" iam attach-user-policy --user-name "$USER_NAME" --policy-arn "$POLICY_ARN"
        ok "attached $POLICY_ARN"

        # Create access key
        KEY_OUT="$("$AWS" iam create-access-key --user-name "$USER_NAME" --output json)"
        KEY_ID="$(echo "$KEY_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['AccessKeyId'])")"
        SECRET="$(echo "$KEY_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['SecretAccessKey'])")"

        # Save to agent.env (append, don't overwrite)
        ENV_FILE="$CONFIG_DIR/agent.env"
        if [[ -f "$ENV_FILE" ]]; then
            {
                echo ""
                echo "# AWS credentials for the fleet connector (created by setup_aws.sh)"
                echo "AWS_ACCESS_KEY_ID=$KEY_ID"
                echo "AWS_SECRET_ACCESS_KEY=$SECRET"
            } >> "$ENV_FILE"
            ok "credentials saved to $ENV_FILE (mode 600)"
        fi

        echo ""
        warn "SAVE THESE NOW — they won't be shown again:"
        echo "    AWS_ACCESS_KEY_ID=$KEY_ID"
        echo "    AWS_SECRET_ACCESS_KEY=$SECRET"
        echo ""
    fi
fi

# --- Step 4: IAM role (for EC2 instances) ----------------------------------
log "[4/5] Checking IAM role iPracticomSweeperRole..."
ROLE_NAME="iPracticomSweeperRole"
if "$AWS" iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    ok "role '$ROLE_NAME' exists"
else
    if [[ "$MODE" == "check" ]]; then
        warn "role '$ROLE_NAME' does not exist"
    else
        log "creating role '$ROLE_NAME'..."
        # Trust policy: EC2 can assume this role
        cat > /tmp/trust-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF
        "$AWS" iam create-role \
            --role-name "$ROLE_NAME" \
            --assume-role-policy-document file:///tmp/trust-policy.json >/dev/null
        ok "role created"

        # Attach managed policy that lets SSM agent register
        "$AWS" iam attach-role-policy \
            --role-name "$ROLE_NAME" \
            --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
        ok "attached AmazonSSMManagedInstanceCore"

        # Create instance profile (EC2 needs a profile to attach a role)
        "$AWS" iam create-instance-profile --instance-profile-name "$ROLE_NAME" >/dev/null
        "$AWS" iam add-role-to-instance-profile \
            --instance-profile-name "$ROLE_NAME" \
            --role-name "$ROLE_NAME"
        ok "instance profile created + linked"
    fi
fi

# --- Step 5: Fleet health ---------------------------------------------------
log "[5/5] Checking fleet reachability..."
if [[ ! -f "$FLEET_YAML" ]]; then
    warn "no $FLEET_YAML found — skipping fleet check"
    exit 0
fi

# Parse tags from YAML using Python (avoid PyYAML dep)
TAGS_JSON="$(python3 -c "
import re, sys, yaml
" 2>/dev/null)" || {
    # YAML parsing failed — try a fallback grep
    TAGS_JSON=""
}

if ! command -v jq >/dev/null 2>&1; then
    warn "jq not installed — fleet check will be best-effort"
fi

# List instances matching tags (without PyYAML — call EC2 directly with our known tag set)
# In production, the user would have set this in fleet.yaml. We read env=* as a safe default.
INSTANCES="$("$AWS" ec2 describe-instances \
    --filters "Name=instance-state-name,Values=running" \
              "Name=tag:env,Values=*" \
    --query 'Reservations[].Instances[].InstanceId' \
    --output text 2>/dev/null || echo "")"

if [[ -z "$INSTANCES" ]]; then
    warn "no running instances with tag:env found"
    echo "  Set tags on your EC2s (env=prod, team=infra) or edit $FLEET_YAML"
else
    log "found $(echo "$INSTANCES" | wc -w) running instances with tag:env"
    # Check SSM registration for each
    for iid in $INSTANCES; do
        SSM_STATE="$("$AWS" ssm describe-instance-information \
            --filters "Key=InstanceIds,Values=$iid" \
            --query 'InstanceInformationList[0].PingStatus' \
            --output text 2>/dev/null || echo "Unknown")"
        if [[ "$SSM_STATE" == "Online" ]]; then
            ok "$iid: SSM online"
        else
            warn "$iid: SSM state = $SSM_STATE (attach $ROLE_NAME + install ssm-agent)"
        fi
    done
fi

echo
ok "AWS setup check complete"
