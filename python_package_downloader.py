#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import shutil
from pathlib import Path
import glob
import requests
import json
import re
from urllib.parse import urljoin
import concurrent.futures
from tqdm import tqdm
import zipfile
import io

def find_requirements_file():
    """
    requirements.txt 파일을 찾습니다.
    현재 디렉토리와 상위 디렉토리에서 검색합니다.
    
    Returns:
        str: requirements.txt 파일의 경로
    """
    # 현재 디렉토리에서 검색
    if os.path.exists("requirements.txt"):
        return os.path.abspath("requirements.txt")
    
    # 상위 디렉토리에서 검색
    parent_dir = os.path.dirname(os.getcwd())
    parent_requirements = os.path.join(parent_dir, "requirements.txt")
    if os.path.exists(parent_requirements):
        return parent_requirements
    
    return None

def copy_source_packages(source_dir, target_dirs):
    """
    소스 패키지를 각 플랫폼 디렉토리로 복사합니다.
    
    Args:
        source_dir (str): 소스 패키지가 있는 디렉토리
        target_dirs (list): 복사할 대상 디렉토리 목록
    """
    # tar.gz 파일 찾기
    source_files = glob.glob(os.path.join(source_dir, "*.tar.gz"))
    
    if not source_files:
        print("복사할 소스 패키지가 없습니다.")
        return
    
    # 각 대상 디렉토리에 파일 복사
    for target_dir in target_dirs:
        for source_file in source_files:
            filename = os.path.basename(source_file)
            target_file = os.path.join(target_dir, filename)
            shutil.copy2(source_file, target_file)
            print(f"복사됨: {filename} -> {target_dir}")

def get_missing_wheel_packages(requirements_path, platform_dir):
    """
    wheel 파일이 없는 패키지 목록을 반환합니다.
    
    Args:
        requirements_path (str): requirements.txt 파일 경로
        platform_dir (str): 플랫폼별 패키지 디렉토리
    
    Returns:
        list: wheel 파일이 없는 패키지 목록
    """
    # requirements.txt 파일 읽기
    with open(requirements_path, 'r') as f:
        packages = [line.strip().split('==')[0] for line in f if line.strip() and not line.startswith('#')]
    
    # wheel 파일이 없는 패키지 찾기
    missing_packages = []
    for package in packages:
        wheel_files = glob.glob(os.path.join(platform_dir, f"{package}*.whl"))
        if not wheel_files:
            missing_packages.append(package)
    
    return missing_packages

def get_package_dependencies(package_name, version=None):
    """패키지의 의존성을 가져옵니다.
    
    Args:
        package_name (str): 패키지 이름
        version (str, optional): 패키지 버전
        
    Returns:
        list: 의존성 패키지 목록 (패키지명, 버전) 튜플의 리스트
    """
    try:
        # 버전 문자열에서 == 제거
        clean_version = version.lstrip('=<>!~') if version else None
        print(f"[DEBUG] get_package_dependencies: {package_name} {version} (clean: {clean_version})")
        
        # 항상 /pypi/{패키지명}/json 엔드포인트 사용
        url = f"https://pypi.org/pypi/{package_name}/json"
        print(f"[DEBUG] PyPI API URL: {url}")
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        print(f"[DEBUG] PyPI JSON keys: {list(data.keys())}")
        
        # 릴리즈 정보에서 버전 선택
        releases = data.get('releases', {})
        if not releases:
            print(f"[DEBUG] No releases found for {package_name}")
            return []
        if clean_version and clean_version in releases and releases[clean_version]:
            target_version = clean_version
        else:
            # 가장 최신 버전 사용
            available_versions = [v for v in releases.keys() if releases[v]]
            if not available_versions:
                print(f"[DEBUG] No available versions for {package_name}")
                return []
            target_version = sorted(available_versions, key=lambda s: list(map(int, re.findall(r'\\d+', s))), reverse=True)[0]
            print(f"[DEBUG] 요청한 버전({clean_version})이 없으므로 최신 버전({target_version})으로 대체합니다.")
        print(f"[DEBUG] Using version: {target_version}")
        
        # wheel 파일 URL 찾기
        wheel_url = None
        for release in releases[target_version]:
            if release['packagetype'] == 'bdist_wheel':
                wheel_url = release['url']
                break
        print(f"[DEBUG] wheel_url: {wheel_url}")
        if not wheel_url:
            return []
        
        # wheel 파일 다운로드
        wheel_response = requests.get(wheel_url)
        wheel_response.raise_for_status()
        
        # METADATA 파일 찾기
        with zipfile.ZipFile(io.BytesIO(wheel_response.content)) as wheel:
            metadata_files = [f for f in wheel.namelist() if f.endswith('METADATA')]
            print(f"[DEBUG] METADATA files: {metadata_files}")
            if not metadata_files:
                return []
            # METADATA 파일 읽기
            metadata_content = wheel.read(metadata_files[0]).decode('utf-8')
            print(f"[DEBUG] METADATA content (first 500 chars):\n{metadata_content[:500]}")
            # 의존성 정보 파싱
            dependencies = []
            for line in metadata_content.split('\n'):
                if line.startswith('Requires-Dist:'):
                    # 버전 정보 추출
                    match = re.match(r'Requires-Dist: ([^;]+)(?:;.*)?', line)
                    if match:
                        dep = match.group(1).strip()
                        # extras([...]) 제거
                        dep = re.sub(r'\[.*?\]', '', dep)
                        # 괄호 및 그 뒤 내용 제거
                        dep = re.sub(r'\(.*', '', dep)
                        # 공백 앞까지만 사용
                        dep = dep.strip().split(' ')[0]
                        # 패키지명이 비정상적이면 무시
                        if not dep or not re.match(r'^[A-Za-z0-9_.\-]+$', dep):
                            continue
                        # 버전 정보가 있는 경우 추출
                        version_match = re.search(r'([<>=!~]+[\d.]+)', match.group(1))
                        if version_match:
                            dep_version = version_match.group(1)
                            dependencies.append((dep, dep_version))
                        else:
                            dependencies.append((dep, None))
            print(f"[DEBUG] Parsed dependencies: {dependencies}")
            return dependencies
    except Exception as e:
        print(f"의존성 정보를 가져오는 중 오류 발생: {e}")
        return []

def parse_requirements(requirements_path):
    """
    requirements.txt 파일을 파싱하여 패키지와 버전 정보를 추출합니다.
    
    Args:
        requirements_path (str): requirements.txt 파일 경로
    
    Returns:
        list: (패키지명, 버전) 튜플의 리스트
    """
    packages = []
    with open(requirements_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                # 버전 정보 추출
                match = re.match(r'^([^<>=!~]+)([<>=!~].*)?$', line)
                if match:
                    package_name = match.group(1).strip()
                    version = match.group(2).strip() if match.group(2) else None
                    packages.append((package_name, version))
    return packages

def get_all_dependencies(requirements_path):
    """requirements.txt 파일의 모든 패키지와 그 의존성을 가져옵니다.
    
    Args:
        requirements_path (str): requirements.txt 파일 경로
        
    Returns:
        set: 모든 패키지와 의존성 목록 (패키지명, 버전) 튜플의 집합
    """
    all_packages = set()
    
    def process_dependencies(package_name, version):
        if (package_name, version) in all_packages:
            return
            
        all_packages.add((package_name, version))
        dependencies = get_package_dependencies(package_name, version)
        for dep_name, dep_version in dependencies:
            process_dependencies(dep_name, dep_version)
    
    # requirements.txt 파일 읽기
    with open(requirements_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                # 버전 정보 추출
                match = re.match(r'([^<>=!~]+)([<>=!~]+[\d.]+)?', line)
                if match:
                    package_name = match.group(1).strip()
                    version = match.group(2) if match.group(2) else None
                    process_dependencies(package_name, version)
                    
    return all_packages

def write_temp_requirements(requirements_path, package_names, temp_path):
    """
    임시 requirements 파일을 생성합니다.
    
    Args:
        requirements_path (str): 원본 requirements.txt 파일 경로
        package_names (set): 포함할 패키지 이름 집합
        temp_path (str): 생성할 임시 파일 경로
    """
    packages = parse_requirements(requirements_path)
    with open(temp_path, 'w') as f:
        for package_name, version in packages:
            if package_name in package_names:
                if version:
                    f.write(f"{package_name}{version}\n")
                else:
                    f.write(f"{package_name}\n")

def download_file(url, target_path):
    """
    URL에서 파일을 다운로드합니다.
    
    Args:
        url (str): 다운로드할 파일의 URL
        target_path (str): 저장할 파일 경로
    """
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        block_size = 8192
        
        with open(target_path, 'wb') as f, tqdm(
            desc=os.path.basename(target_path),
            total=total_size,
            unit='iB',
            unit_scale=True,
            unit_divisor=1024,
        ) as pbar:
            for data in response.iter_content(block_size):
                size = f.write(data)
                pbar.update(size)
    except Exception as e:
        print(f"파일 다운로드 중 오류 발생: {url} -> {e}")
        if os.path.exists(target_path):
            os.remove(target_path)

def parse_wheel_tag(filename):
    """
    wheel 파일명에서 태그 정보를 파싱합니다.
    
    Args:
        filename (str): wheel 파일명
    
    Returns:
        tuple: (python_tag, abi_tag, platform_tag) 또는 None
    """
    # wheel 파일명 형식: {package}-{version}-{python_tag}-{abi_tag}-{platform_tag}.whl
    match = re.search(r'-([^-]+)-([^-]+)-([^-]+)\.whl$', filename)
    if match:
        return match.groups()
    return None

def is_compatible_wheel(filename, python_version):
    """
    wheel 파일이 현재 Python 버전과 호환되는지 확인합니다.
    
    Args:
        filename (str): wheel 파일명
        python_version (str): Python 버전
    
    Returns:
        bool: 호환 여부
    """
    tags = parse_wheel_tag(filename)
    if not tags:
        return False
    
    python_tag, abi_tag, platform_tag = tags
    
    # Python 버전 확인
    py_version = python_version.replace('.', '')
    if not (f'cp{py_version}' in python_tag or 'py3' in python_tag):
        return False
    
    # 플랫폼 태그 확인
    if 'win' in filename.lower():
        return 'win_amd64' in platform_tag or 'win_x86_64' in platform_tag
    elif 'linux' in filename.lower():
        return 'linux_x86_64' in platform_tag or 'manylinux' in platform_tag
    else:
        # 플랫폼 독립적인 wheel 파일
        return 'any' in platform_tag

def get_package_files(package_name, version=None, python_version=None):
    """
    PyPI에서 패키지의 모든 파일 정보를 가져옵니다.
    
    Args:
        package_name (str): 패키지 이름
        version (str, optional): 패키지 버전
        python_version (str, optional): Python 버전
    
    Returns:
        list: (url, filename, platform) 튜플의 리스트
    """
    # 버전 문자열에서 연산자 제거 (예: '==1.2.3' -> '1.2.3')
    version_str = None
    if version:
        m = re.match(r'^[^\d]*([\d.]+)', version)
        if m:
            version_str = m.group(1)
        else:
            version_str = version
    print(f"PyPI API 호출: {package_name} (요청 버전: {version_str if version_str else '최신'})")
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        releases = data.get('releases', {})
        # 버전이 지정된 경우 해당 버전만, 아니면 모든 버전
        if version_str and version_str in releases and releases[version_str]:
            target_versions = [version_str]
        else:
            # 가장 최신 버전으로 대체
            available_versions = [v for v in releases.keys() if releases[v]]
            if not available_versions:
                print(f"  [경고] {package_name}의 사용 가능한 릴리즈가 없습니다.")
                return []
            latest_version = sorted(available_versions, key=lambda s: list(map(int, re.findall(r'\d+', s))), reverse=True)[0]
            print(f"  [알림] 요청한 버전({version_str})이 없으므로 최신 버전({latest_version})으로 대체합니다.")
            target_versions = [latest_version]
        files = []
        for ver in target_versions:
            release_files = releases[ver]
            # whl 파일이 있는지 확인
            whl_files = [f for f in release_files if f['filename'].endswith('.whl')]
            if whl_files:
                # Python 버전과 아키텍처에 맞는 wheel 파일 필터링
                compatible_wheels = []
                for wheel in whl_files:
                    filename = wheel['filename']
                    if is_compatible_wheel(filename, python_version):
                        compatible_wheels.append(wheel)
                        print(f"  호환되는 wheel 파일 발견: {filename}")
                
                if compatible_wheels:
                    selected_files = compatible_wheels
                else:
                    # 호환되는 wheel 파일이 없는 경우 소스 배포판 사용
                    selected_files = [f for f in release_files if f['filename'].endswith('.tar.gz')]
                    print(f"  호환되는 wheel 파일이 없어 소스 배포판을 사용합니다.")
            else:
                selected_files = [f for f in release_files if f['filename'].endswith('.tar.gz')]
            
            for file_info in selected_files:
                url = file_info['url']
                filename = file_info['filename']
                # 플랫폼 식별
                platform = None
                if 'win' in filename.lower():
                    platform = 'win'
                elif 'linux' in filename.lower():
                    platform = 'linux'
                files.append((url, filename, platform))
                print(f"  파일 발견: {filename} (플랫폼: {platform if platform else '공통'})")
        return files
    except requests.exceptions.RequestException as e:
        print(f"PyPI API 호출 중 오류 발생: {e}")
        return []

def download_package_files(package_name, version, target_dirs, python_version):
    """
    패키지의 모든 파일을 다운로드합니다.
    
    Args:
        package_name (str): 패키지 이름
        version (str, optional): 패키지 버전
        target_dirs (dict): 플랫폼별 대상 디렉토리
        python_version (str): Python 버전
    """
    print(f"\n패키지 다운로드 시작: {package_name} (버전: {version if version else '최신'})")
    files = get_package_files(package_name, version, python_version)
    print(f"발견된 파일 수: {len(files)}")
    
    for url, filename, platform in files:
        print(f"  파일: {filename} (플랫폼: {platform if platform else '공통'})")
        if platform == 'win':
            target_dir = target_dirs['win']
        elif platform == 'linux':
            target_dir = target_dirs['linux']
        else:
            # 플랫폼 특정 파일이 아닌 경우 모든 디렉토리에 다운로드
            for dir_path in target_dirs.values():
                target_path = os.path.join(dir_path, filename)
                print(f"    다운로드: {url} -> {target_path}")
                download_file(url, target_path)
            continue
        
        target_path = os.path.join(target_dir, filename)
        print(f"    다운로드: {url} -> {target_path}")
        download_file(url, target_path)

def create_install_scripts(target_dirs, python_version):
    """
    Windows와 Linux용 설치 스크립트를 생성합니다.
    
    Args:
        target_dirs (dict): 플랫폼별 대상 디렉토리
        python_version (str): Python 버전
    """
    # Windows용 설치 스크립트 생성
    win_script = f"""@echo off
setlocal enabledelayedexpansion

echo Python {python_version} 패키지 설치를 시작합니다...

:: pip 설치 확인
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo pip가 설치되어 있지 않습니다. pip를 설치합니다...
    python -m ensurepip --default-pip
)

:: 패키지 설치
for %%f in (*.whl) do (
    echo %%f 설치 중...
    python -m pip install --no-index --find-links=. %%f
)

echo 설치가 완료되었습니다.
pause
"""
    win_script_path = os.path.join(target_dirs['win'], 'install.bat')
    with open(win_script_path, 'w', encoding='utf-8') as f:
        f.write(win_script)
    print(f"Windows 설치 스크립트 생성됨: {win_script_path}")

    # Linux용 설치 스크립트 생성
    linux_script = f"""#!/bin/bash

echo "Python {python_version} 패키지 설치를 시작합니다..."

# pip 설치 확인
if ! command -v pip3 &> /dev/null; then
    echo "pip가 설치되어 있지 않습니다. pip를 설치합니다..."
    python3 -m ensurepip --default-pip
fi

# 패키지 설치
for file in *.whl; do
    if [ -f "$file" ]; then
        echo "$file 설치 중..."
        python3 -m pip install --no-index --find-links=. "$file"
    fi
done

echo "설치가 완료되었습니다."
"""
    linux_script_path = os.path.join(target_dirs['linux'], 'install.sh')
    with open(linux_script_path, 'w', encoding='utf-8') as f:
        f.write(linux_script)
    # 실행 권한 부여
    os.chmod(linux_script_path, 0o755)
    print(f"Linux 설치 스크립트 생성됨: {linux_script_path}")

def download_packages(requirements_path, python_version):
    """requirements.txt 파일에 명시된 패키지와 그 의존성을 다운로드합니다.
    
    Args:
        requirements_path (str): requirements.txt 파일 경로
        python_version (str): Python 버전 (예: "3.12")
    """
    if not os.path.exists(requirements_path):
        print(f"requirements.txt 파일을 찾을 수 없습니다: {requirements_path}")
        return
        
    print(f"requirements.txt 파일을 찾았습니다: {requirements_path}")
    
    # 플랫폼별 디렉토리 생성
    win_dir = f"pypackage_win_x86_64_py{python_version.replace('.', '')}"
    linux_dir = f"pypackage_linux_amd64_py{python_version.replace('.', '')}"
    target_dirs = {'win': win_dir, 'linux': linux_dir}
    
    # 기존 디렉토리 삭제
    for dir_path in [win_dir, linux_dir]:
        if os.path.exists(dir_path):
            print(f"기존 디렉토리 삭제: {dir_path}")
            shutil.rmtree(dir_path)
        print(f"디렉토리 생성: {dir_path}")
        os.makedirs(dir_path)
    
    # 패키지 의존성 분석
    print("패키지 의존성 분석 중...")
    all_packages = get_all_dependencies(requirements_path)
    print(f"총 {len(all_packages)}개의 패키지가 필요합니다.")
    print("패키지 목록:")
    for package_name, version in sorted(all_packages):
        print(f"  {package_name} (버전: {version if version else 'latest'})")
    
    # 패키지 다운로드
    print("\n패키지 다운로드 시작...")
    for package_name, version in tqdm(all_packages, desc="패키지 다운로드"):
        print(f"\n패키지 다운로드 시작: {package_name} (버전: {version if version else 'latest'})")
        download_package_files(package_name, version, target_dirs, python_version)
    
    # 설치 스크립트 생성
    create_install_scripts(target_dirs, python_version)

def main():
    parser = argparse.ArgumentParser(description='Python 패키지와 의존성을 다운로드합니다.')
    parser.add_argument('--requirements-path', help='requirements.txt 파일 경로 (지정하지 않으면 자동으로 찾습니다)')
    parser.add_argument('--python-version', default='3.12', help='Python 버전 (기본값: 3.12)')
    
    args = parser.parse_args()
    
    # requirements.txt 파일 경로 찾기
    requirements_path = args.requirements_path
    if not requirements_path:
        requirements_path = find_requirements_file()
        if not requirements_path:
            print("오류: requirements.txt 파일을 찾을 수 없습니다.")
            print("현재 디렉토리나 상위 디렉토리에 requirements.txt 파일이 있어야 합니다.")
            sys.exit(1)
        print(f"requirements.txt 파일을 찾았습니다: {requirements_path}")
    
    if not os.path.exists(requirements_path):
        print(f"오류: {requirements_path} 파일을 찾을 수 없습니다.")
        sys.exit(1)
    
    download_packages(requirements_path, args.python_version)

if __name__ == "__main__":
    main()
