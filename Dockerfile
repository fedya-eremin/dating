FROM python:3.11
ENV PYTHONUNBUFFERED=1
WORKDIR /code
COPY requirements.txt .
RUN pip install -r requirements.txt

