FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# git records the vault history (OBSIDIAN_HISTORY_DIR).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-deps . && pip install fastapi 'uvicorn[standard]' pydantic pyyaml python-frontmatter watchfiles

# Vault is bind-mounted at runtime. /vault inside the container is read-write.
VOLUME ["/vault"]

EXPOSE 4040

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:4040/healthz', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "obsidian_writer.app:app", "--host", "0.0.0.0", "--port", "4040"]
