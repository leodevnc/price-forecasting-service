FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home app && mkdir /state && chown app /state
USER app
ENV FORECAST_STATE_DIR=/state
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "price_forecasting.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
