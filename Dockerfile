FROM python:3.13-alpine
RUN apk add --no-cache docker-cli
COPY app /app
WORKDIR /app
CMD ["python3", "server.py"]
