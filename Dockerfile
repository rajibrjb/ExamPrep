FROM alpine:3.21

# Use Alpine's own python3 (not the official python:3.12-alpine image) so that
# apk's py3-opencv -- which only installs into Alpine's system interpreter --
# and pip's packages share the same Python. Mixing the two gives a cv2 that's
# importable by a different interpreter than the one running the app.
RUN apk add --no-cache \
      python3 \
      py3-pip \
      py3-opencv \
      tesseract-ocr \
      tesseract-ocr-data-eng \
      postgresql-libs \
      jpeg \
      zlib \
    && apk add --no-cache --virtual .build-deps \
      gcc musl-dev python3-dev postgresql-dev jpeg-dev zlib-dev

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt \
    && apk del .build-deps

COPY app/ /app/app/
COPY config.yaml schema.sql /app/

ENV PYTHONPATH=/app
EXPOSE 5000

CMD ["python3", "-m", "app.review_app"]
