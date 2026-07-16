"""audit_vmless_boot_image — verify SIMANTW's boot image is data-only.

Thin CLI over the generic ``win16.bootimage.audit_boot_image`` (dos_re_2.0
§1a'): no bundled executable, every recovered code byte poisoned or declared
``code_as_data``, and every nonzero byte inside a code segment accounted for
(IR-decoded or declared).  Exit 0 = PASS.

    python scripts/audit_vmless_boot_image.py [--boot-dir artifacts/vmless_boot]
                                              [--ir artifacts/recovery_ir.json]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--boot-dir",
                    default=str(REPO_ROOT / "artifacts" / "vmless_boot"))
    ap.add_argument("--ir", default=str(REPO_ROOT / "artifacts" / "recovery_ir.json"))
    args = ap.parse_args(argv)

    from win16.bootimage import audit_boot_image
    fails, info = audit_boot_image(args.boot_dir, args.ir)
    print(f"boot image audit: {args.boot_dir}")
    for line in info:
        print(f"  {line}")
    if fails:
        print(f"FAIL — {len(fails)} problem(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS — the boot image is a legitimate data-only artifact; all "
          "recovered code is poisoned or declared code_as_data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
