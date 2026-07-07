"""
Plashker — CLI рендера одного формата (проверка движка end-to-end).

  python -m tools.render_one 16x9 --bg --safe -o out.png
  python -m tools.render_one --all -o renders/

Без аргументов рендерит все форматы демо-проекта MICHAEL в renders/.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from plashker.project import AppConfig, Project  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("format", nargs="?", help="ключ формата, напр. 16x9")
    ap.add_argument("--all", action="store_true", help="рендерить все форматы")
    ap.add_argument("--bg", action="store_true", help="декоративный фон (превью)")
    ap.add_argument("--safe", action="store_true", help="оверлей safe zone")
    ap.add_argument("-o", "--out", default="renders", help="файл или папка вывода")
    args = ap.parse_args()

    app = AppConfig.load(os.path.join(ROOT, "data"))
    project = Project.open_dir(os.path.join(ROOT, "examples", "MICHAEL"))

    keys = project.format_keys if (args.all or not args.format) else [args.format]

    if len(keys) > 1 or os.path.isdir(args.out) or not args.out.endswith(".png"):
        os.makedirs(args.out, exist_ok=True)
        for k in keys:
            img = project.render(app, k, with_background=args.bg,
                                 with_safe_zone=args.safe)
            p = os.path.join(args.out, f"{k}.png")
            img.save(p)
            print(f"{k:16} {img.width}x{img.height}  -> {p}")
    else:
        img = project.render(app, keys[0], with_background=args.bg,
                             with_safe_zone=args.safe)
        img.save(args.out)
        print(f"{keys[0]} -> {args.out}")


if __name__ == "__main__":
    main()
