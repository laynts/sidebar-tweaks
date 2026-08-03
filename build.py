# SPDX-FileCopyrightText: 2026 れいんつ
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""配布用の拡張機能zipを Release/ に、更新配信用のリポジトリを docs/ に書き出す。

zip化はBlender自身の `extension build` に任せる。マニフェストの検証と
__pycache__ などの除外まで面倒を見てくれるため、手でzipを作るより確実。

    python build.py

Blenderの実行ファイルは環境変数 BLENDER で差し替えられる。
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "sidebar_tweaks"
OUTPUT_DIR = ROOT / "Release"
REPO_DIR = ROOT / "docs"
PRODUCT = "SidebarTweaks"

BLENDER_CANDIDATES = (
    os.environ.get("BLENDER"),
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    "blender",
)


def find_blender():
    for path in BLENDER_CANDIDATES:
        if not path:
            continue
        if path == "blender" or Path(path).exists():
            return path
    raise SystemExit(
        "Blenderが見つかりません。環境変数 BLENDER に実行ファイルのパスを指定してください。")


def read_version():
    text = (SOURCE_DIR / "blender_manifest.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("blender_manifest.toml から version を読めませんでした。")
    return match.group(1)


def run_blender(blender, *args):
    command = [blender, "--factory-startup", "--command", *args]
    print("$ " + " ".join(command))
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")

    # 他アドオンの読み込みエラーが混ざるので、結果行だけ拾う
    for line in (result.stdout or "").splitlines():
        if line.strip():
            print(line)
    if result.returncode != 0:
        print(result.stderr or "", file=sys.stderr)
        raise SystemExit("失敗しました（終了コード {}）".format(result.returncode))


def build_zip(blender, version):
    OUTPUT_DIR.mkdir(exist_ok=True)
    output = OUTPUT_DIR / "{}_v{}.zip".format(PRODUCT, version)
    run_blender(blender, "extension", "build",
                "--source-dir", str(SOURCE_DIR),
                "--output-filepath", str(output))
    if not output.exists():
        raise SystemExit("zipが作られませんでした: {}".format(output))
    return output


def update_repo(blender, archive):
    """GitHub Pages で配る更新配信用のリポジトリを作り直す。

    ユーザーがBlender上で更新を受け取れるのは、リモートリポジトリとして
    登録された index.json 経由のときだけ。index.json の archive_url は
    同じディレクトリへの相対パスになるため、zipを並べて置く必要がある。

    載せるのは最新版だけにする。旧版はGitHub Releasesに残る。
    """
    REPO_DIR.mkdir(exist_ok=True)
    for old in REPO_DIR.glob("{}_v*.zip".format(PRODUCT)):
        if old.name != archive.name:
            old.unlink()
            print("旧版を消しました: {}".format(old.name))
    shutil.copy2(archive, REPO_DIR / archive.name)
    run_blender(blender, "extension", "server-generate",
                "--repo-dir", str(REPO_DIR), "--html")

    index = REPO_DIR / "index.json"
    if not index.exists():
        raise SystemExit("index.jsonが作られませんでした: {}".format(index))


def main():
    blender = find_blender()
    version = read_version()

    output = build_zip(blender, version)
    print("できました: {} ({:,} bytes)".format(output, output.stat().st_size))

    update_repo(blender, output)
    print("更新配信用のリポジトリを更新しました: {}".format(REPO_DIR))
    print("docs/ をコミットしてpushすると、ユーザーのBlenderに更新が届きます。")


if __name__ == "__main__":
    main()
