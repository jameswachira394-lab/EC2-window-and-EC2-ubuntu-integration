FROM python:3.12-slim

WORKDIR /forex_system

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Windows EC2 IP injected at runtime:
#   docker run -e MT5_SERVER_URL=http://<windows-ip>:8000 forex-bot
ENV MT5_SERVER_URL=http://10.0.1.153:8000" 

CMD ["python", "main.py"]