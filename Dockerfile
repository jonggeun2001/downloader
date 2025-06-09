FROM apache/airflow:2.10.5 as builder

# root 권한으로 시스템 패키지 설치 (필요시)
USER root
RUN apt-get update && apt-get install -y \
    build-essential krb5-config libkrb5-dev \
    && rm -rf /var/lib/apt/lists/* \
    && which krb5-config && chmod +x /usr/bin/krb5-config

# requirements.txt 복사 및 설치
USER airflow
COPY requirements-airflow.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 최종 이미지
FROM apache/airflow:2.10.5

USER root
RUN apt-get update && apt-get install -y \
    krb5-config libkrb5-dev \
    && rm -rf /var/lib/apt/lists/* \
    && which krb5-config && chmod +x /usr/bin/krb5-config

USER airflow
COPY --from=builder /home/airflow/.local /home/airflow/.local

#podman build --platform linux/amd64 -t mirror-registry.dp-dev.kbstar.com:5000/airflow:2.10.5-kb-0.1.1 . 