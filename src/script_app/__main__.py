from __future__ import annotations

import argparse
from pathlib import Path

from .content_handoff import write_content_factory_card_from_file, write_content_factory_task_from_file
from .db import init_db
from .exporter import export_video_script
from .paths import DEFAULT_DB_PATH
from .seeds import seed_challenge, seed_challenge_backlog, seed_explain_with_images, seed_soprovod


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m src.script_app")
    parser.add_argument("command", nargs="?", default="serve")
    parser.add_argument("id", nargs="?")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--input")
    parser.add_argument("--inbox-dir")
    parser.add_argument("--outbox-dir")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    db_path = Path(args.db)

    if args.command == "init-db":
        init_db(db_path)
        print(f"База готова: {db_path}")
        return
    if args.command == "seed-soprovod":
        print(f"Загружено сценариев «Видео под ключ»: {seed_soprovod(db_path)}")
        return
    if args.command == "seed-explain-with-images":
        print(f"Загружено сценариев «Объяснять с картинками»: {seed_explain_with_images(db_path)}")
        return
    if args.command == "seed-challenge":
        print(f"Загружено сценариев челенджа: {seed_challenge(db_path)}")
        return
    if args.command == "seed-challenge-backlog":
        print(f"Загружено тем бэклога челенджа: {seed_challenge_backlog(db_path)}")
        return
    if args.command == "export-video-script":
        if args.id is None:
            raise SystemExit("Для export-video-script нужен id")
        path = export_video_script(int(args.id), db_path)
        print(path)
        return
    if args.command == "export-content-card":
        if not args.input:
            raise SystemExit("Для export-content-card нужен --input")
        if args.outbox_dir:
            path = write_content_factory_card_from_file(Path(args.input), outbox_dir=Path(args.outbox_dir))
        else:
            path = write_content_factory_card_from_file(Path(args.input))
        print(path)
        return
    if args.command == "import-content-task":
        if not args.input:
            raise SystemExit("Для import-content-task нужен --input")
        if args.inbox_dir:
            path = write_content_factory_task_from_file(Path(args.input), inbox_dir=Path(args.inbox_dir))
        else:
            path = write_content_factory_task_from_file(Path(args.input))
        print(path)
        return
    if args.command != "serve":
        raise SystemExit(f"Неизвестная команда: {args.command}")

    import uvicorn

    uvicorn.run("src.script_app.web:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
