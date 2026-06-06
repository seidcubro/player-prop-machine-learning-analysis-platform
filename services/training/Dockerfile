FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir -U pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY train.py eval.py train_moneyline.py build_prop_edges.py .
CMD python train.py

