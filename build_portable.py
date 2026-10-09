"""Build an allowlisted source-only package. Never package user articles/auth."""
import json
from pathlib import Path
import zipfile

BASE = Path(__file__).resolve().parent
FILES = [
    'Initialize-Bridge.ps1', '一键安装初始化.bat', '检查安装环境.bat', '登录Codex.bat',
    '保存草稿.bat', '正式发布.bat', '检查HTML.bat', '新电脑使用说明.md',
    'HTML自动发布说明.md', '迁移包验证说明.md', 'requirements-lock.txt', 'requirements.txt',
    'package.json', 'package-lock.json', 'configure.py', 'check_connection.py',
    'yinxiang_server.py', 'publish_html.py', 'login_codex.py', 'publish-result.schema.json',
    'test_publish_html.py', 'test_yinxiang.py', 'test_configure.py', 'build_portable.py',
]


def build(destination):
    destination = Path(destination)
    for filename in FILES:
        if not (BASE / filename).is_file():
            raise FileNotFoundError(filename)
    # Force generic defaults, never copy personalized runtime/settings files.
    defaults = {'platforms': ['zhihu', 'csdn'], 'codex_model': '',
                'csdn_article_type': '', 'repost_source_url': ''}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for filename in FILES:
            archive.write(BASE / filename, 'HTML自动发布工具/' + filename)
        archive.writestr('HTML自动发布工具/publish-settings.json', json.dumps(defaults, ensure_ascii=False, indent=2))
    print(destination)
    return destination


if __name__ == '__main__':
    build(BASE.parent / 'HTML自动发布工具-Windows迁移包.zip')
