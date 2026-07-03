# ─── ベースイメージ ───────────────────────────────────────────
# python:3.11-slim (Debian Bookworm) を使用。
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ─── システムパッケージ ───────────────────────────────────────
# Apple Silicon (aarch64) では fiona がバイナリホイールなくソースビルドになる。
# gdal-config が必要なため libgdal-dev を追加。
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

# ─── Python 依存 ─────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── アプリコード ─────────────────────────────────────────────
COPY . .

# ─── デフォルト起動コマンド（API サーバー） ──────────────────
# ワーカーは docker-compose.yml の worker サービスで上書きする
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
