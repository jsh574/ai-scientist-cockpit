# 部署

## 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm install
Copy-Item backend\.env.example backend\.env
.\start.ps1
```

## Docker Compose

先创建 `backend/.env` 并填入运行所需凭据，然后执行：

```powershell
docker compose up --build
```

- 前端：http://localhost:5173
- API：http://localhost:8000
- OpenAPI：http://localhost:8000/docs

任务 Artifact 保存在命名卷 `scientist_artifacts` 中。不要把凭据写进 Dockerfile、镜像构建参数或 Git。

## 生产注意事项

当前文件系统存储适合单机演示。公开部署前至少增加：

1. API 身份认证与任务所有权校验。
2. PostgreSQL 或对象存储，以及备份策略。
3. HTTPS 反向代理和 CORS 白名单。
4. Agent 并发限流、调用预算与超时队列。
5. 日志脱敏，禁止记录 API Key 和完整敏感输入。

静态前端可以单独部署，但 `VITE_API_BASE_URL` 必须在构建时指向可访问的 HTTPS 后端地址。
