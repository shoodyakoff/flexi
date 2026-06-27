#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.meme_video import (  # noqa: E402
    BILLION_PROFILE,
    DEFAULT_BROLL_DIR,
    DEFAULT_DRAFT_ROOT,
    DEFAULT_FONT_FILE,
    DEFAULT_PUBLISH_ROOT,
    build_jobs,
    collect_broll_files,
    load_texts_file,
    render_job,
    resolve_template_path,
)
from src.output_paths import versioned_dir  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render batch meme videos from a draft template, replacement b-roll, and text list."
    )
    parser.add_argument("--template", required=True, type=Path, help="Template video path or name under drafts root.")
    parser.add_argument("--series", required=True, help="Publish folder name and output slug.")
    parser.add_argument("--texts-file", required=True, type=Path, help="Numbered text list, one variant per line.")
    parser.add_argument("--outro-text", required=True, help="Fixed text drawn over the template outro.")
    parser.add_argument("--broll-dir", type=Path, default=DEFAULT_BROLL_DIR)
    parser.add_argument("--draft-root", type=Path, default=DEFAULT_DRAFT_ROOT)
    parser.add_argument("--publish-root", type=Path, default=DEFAULT_PUBLISH_ROOT)
    parser.add_argument("--font-file", type=Path, default=DEFAULT_FONT_FILE)
    parser.add_argument("--work-root", type=Path, default=ROOT / "output" / "meme_video")
    parser.add_argument("--version", help="Render into a specific version folder, e.g. v2.")
    parser.add_argument("--profile", choices=["billion"], default="billion")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = BILLION_PROFILE
    template = resolve_template_path(args.template, draft_root=args.draft_root)
    texts = load_texts_file(args.texts_file)
    if not texts:
        raise ValueError(f"No texts found in {args.texts_file}")

    broll_dir = args.broll_dir if args.broll_dir.is_absolute() else ROOT / args.broll_dir
    broll_files = collect_broll_files(broll_dir)
    publish_dir = args.publish_root / args.series
    work_dir = versioned_dir(args.work_root / args.series, version=args.version)

    jobs = build_jobs(texts=texts, broll_files=broll_files, publish_dir=publish_dir)
    manifest = {
        "profile": profile.name,
        "template": str(template),
        "series": args.series,
        "outro_text": args.outro_text,
        "publish_dir": str(publish_dir),
        "work_dir": str(work_dir),
        "jobs": [
            {
                "index": job.index,
                "text": job.text,
                "broll": str(job.broll_path),
                "final": str(job.final_path),
            }
            for job in jobs
        ],
    }
    (work_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Template: {template}")
    print(f"B-roll files: {len(broll_files)}")
    print(f"Texts: {len(texts)}")
    print(f"Work dir: {work_dir}")
    print(f"Publish dir: {publish_dir}")

    for job in jobs:
        print(f"[{job.index:02d}/{len(jobs):02d}] {job.final_path.name}")
        render_job(
            job=job,
            template_path=template,
            profile=profile,
            work_dir=work_dir,
            font_file=args.font_file,
            outro_text=args.outro_text,
        )

    print("Done")


if __name__ == "__main__":
    main()
