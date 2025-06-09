#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import shutil
from pathlib import Path
import glob

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

def write_temp_requirements(requirements_path, package_names, temp_path):
    with open(requirements_path, 'r') as f:
        lines = f.readlines()
    with open(temp_path, 'w') as f:
        for line in lines:
            pkg = line.strip().split('==')[0]
            if pkg in package_names:
                f.write(line)

def download_packages(requirements_path, python_version):
    """
    requirements.txt 파일에 명시된 패키지와 의존성을 다운로드합니다.
    
    Args:
        requirements_path (str): requirements.txt 파일 경로
        python_version (str): Python 버전
    """
    # Python 버전에서 점(.)을 언더스코어(_)로 변경
    version_suffix = python_version.replace('.', '_')
    
    # Windows와 Linux용 다운로드 디렉토리 설정
    win_dir = f"pypackage_win_x86_64_py{version_suffix}"
    linux_dir = f"pypackage_linux_amd64_py{version_suffix}"
    source_dir = f"source_packages_py{version_suffix}"
    
    # 다운로드 디렉토리 생성
    Path(win_dir).mkdir(parents=True, exist_ok=True)
    Path(linux_dir).mkdir(parents=True, exist_ok=True)
    Path(source_dir).mkdir(parents=True, exist_ok=True)
    
    # 1. Windows용 wheel 다운로드 (의존성 포함)
    win_cmd = [
        sys.executable, "-m", "pip", "download",
        "-r", requirements_path,
        "-d", win_dir,
        "--platform", "win_amd64",
        "--python-version", python_version,
        "--only-binary=:all:"
    ]
    print("Windows용 패키지 다운로드 중...")
    subprocess.run(win_cmd, check=False)
    print(f"Windows용 패키지 다운로드 완료: {win_dir}")

    # 2. Linux용 wheel 다운로드 (의존성 포함)
    linux_cmd = [
        sys.executable, "-m", "pip", "download",
        "-r", requirements_path,
        "-d", linux_dir,
        "--platform", "manylinux2014_x86_64",
        "--python-version", python_version,
        "--only-binary=:all:"
    ]
    print("Linux용 패키지 다운로드 중...")
    subprocess.run(linux_cmd, check=False)
    print(f"Linux용 패키지 다운로드 완료: {linux_dir}")

    # 3. wheel 없는 패키지 추출
    missing_win = get_missing_wheel_packages(requirements_path, win_dir)
    missing_linux = get_missing_wheel_packages(requirements_path, linux_dir)
    print(f"\nWindows용 wheel 없는 패키지: {missing_win}")
    print(f"Linux용 wheel 없는 패키지: {missing_linux}")

    # 4. 소스 배포판 다운로드 (필요한 경우만, 의존성 포함)
    need_source_pkgs = set(missing_win + missing_linux)
    if need_source_pkgs:
        temp_req = f"temp_requirements_{version_suffix}.txt"
        write_temp_requirements(requirements_path, need_source_pkgs, temp_req)
        source_cmd = [
            sys.executable, "-m", "pip", "download",
            "-r", temp_req,
            "-d", source_dir,
            "--no-binary", ":all:"
        ]
        print("\n소스 배포판 다운로드 중...")
        subprocess.run(source_cmd, check=True)
        print(f"소스 배포판 다운로드 완료: {source_dir}")
        print("\n소스 패키지를 플랫폼 디렉토리로 복사 중...")
        copy_source_packages(source_dir, [win_dir, linux_dir])
        os.remove(temp_req)
    else:
        print("\n모든 패키지가 wheel 파일로 다운로드되었습니다. 소스 배포판 다운로드가 필요하지 않습니다.")

    if os.path.exists(source_dir):
        shutil.rmtree(source_dir)
        print(f"\n임시 소스 디렉토리({source_dir})가 삭제되었습니다.")

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
