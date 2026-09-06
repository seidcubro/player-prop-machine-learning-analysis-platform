FROM python:3.11-slim
WORKDIR /app
# LightGBM links against the OpenMP runtime, which the slim base image omits.
RUN apt-get update  && apt-get install -y --no-install-recommends libgomp1  && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -U pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Copy every training script so new tooling does not need a Dockerfile edit.
COPY *.py .
CMD python train.py

