# mcp-workspace: Workspace MCP server (Streamable HTTP)
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project 
COPY . .
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8103
CMD ["python", "-m", "mcp_servers.workspace.server", "--transport", "http", "--port", "8103"]
