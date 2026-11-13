FROM python:3.11-slim
WORKDIR /srv
COPY . .
RUN pip install -r requirements.txt
CMD ["uvicorn", "app.main:api", "--host", "0.0.0.0"]
