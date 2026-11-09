# ============================================================================
# 方案 B：Docker 部署专用镜像定义
# 构建上下文 = 仓库根目录（由 compose.yaml 的 build.context: ../.. 指定）。
# 只 COPY backend/、frontend/ 以及本目录下的 entrypoint.sh / healthcheck.py，
# 不引用、不打包 deploy/vm/ 的任何文件。
# ============================================================================
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="Wheelhouse (Docker deployment)" \
      org.opencontainers.image.description="Intranet Python package repository, stdlib only" \
      org.opencontainers.image.version="1.1"

# 固定 UID/GID 10001（与 compose 的 DK_UID/DK_GID 默认值一致）。
# 不创建系统账号：Docker 允许直接用数字 UID 运行进程。
RUN mkdir -p /app/backend /app/frontend /data /backup /tls \
 && chown -R 10001:10001 /app /data /backup /tls

WORKDIR /app

COPY --chown=10001:10001 backend/ /app/backend/
COPY --chown=10001:10001 frontend/ /app/frontend/
COPY --chown=10001:10001 deploy/docker/entrypoint.sh /app/entrypoint.sh
COPY --chown=10001:10001 deploy/docker/healthcheck.py /app/healthcheck.py
RUN chmod 0755 /app/entrypoint.sh /app/healthcheck.py

# 运行依赖：仅 Python 标准库（含 sqlite3、ssl、http.server），无需 pip install
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/app \
    TZ=Asia/Shanghai

# 容器内固定路径（与 DK_* 配置解耦）
ENV WH_DATA_DIR=/data \
    WH_FRONTEND_DIR=/app/frontend \
    WH_BACKUP_DIR=/backup

USER 10001:10001

# EXPOSE 仅为元数据；实际监听端口由 WH_LISTEN_PORT / DK_CONTAINER_PORT 决定
EXPOSE 8080
VOLUME ["/data", "/backup"]
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python3", "/app/healthcheck.py"]

# 清掉基础镜像继承的 CMD（python:3.12-slim 默认是 ["python3"]）
CMD []
ENTRYPOINT ["/app/entrypoint.sh"]
