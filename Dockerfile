# hr-interview-simulator — API 容器（agent 家族 api 段 5402，host=container）
#
# 家族 deploy 链同款（lab/saas-fastapi 先例）：
#   VPS nginx 终结 TLS → /api/ proxy_pass http://127.0.0.1:5402 → 容器 uvicorn。
#   CI deploy job build & push（latest + tag 双份）→ VPS deploy/hr-interview-simulator.sh
#   拉镜像起容器。
#
# 端口是家族契约（docs/families/agent.md，用户拍板：前端 5301 起/后端 5401 起），
# 钉死在 CMD 里不走 env 兜底（suite 硬规则 §1 同款口径：fail-fast，不留静默默认）。
# SQLite 持久化：deploy 脚本挂 /home/deploy/<repo>/data:/data + APP_DB_PATH=/data/app.db。
# 无 HEALTHCHECK（debian slim 无 wget）：探活由 deploy 脚本 host 侧 /api/health 完成。
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

EXPOSE 5402
CMD ["uvicorn", "--factory", "hr_interview.main:create_app", "--host", "0.0.0.0", "--port", "5402"]
