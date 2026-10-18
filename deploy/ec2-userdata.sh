#!/bin/bash
# EC2 (Amazon Linux 2023) user-data: installs Docker, builds and runs LogFix AI.
# 1) Attach an instance profile using deploy/iam-policy.json  2) Upload the ZIP contents to /opt/logfix (see deploy/README.md)
set -euxo pipefail
dnf install -y docker git
systemctl enable --now docker
mkdir -p /opt/logfix && cd /opt/logfix
# After copying the project here:
docker build -t logfix-ai .
docker run -d --name logfix --restart unless-stopped -p 127.0.0.1:8000:8000 \
  -v logfix-data:/data -e LLM_PROVIDER=bedrock -e AWS_REGION=us-east-1 logfix-ai
