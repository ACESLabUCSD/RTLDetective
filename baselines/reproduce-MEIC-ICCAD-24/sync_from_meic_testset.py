
import argparse
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.normpath(os.path.join(HERE, "..", "..", "testset", "MEIC_Testset"))
DST = os.path.join(HERE, "ErrorSet")


def read_lf(path):
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true")
    args = ap.parse_args()

    stats = {"added": 0, "updated": 0, "unchanged": 0, "extra": 0}
    for design in sorted(os.listdir(args.src)):
        sdir = os.path.join(args.src, design)
        if not os.path.isdir(sdir):
            continue
        ddir = os.path.join(DST, design)
        pairs = [(os.path.join(sdir, "buggy", f), os.path.join(ddir, f))
                 for f in sorted(os.listdir(os.path.join(sdir, "buggy"))) if f.endswith(".v")]
        wanted = {os.path.basename(d) for _, d in pairs}
        pairs.append((os.path.join(sdir, "benchmarks", "tb.v"), os.path.join(ddir, "tb.v")))
        pairs.append((os.path.join(sdir, "design_description.txt"),
                      os.path.join(ddir, "design_description.txt")))

        for src, dst in pairs:
            data = read_lf(src)
            if not os.path.exists(dst):
                status = "added"
            elif read_lf(dst) == data:
                status = "unchanged"
            else:
                status = "updated"
            stats[status] += 1
            if status != "unchanged":
                print(f"{status:8s} {os.path.relpath(dst, HERE)}")
                if not args.dry_run:
                    os.makedirs(ddir, exist_ok=True)
                    with open(dst, "wb") as f:
                        f.write(data)

        if os.path.isdir(ddir):
            for f in sorted(os.listdir(ddir)):
                if f.endswith(".v") and f != "tb.v" and f not in wanted:
                    stats["extra"] += 1
                    print(f"{'pruned' if args.prune else 'EXTRA':8s} {os.path.relpath(os.path.join(ddir, f), HERE)}")
                    if args.prune and not args.dry_run:
                        os.remove(os.path.join(ddir, f))

    print(stats)


if __name__ == "__main__":
    main()
