# infra/bootstrap

One-time foundation, applied by the account administrator in AWS CloudShell.
Everything after it runs in GitHub Actions through OIDC
([ADR 0001](../../docs/adr/0001-keyless-oidc.md)).

It creates:

- the S3 state bucket `fitcheck-tfstate-<random>` (versioning, SSE-S3, public access blocked)
  ([ADR 0002](../../docs/adr/0002-s3-state-native-lock.md));
- the GitHub Actions OIDC provider;
- three CI roles:
  - `fitcheck-ci-plan`: pull requests, read-only;
  - `fitcheck-ci-apply`: main branch, infrastructure (Lambda, API Gateway, logs,
    workload IAM roles, and the stage 3 DynamoDB tables, SQS queues, SNS
    topics, alarms and event source mappings);
  - `fitcheck-ci-eval`: pull requests and main, Bedrock model invocation only
    (Titan Text Embeddings V2; Claude Sonnet 4.6, and Sonnet 5 and 5.5 once
    AWS grants the account access to them);
- `fitcheck-workload-boundary`, the permissions boundary for every role the CI
  creates: logs, X-Ray, the same Bedrock models, item access on `fitcheck-*`
  tables and message access on `fitcheck-*` queues;
- the `fitcheck-monthly-cost` budget (US$ 20 during stages 3 to 5, back to 5
  afterwards; email alerts at 50 %, 80 % and 100 %).

`terraform test` checks the rendered IAM documents offline, without AWS credentials.

## First run

Open CloudShell in **us-east-1**. Run each block separately and read its
output before the next one.

### 1. Session setup

Repeat this block after any CloudShell restart. The AWS provider unpacks to
roughly 800 MB and `$HOME` holds 1 GB, so the provider cache lives in `/tmp`.

```bash
export PATH="$HOME/bin:$PATH"
export TF_PLUGIN_CACHE_DIR=/tmp/terraform-plugin-cache
mkdir -p "$TF_PLUGIN_CACHE_DIR"
df -h "$HOME" /tmp
```

### 2. Pre-flight: the GitHub OIDC provider must not exist yet

Reads only that provider. If it prints STOP, do not continue.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if aws iam get-open-id-connect-provider \
    --open-id-connect-provider-arn "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com" \
    >/dev/null 2>/tmp/oidc-check.err; then
  echo "STOP: a GitHub OIDC provider already exists in this account."
elif grep -q NoSuchEntity /tmp/oidc-check.err; then
  echo "OK: no GitHub OIDC provider yet."
else
  echo "STOP: the check failed:"; cat /tmp/oidc-check.err
fi
```

### 3. Install Terraform 1.16.5 for this machine's architecture

```bash
TF_VERSION=1.16.5
TF_ARCH=$(uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')
mkdir -p ~/bin /tmp/terraform-download && cd /tmp/terraform-download
curl -fsSLO "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_${TF_ARCH}.zip"
curl -fsSLO "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_SHA256SUMS"
grep "_linux_${TF_ARCH}.zip" "terraform_${TF_VERSION}_SHA256SUMS" | sha256sum -c - \
  && unzip -o -q "terraform_${TF_VERSION}_linux_${TF_ARCH}.zip" terraform -d ~/bin
cd ~ && terraform version
```

### 4. Clone the repository

```bash
cd ~ && git clone https://github.com/khaled-haikel/fit-check.git
cd ~/fit-check/infra/bootstrap
```

### 5. Create `terraform.tfvars` and `backend.hcl`

Both files are git-ignored. Replace the email before running. The block keeps
an existing `backend.hcl`, so running it twice never picks a second bucket name.

```bash
ALERT_EMAIL='you@example.com'
if [ -f backend.hcl ]; then
  echo "backend.hcl already exists, keeping it."
else
  STATE_BUCKET="fitcheck-tfstate-$(openssl rand -hex 4)"
  printf 'bucket = "%s"\n' "$STATE_BUCKET" > backend.hcl
  printf 'state_bucket_name = "%s"\nalert_email       = "%s"\n' "$STATE_BUCKET" "$ALERT_EMAIL" > terraform.tfvars
fi
cat backend.hcl terraform.tfvars
```

### 6. Init with local state, then plan

The bucket does not exist yet, so this run keeps the state in a local file.
`backend_override.tf` is git-ignored.

```bash
cat > backend_override.tf <<'EOF'
# First run only: keep the state local until the bucket exists.
terraform {
  backend "local" {}
}
EOF
terraform init -input=false
terraform plan -input=false -out=bootstrap.tfplan
```

Review the plan before going on.

### 7. Apply and move the state into the bucket, in the same session

```bash
terraform apply -input=false bootstrap.tfplan
rm backend_override.tf
terraform init -input=false -backend-config=backend.hcl -migrate-state -force-copy
terraform state list
terraform plan -input=false
```

The last plan must report `No changes.` Only then remove the local copies:

```bash
rm -f terraform.tfstate terraform.tfstate.backup bootstrap.tfplan
terraform output
```

The outputs identify the account: they go into the GitHub repository
variables `TF_STATE_BUCKET`, `AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN` and
`AWS_EVAL_ROLE_ARN`, never into a committed file.

If CloudShell restarts before the migration, the files in `$HOME` (local
state, saved plan, `terraform.tfvars`, `backend.hcl`) survive and `/tmp` does
not: run block 1, then `terraform init -input=false`, and continue.

## Later runs

Later stages change the CI permissions here. In CloudShell, re-apply a branch
with `reapply.sh` (Terraform must already be in `~/bin`, step 3):

```bash
git -C ~/fit-check switch main && git -C ~/fit-check pull --ff-only
bash ~/fit-check/infra/bootstrap/reapply.sh <branch>
```

The script:

1. sets `PATH` and `TF_PLUGIN_CACHE_DIR` for the session;
2. stops if `backend_override.tf` exists or if `backend.hcl` or
   `terraform.tfvars` is missing;
3. switches to the branch and pulls it;
4. runs `terraform init` and `terraform plan`, and prints only the changed
   resources, their `+`/`-`/`~` lines and the `Plan:` line;
5. asks `Apply? (y/n)` and applies the saved plan only on `y`;
6. deletes the plan and returns to `main`, whether it applied or not.

If CloudShell lost `backend.hcl` or `terraform.tfvars`, recreate them. The
bucket name is the `TF_STATE_BUCKET` repository variable.

```bash
cd ~/fit-check/infra/bootstrap
printf 'bucket = "%s"\n' 'fitcheck-tfstate-XXXXXXXX' > backend.hcl
printf 'state_bucket_name = "%s"\nalert_email       = "%s"\n' 'fitcheck-tfstate-XXXXXXXX' 'you@example.com' > terraform.tfvars
```
