FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY policy.py .
COPY notify.py .
COPY controller.py .

CMD ["python", "-u", "controller.py"]