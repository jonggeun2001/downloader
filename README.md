# Helm Chart 이미지 다운로더

## 개요
Helm 차트(github에 존재)에서 사용되는 모든 컨테이너 이미지를 의존성 포함하여 다운로드하는 도구입니다.

## 사용법

1. 가상환경 생성 및 패키지 설치

```bash
bash venv/setup_venv.sh
```

2. 이미지 다운로드 실행

```bash
python helm_image_downloader.py apache-airflow/airflow --version 1.16.0
```

## 필요 사항
- Docker가 설치되어 있어야 합니다.
- 인터넷 연결 필요
