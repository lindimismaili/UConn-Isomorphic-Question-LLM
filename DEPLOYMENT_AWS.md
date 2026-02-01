# Deploy to AWS (simple) + Cloudflare subdomain

This project is a FastAPI backend (`server.py`) that also serves the frontend from `Site/`.

## 0) Prereqs

- An AWS account
- Your Cloudflare zone for `lindimismaili.com`
- An OpenAI API key (`OPENAI_API_KEY`)

## Option A (recommended): AWS Elastic Beanstalk (Docker, single instance)

This is the simplest “AWS-managed” path with minimal moving parts.

### 1) Build a small deploy zip (Windows / PowerShell)

From the repo root:

```powershell
Compress-Archive -Force `
  -Path Dockerfile,requirements.txt,server.py,run.py,main.py,Site,Prompts `
  -DestinationPath deploy.zip
```

### 2) Create the Elastic Beanstalk environment (AWS Console)

1. Go to **Elastic Beanstalk** → **Create application**
2. **Platform**: `Docker`
3. **Application code**: “Upload your code” → upload `deploy.zip`
4. Create the environment and wait for it to turn green

You’ll get an AWS URL like:
`https://<your-env>.<region>.elasticbeanstalk.com`

### 3) Set your OpenAI key in AWS

Elastic Beanstalk → your environment → **Configuration** → **Updates, monitoring, and logging** (or **Software**) → **Environment properties**:

- `OPENAI_API_KEY` = your key

Then **Apply**.

### 4) Point `uconn.lindimismaili.com` to the EB URL (Cloudflare)

Cloudflare → DNS → Records:

- Type: `CNAME`
- Name: `uconn`
- Target: your Elastic Beanstalk CNAME (the hostname part of the EB URL)

If you turn **Proxy** on (orange cloud):
- **Fastest**: Cloudflare SSL/TLS mode **Flexible** (HTTPS at Cloudflare, HTTP to AWS)
- **More secure (end-to-end HTTPS)**: see “HTTPS (recommended)” below

### 5) Test

After DNS propagates, open:
`https://uconn.lindimismaili.com`

## HTTPS (recommended, end-to-end)

If you want Cloudflare **Full (strict)** and HTTPS all the way to AWS:

1. In AWS **Certificate Manager (ACM)**, request a cert for `uconn.lindimismaili.com`
2. Validate it using the DNS records ACM gives you (in Cloudflare)
3. Use an Elastic Beanstalk environment that has a load balancer (ALB), then attach the ACM cert to an HTTPS listener (443)
4. In Cloudflare, set SSL/TLS mode to **Full (strict)** and keep the DNS record proxied if you want

## Notes

- The service needs `OPENAI_API_KEY` in the environment in AWS (don’t rely on `.env` in production).
- `Uploads/` and `Outputs/` are stored on the instance/container filesystem (ephemeral). If you need persistence, add S3 later.

