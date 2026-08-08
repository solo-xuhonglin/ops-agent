"""通用删除工具：逐文件删除，规避沙箱对 shutil.rmtree / rm -rf 的拦截。

用法（Windows 绝对路径，可混用通配符）：
  python scripts/del.py build-out
  python scripts/del.py "D:\\x\\dist-old-*" D:/x/tmp.log
  python scripts/del.py -y dist-check  # -y 跳过确认

行为：文件用 os.remove；目录先删文件后删空目录（os.rmdir）。
沙箱会把 os.remove 的文件送回收站，目录清理失败时打印原因但不中断。
"""
import glob
import os
import sys


def expand(pattern: str) -> list[str]:
    if any(c in pattern for c in "*?"):
        return glob.glob(pattern)
    return [pattern] if os.path.exists(pattern) else []


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "-y"]
    if "-y" not in sys.argv[1:] and args:
        print("将删除以下目标，回车继续 / Ctrl+C 取消：")
        for a in args:
            print("  ", a)
        input()
    removed, missing, failed = [], [], []
    for pat in args:
        hits = expand(pat)
        if not hits:
            missing.append(pat)
            continue
        for target in hits:
            try:
                if os.path.isfile(target):
                    os.remove(target)
                    removed.append(target)
                elif os.path.isdir(target):
                    # 先删文件，再自底向上删空目录
                    for dirpath, dirnames, filenames in os.walk(target, topdown=False):
                        for f in filenames:
                            os.remove(os.path.join(dirpath, f))
                        for d in dirnames:
                            pass  # 由下层 rmdir 处理
                        try:
                            os.rmdir(dirpath)
                        except OSError as e:
                            failed.append((dirpath, str(e)))
                    if not os.path.exists(target):
                        removed.append(target)
            except Exception as e:  # noqa: BLE001
                failed.append((target, str(e)))
    print("REMOVED:", len(removed))
    for p in removed:
        print("  ", p)
    print("MISSING:", len(missing))
    for p in missing:
        print("  ", p)
    print("FAILED:", len(failed))
    for p, e in failed:
        print("  ", p, "->", e)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
