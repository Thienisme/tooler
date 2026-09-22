#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
cd app
exec streamlit run app.py \
    --server.port 8501 \
    --server.headless true \
    --server.address 0.0.0.0
