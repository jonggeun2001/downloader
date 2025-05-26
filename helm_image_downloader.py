import argparse
import os
import tempfile
import subprocess
import yaml
import requests
import tarfile
import shutil

def extract_chart(tgz_path, extract_dir):
    with tarfile.open(tgz_path, "r:gz") as tar:
        tar.extractall(path=extract_dir)
    return extract_dir

def find_image_references(yaml_obj):
    images = set()
    if isinstance(yaml_obj, dict):
        for k, v in yaml_obj.items():
            if k == 'image':
                if isinstance(v, dict) and 'repository' in v and 'tag' in v:
                    images.add(f"{v['repository']}:{v['tag']}")
                elif isinstance(v, str):
                    images.add(v)
            else:
                images.update(find_image_references(v))
    elif isinstance(yaml_obj, list):
        for item in yaml_obj:
            images.update(find_image_references(item))
    return images

def extract_images_from_chart(chart_dir):
    images = set()
    rendered_dir = os.path.join('download_images', 'rendered')
    os.makedirs(rendered_dir, exist_ok=True)

    def render_and_extract(chart_path):
        chart_yaml = os.path.join(chart_path, 'Chart.yaml')
        if not os.path.isfile(chart_yaml):
            return set()
        chart_name = os.path.basename(chart_path.rstrip('/'))
        rendered_path = os.path.join(rendered_dir, f'{chart_name}_rendered.yaml')
        try:
            rendered = subprocess.check_output([
                'helm', 'template', chart_path
            ])
            with open(rendered_path, 'w') as f:
                f.write(rendered.decode())
            local_images = set()
            with open(rendered_path) as f:
                docs = list(yaml.safe_load_all(f))
                for doc in docs:
                    local_images.update(find_image_references(doc))
            return local_images
        except Exception as e:
            print(f"[WARN] {chart_path} 렌더링 실패: {e}")
            return set()

    # chart_dir 하위 모든 디렉토리에서 Chart.yaml이 있으면 helm template 실행
    for root, dirs, files in os.walk(chart_dir):
        if 'Chart.yaml' in files:
            images.update(render_and_extract(root))
    return images

def pull_images(images, images_dir='download_images/images', repository_prefix='mirror-registry.dp-dev.kbstar.com:5000/'):
    os.makedirs(images_dir, exist_ok=True)
    for image in images:
        print(f"이미지 다운로드: {image}")
        subprocess.run(["podman", "pull", "--platform=linux/amd64", image], check=True)
        # 레포지토리 prefix 변경
        image_name = image.split('/')[-1]
        new_image = repository_prefix.rstrip('/') + '/' + image_name
        print(f"이미지 태그 변경: {image} → {new_image}")
        subprocess.run(["podman", "tag", image, new_image], check=True)
        safe_image = new_image.replace('/', '_').replace(':', '_')
        tar_path = os.path.join(images_dir, f"{safe_image}.tar")
        print(f"이미지 저장: {tar_path}")
        subprocess.run(["podman", "save", "-o", tar_path, new_image], check=True)

def extract_images_from_yaml(yaml_path):
    images = set()
    with open(yaml_path) as f:
        docs = list(yaml.safe_load_all(f))
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if doc.get('kind') == 'Deployment':
                spec = doc.get('spec', {})
                template = spec.get('template', {})
                containers = template.get('spec', {}).get('containers', [])
                for c in containers:
                    image = c.get('image')
                    if image:
                        images.add(image)
    return images

def main():
    parser = argparse.ArgumentParser(description='Helm 차트(tgz) 또는 yaml에서 이미지 다운로드')
    parser.add_argument('input_file', help='helm chart tgz 또는 yaml/yml 파일 경로')
    parser.add_argument('--repository-prefix', default='mirror-registry.dp-dev.kbstar.com:5000/', help='저장할 이미지 레포지토리 prefix')
    args = parser.parse_args()

    base_dir = os.path.abspath('download_images')
    if os.path.exists(base_dir):
        shutil.rmtree(base_dir)
    os.makedirs(base_dir, exist_ok=True)

    chart_dir = os.path.join(base_dir, 'chart')
    images_dir = os.path.join(base_dir, 'images')
    os.makedirs(chart_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    extract_chart(args.input_file, chart_dir)

    return True
    images = set()
    if args.input_file.endswith(('.yaml', '.yml')):
        images = extract_images_from_yaml(args.input_file)
    elif args.input_file.endswith('.tgz'):
        extract_chart(args.input_file, chart_dir)
        images = extract_images_from_chart(chart_dir)
    else:
        print('지원하지 않는 파일 형식입니다.')
        return

    if not images:
        print('이미지 없음')
        return
    pull_images(images, images_dir, args.repository_prefix)

if __name__ == '__main__':
    main()
