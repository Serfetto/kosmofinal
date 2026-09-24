"""Полный конвейер для акватории: скачать снимки → детекция → агрегация.

python -m pipeline.build run sochi vladivostok
python -m pipeline.build add azov "Таганрогский залив" 38.6 46.9 39.3 47.3 --port 38.93,47.2 --tz 3
"""
import argparse
import json

from . import aggregate, detect, download
from .config import AOIS, DATA


def add(args) -> None:
    path = DATA / "aois.json"
    extra = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    lon, lat = map(float, args.port.split(","))
    extra[args.id] = {"name": args.name, "bbox": [args.lon0, args.lat0, args.lon1, args.lat1],
                      "kind": args.kind, "tz": args.tz, "port": [lon, lat], "rivers": {}}
    path.write_text(json.dumps(extra, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"добавлена акватория {args.id}; запустите: python -m pipeline.build run {args.id}")


def run(args) -> None:
    for a in args.aois or list(AOIS):
        download.run(a)
        detect.run(a)
        aggregate.run(a)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("aois", nargs="*")
    a = sub.add_parser("add")
    a.add_argument("id")
    a.add_argument("name")
    for k in ("lon0", "lat0", "lon1", "lat1"):
        a.add_argument(k, type=float)
    a.add_argument("--kind", choices=["sea", "inland"], default="sea")
    a.add_argument("--port", required=True, help="lon,lat точки выхода судна")
    a.add_argument("--tz", type=int, default=3)
    args = p.parse_args()
    {"add": add, "run": run}[args.cmd](args)
