# Deploying LogFix AI V2

LogFix AI is one container with SQLite storage. **Run exactly one replica.**

## Quick local (Docker)
    docker compose up --build        # http://localhost:8000
    # first-run admin password: docker compose exec logfix cat /data/initial_admin_password.txt

## AWS (EC2 + Bedrock)
1. Create an IAM role from `iam-policy.json` and attach it to the instance (no access keys on the box).
2. In the Bedrock console, enable model access for the Claude model you set in `BEDROCK_MODEL_ID`.
3. Launch EC2 (t3.small+, Amazon Linux 2023). Security group: allow 443 (or 22 for tunnelling) only from your IP.
4. Copy the project, run `ec2-userdata.sh` steps. The container binds to 127.0.0.1; put nginx/Caddy/ALB with TLS in front, or use `ssh -L 8000:127.0.0.1:8000`.
5. In the UI: Sources > Add > AWS (region + log groups; leave keys empty to use the instance role). Admin > LLM > bedrock > Test.

## Azure (VM + Log Analytics + Azure OpenAI)
1. Create a service principal (or use managed identity) with **Log Analytics Reader** on the workspace.
2. Sources > Add > Azure: workspace ID, tenant ID, client ID, client secret.
3. Admin > LLM > azure_openai: endpoint + deployment + key.

## GCP
Create a service account with **Logs Viewer**; paste its JSON key in Sources > Add > GCP.

## Production hardening checklist
- Put TLS in front. The app does not terminate TLS.
- Set `LOGFIX_SECRET_KEY` and `LOGFIX_JWT_SECRET` from a secrets manager (KMS/Key Vault/Secrets Manager). By default they are files in the data volume, which protects against DB leaks but not against disk theft.
- Back up the `/data` volume.
- Set `LOGFIX_REPO_ROOTS` so the code assistant can only read intended directories.
- For more than one replica, replace `db.py` with Postgres and move the scheduler to a single leader.
