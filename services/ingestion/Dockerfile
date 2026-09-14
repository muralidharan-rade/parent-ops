FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run the web service on container startup using uvicorn
CMD ["uvicorn", "aws_main:app", "--host", "0.0.0.0", "--port", "8080"]
