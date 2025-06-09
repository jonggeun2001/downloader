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
import packaging.version
import packaging.specifiers
import platform
from typing import Optional, Set

processed_packages = set()

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

def get_python_version(python_version: str) -> str:
    """Python 버전을 가져옵니다."""
    major, minor = python_version.split('.')
    return f"cp{major}{minor}0"

def get_python_abi(python_version: str) -> str:
    """Python ABI를 가져옵니다."""
    major, minor = python_version.split('.')
    return f"cp{major}{minor}"

def get_platform_tag() -> str:
    """현재 플랫폼에 맞는 태그를 가져옵니다."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    if system == "darwin":
        return "macosx_10_9_universal2"
    elif system == "linux":
        if machine == "x86_64":
            return "manylinux_2_17_x86_64"
        elif machine == "aarch64":
            return "manylinux_2_17_aarch64"
    elif system == "windows":
        if machine == "amd64":
            return "win_amd64"
        elif machine == "x86":
            return "win32"
    
    return "any"

def get_package_dependencies(package_name: str, version: str, processed_packages: set) -> set:
    """패키지의 의존성을 가져옵니다."""
    dependencies = set()
    
    print(f"[의존성 파싱] {package_name} {version if version else ''}")
    
    # 버전에서 연산자 제거 (예: '==1.2.3' -> '1.2.3')
    version_only = None
    if version:
        m = re.match(r'.*?([0-9][0-9a-zA-Z\.]*)', version)
        if m:
            version_only = m.group(1)
    
    try:
        # PyPI API 호출
        if version_only:
            url = f"https://pypi.org/pypi/{package_name}/{version_only}/json"
        else:
            url = f"https://pypi.org/pypi/{package_name}/json"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        # requires_dist 파싱
        requires_dist = data.get('info', {}).get('requires_dist', [])
        if not requires_dist:
            return dependencies
            
        for req in requires_dist:
            # 환경 마커 제거 (예: '; python_version >= "3.6"')
            req = req.split(';')[0].strip()
            if not req:
                continue
                
            # 패키지 이름과 버전 추출
            match = re.match(r'^([a-zA-Z0-9_\-\.]+)(?:\[.*?\])?(?:\s*([<>=!~].*))?$', req)
            if match:
                dep_name = match.group(1).lower()
                dep_version = match.group(2) if match.group(2) else ''
                dependencies.add((dep_name, dep_version))
                print(f"  의존성 발견: {dep_name} {dep_version}")
                    
    except Exception as e:
        print(f"  [경고] 의존성 정보를 가져오는 중 오류 발생: {str(e)}")
        
    return dependencies

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
                # extras 처리
                if '[' in line:
                    package_name = line.split('[')[0].strip()
                else:
                    package_name = line
                
                # 버전 조건 추출
                version_spec = None
                for op in ['==', '>=', '<=', '~=', '!=', '>', '<']:
                    if op in package_name:
                        package_name, version_spec = package_name.split(op, 1)
                        version_spec = f"{op}{version_spec.strip()}"
                        break
                
                package_name = package_name.strip()
                if package_name:
                    packages.append((package_name, version_spec))
    return packages

def get_all_dependencies(requirements_path, target_dirs, python_version):
    """requirements.txt 파일에서 모든 패키지와 의존성을 재귀적으로 파싱하며, 발견 즉시 다운로드합니다."""
    all_packages = set()
    processed_packages.clear()

    def process_dependencies(package_name, version_spec):
        key = (package_name.lower(), version_spec or '')
        if key in processed_packages:
            print(f"[SKIP] 이미 처리됨: {package_name} {version_spec if version_spec else ''}")
            return
        processed_packages.add(key)
        all_packages.add((package_name, version_spec))
        print(f"[의존성 파싱] {package_name} {version_spec if version_spec else ''}")
        # 다운로드
        download_package_files(package_name, version_spec, python_version, processed_packages)
        # 의존성 가져오기
        dependencies = get_package_dependencies(package_name, version_spec, processed_packages)
        for dep_name, dep_version_spec in dependencies:
            process_dependencies(dep_name, dep_version_spec)

    # requirements.txt 파일 파싱
    packages = parse_requirements(requirements_path)
    for package_name, version_spec in packages:
        process_dependencies(package_name, version_spec)

    print(f"총 {len(all_packages)}개의 패키지가 필요합니다.")
    print("패키지 목록:")
    for package_name, version_spec in sorted(all_packages, key=lambda x: (x[0], x[1] or '')):
        print(f"  {package_name} (버전 조건: {version_spec})")

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
    
    # musllinux 제외
    if 'musllinux' in platform_tag:
        return False
    
    # 플랫폼 태그 확인 (64비트만 허용)
    if 'win' in filename.lower():
        # amd64만 허용, win32, i686 등은 제외
        return 'win_amd64' in platform_tag or 'win64' in platform_tag
    elif 'linux' in filename.lower():
        # x86_64, manylinux_x86_64만 허용, i686, arm 등은 제외
        return (
            'x86_64' in platform_tag or
            'manylinux1_x86_64' in platform_tag or
            'manylinux2010_x86_64' in platform_tag or
            'manylinux2014_x86_64' in platform_tag or
            'manylinux_2_17_x86_64' in platform_tag
        )
    else:
        # 플랫폼 독립적인 wheel 파일
        return 'any' in platform_tag

def get_package_files(package_name: str, version: str, python_version: str) -> list:
    """패키지의 파일 정보를 가져옵니다."""
    files = []
    
    try:
        # 버전에서 연산자 제거 (예: '==1.2.3' -> '1.2.3')
        version_only = None
        if version:
            m = re.match(r'.*?([0-9][0-9a-zA-Z\.]*)', version)
            if m:
                version_only = m.group(1)
        
        # PyPI API 호출
        if version_only:
            url = f"https://pypi.org/pypi/{package_name}/{version_only}/json"
        else:
            url = f"https://pypi.org/pypi/{package_name}/json"
            
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        # 파일 정보 파싱
        releases = data.get('releases', {})
        if not releases:
            return files
            
        # 버전 조건에 맞는 릴리즈 찾기
        target_version = None
        if version:
            # 버전 조건 파싱
            version_spec = version.strip()
            if version_spec.startswith('=='):
                target_version = version_spec[2:]
            elif version_spec.startswith('>='):
                min_version = version_spec[2:]
                # 최신 버전 선택
                target_version = max(releases.keys(), key=lambda x: packaging.version.parse(x))
            elif version_spec.startswith('<'):
                max_version = version_spec[1:]
                # 최신 버전 선택
                target_version = max(releases.keys(), key=lambda x: packaging.version.parse(x))
            else:
                # 기본적으로 최신 버전 선택
                target_version = max(releases.keys(), key=lambda x: packaging.version.parse(x))
        else:
            # 버전이 지정되지 않은 경우 최신 버전 선택
            target_version = max(releases.keys(), key=lambda x: packaging.version.parse(x))
            
        if not target_version:
            print(f"  [경고] {package_name} {version}에 대한 호환되는 버전을 찾을 수 없습니다.")
            return files
            
        # 선택된 버전의 파일들 처리
        for file_info in releases[target_version]:
            filename = file_info['filename']
            url = file_info['url']
            
            # wheel 파일인 경우
            if filename.endswith('.whl'):
                # Python 버전 호환성 확인
                wheel_info = parse_wheel_tag(filename)
                if not wheel_info:
                    continue
                    
                # Python 버전 호환성 확인
                if not is_python_version_compatible(wheel_info['python_tag'], python_version):
                    continue
                    
                # 플랫폼 호환성 확인
                platform = get_platform_from_wheel(wheel_info['platform_tag'])
                if not platform:
                    continue
                    
                print(f"  호환되는 wheel 파일 발견: {filename}")
                files.append((url, filename, platform))
                
            # tar.gz 소스 파일인 경우
            elif filename.endswith('.tar.gz'):
                print(f"  파일 발견: {filename} (플랫폼: 공통)")
                files.append((url, filename, 'common'))
                
    except Exception as e:
        print(f"  [경고] 파일 정보를 가져오는 중 오류 발생: {str(e)}")
        
    return files

def download_package_files(package_name: str, version: str, python_version: str, processed_packages: set) -> None:
    """패키지 파일을 다운로드합니다."""
    # 이미 처리된 패키지인지 확인 (다운로드된 패키지 리스트 기반)
    package_key = package_name.lower()
    if package_key in processed_packages:
        print(f"[SKIP] 이미 다운로드됨: {package_name}")
        return
    processed_packages.add(package_key)

    print(f"\n패키지 다운로드 시작: {package_name} (버전: {version})")
    
    # PyPI API 호출
    print(f"PyPI API 호출: {package_name} (요청 버전: {version})")
    files = get_package_files(package_name, version, python_version)
    
    if not files:
        print(f"  [경고] {package_name} {version}에 대한 호환되는 파일을 찾을 수 없습니다.")
        return

    # 파일 다운로드
    print(f"발견된 파일 수: {len(files)}")
    for file_info in files:
        url = file_info[0]
        filename = file_info[1]
        platform = file_info[2]
        
        print(f"  파일: {filename} (플랫폼: {platform})")
        
        # 플랫폼별 디렉토리 결정
        if platform == 'win':
            target_dirs = [f"pypackage_win_x86_64_py{python_version.replace('.', '')}"]
        elif platform == 'linux':
            target_dirs = [f"pypackage_linux_amd64_py{python_version.replace('.', '')}"]
        else:  # common
            target_dirs = [
                f"pypackage_win_x86_64_py{python_version.replace('.', '')}",
                f"pypackage_linux_amd64_py{python_version.replace('.', '')}"
            ]
        
        # 각 타겟 디렉토리에 다운로드
        for target_dir in target_dirs:
            # 디렉토리 생성
            os.makedirs(target_dir, exist_ok=True)
            
            # 파일 다운로드
            target_path = os.path.join(target_dir, filename)
            print(f"    다운로드: {url} -> {target_path}")
            
            try:
                response = requests.get(url, stream=True)
                response.raise_for_status()
                
                total_size = int(response.headers.get('content-length', 0))
                block_size = 1024  # 1 KB
                
                with open(target_path, 'wb') as f, tqdm(
                    desc=filename,
                    total=total_size,
                    unit='B',
                    unit_scale=True,
                    unit_divisor=1024,
                ) as bar:
                    for data in response.iter_content(block_size):
                        f.write(data)
                        bar.update(len(data))
                        
            except Exception as e:
                print(f"    [오류] 다운로드 실패: {str(e)}")
                if os.path.exists(target_path):
                    os.remove(target_path)
                continue

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

def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description='Python 패키지 다운로더')
    parser.add_argument('--requirements-path', type=str, help='requirements.txt 파일 경로')
    parser.add_argument('--python-version', type=str, default='3.12', help='Python 버전 (기본값: 3.12)')
    args = parser.parse_args()

    if not args.requirements_path:
        parser.print_help()
        return

    # requirements.txt 파일 읽기
    requirements = parse_requirements(args.requirements_path)
    if not requirements:
        print("패키지 정보를 찾을 수 없습니다.")
        return

    # 처리된 패키지 추적을 위한 세트
    processed_packages = set()

    # 각 패키지 처리
    for package_name, version_spec in requirements:
        print(f"\n[패키지 처리] {package_name} {version_spec if version_spec else ''}")
        # 패키지 다운로드
        download_package_files(package_name, version_spec, args.python_version, processed_packages)
        # 의존성 가져오기
        dependencies = get_package_dependencies(package_name, version_spec, processed_packages)
        for dep_name, dep_version_spec in dependencies:
            download_package_files(dep_name, dep_version_spec, args.python_version, processed_packages)

    # 설치 스크립트 생성
    target_dirs = {
        'win': f"pypackage_win_x86_64_py{args.python_version.replace('.', '')}",
        'linux': f"pypackage_linux_amd64_py{args.python_version.replace('.', '')}"
    }
    create_install_scripts(target_dirs, args.python_version)

if __name__ == "__main__":
    main()
