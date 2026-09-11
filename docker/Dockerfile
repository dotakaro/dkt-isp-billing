FROM odoo19-custom:19

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-ind \
    && pip3 install --break-system-packages --no-cache-dir \
        routeros-api \
        pytesseract \
        Pillow \
    && rm -rf /var/lib/apt/lists/*
USER odoo
