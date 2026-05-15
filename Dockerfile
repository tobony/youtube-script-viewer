FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy dependency files first (cache layer)
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --no-dev --frozen

# Copy app code
COPY main.py .
COPY app/ app/

EXPOSE 7030

CMD ["uv", "run", "main.py"]
