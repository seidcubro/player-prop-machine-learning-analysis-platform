FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir -U pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY train.py eval.py .
CMD python train.py

