from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .collector import CollectionError, collect
from .database import replace_season_data
from .images import attach_cached_hero_images, download_hero_images


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="采集金铲铲官网英雄数据并写入 SQLite")
    parser.add_argument("--db", type=Path, default=Path("data/jcc.db"), help="SQLite 文件路径")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"), help="官网原始快照目录")
    parser.add_argument("--mode", help="指定玩法 mode；默认读取官网当前 mode")
    parser.add_argument("--season", help="指定赛季，例如 S18；默认读取官网当前赛季")
    parser.add_argument("--version", help="指定精确版本；默认选择当前日期已生效的最新版本")
    parser.add_argument(
        "--assets-dir", type=Path, default=Path("data/assets"), help="本地图片资源目录"
    )
    parser.add_argument("--skip-images", action="store_true", help="跳过英雄图片下载")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        version, heroes, traits, equipment, augments, metadata = collect(
            args.raw_dir,
            mode=args.mode,
            season=args.season,
            requested_version=args.version,
        )
        image_counts = (
            attach_cached_hero_images(heroes, version, args.assets_dir)
            if args.skip_images
            else download_hero_images(heroes, version, args.assets_dir)
        )
        counts = replace_season_data(
            args.db, version, heroes, traits, equipment, augments, metadata
        )
    except CollectionError as exc:
        print(f"采集失败：{exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "database": str(args.db),
                "mode": version.mode,
                "season": version.season,
                "version": version.version,
                "name": version.name,
                **counts,
                "hero_images": image_counts,
                "snapshot": metadata["snapshot_dir"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
