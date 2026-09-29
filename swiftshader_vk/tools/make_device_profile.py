"""Freeze the oracle's device profile into spec/device_profile.json.

    python3 swiftshader_vk/tools/make_device_profile.py [--image ssvk-ref:1] [--check]

Runs `vulkaninfo` in the reference image against the oracle of record (LLVM
backend) and merges three sources:
  - the Vulkan Profiles JSON export (`vulkaninfo --json`): extensions,
    features, properties, format properties, queue families;
  - memory heaps and types, and the loader-visible instance extensions, parsed
    from the text report (the profiles format carries neither).
The Subzero backend is dumped too and must agree on everything except
deviceName (Stage 3 finding); the script fails if it does not. `--check`
regenerates and compares with the committed file instead of writing it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "spec" / "device_profile.json"


def docker(image: str, *args: str, mount: str | None = None) -> str:
    cmd = ["docker", "run", "--rm", "--network", "none"]
    if mount:
        cmd += ["-v", f"{mount}:/o"]
    cmd += [image, *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def profile_json(image: str, icd: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        docker(image, "with", icd, "vulkaninfo", "--json=0", "--output", "/o/p.json", mount=d)
        return json.loads((Path(d) / "p.json").read_text())["capabilities"]["device"]


def parse_text(text: str) -> tuple[dict, dict]:
    """(memory properties, instance extensions) from the vulkaninfo text report."""
    inst = {}
    m = re.search(r"^Instance Extensions: count = \d+\n=+\n(.*?)\n\n", text, re.S | re.M)
    for line in m.group(1).splitlines():
        name, rev = re.match(r"\s*(\S+)\s*: extension revision (\d+)", line).groups()
        inst[name] = int(rev)
    mem = {"memoryHeaps": [], "memoryTypes": []}
    block = text[text.index("VkPhysicalDeviceMemoryProperties:"):]
    block = block[:block.index("\n\n")]
    for size, flags in re.findall(r"size\s+= (\d+).*?\n\t\tflags: count = \d+\n((?:\t\t\t\S+\n)*)", block):
        mem["memoryHeaps"].append({"size": int(size), "flags": ["VK_" + f for f in flags.split()]})
    for heap, flags in re.findall(r"heapIndex\s+= (\d+)\n\t\tpropertyFlags = \S+: count = \d+\n((?:\t\t\t\S+\n)*)", block):
        mem["memoryTypes"].append({"heapIndex": int(heap), "propertyFlags": ["VK_" + f for f in flags.split()]})
    assert mem["memoryHeaps"] and mem["memoryTypes"] and inst, "vulkaninfo text report changed shape"
    return mem, inst


def build(image: str) -> dict:
    llvm = profile_json(image, "llvm")
    subzero = profile_json(image, "subzero")
    a, b = json.loads(json.dumps(llvm)), json.loads(json.dumps(subzero))
    a["properties"]["VkPhysicalDeviceProperties"].pop("deviceName")
    b["properties"]["VkPhysicalDeviceProperties"].pop("deviceName")
    if a != b:
        sys.exit("LLVM and Subzero profiles differ beyond deviceName; investigate before freezing")
    mem, inst = parse_text(docker(image, "vulkaninfo", "llvm"))
    image_id = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                              check=True, capture_output=True, text=True).stdout.strip()
    return {
        "_about": {
            "what": "Device profile of the oracle of record (SwiftShader, LLVM 10 backend). A candidate "
                    "must report these values; deviceName is the one field that differs between backends.",
            "instanceExtensions": "as the loader reports them with only this ICD visible; includes loader-provided "
                                  "ones (debug_report, debug_utils, portability_enumeration, direct_driver_loading)",
            "image": image_id,
            "generator": "swiftshader_vk/tools/make_device_profile.py",
        },
        "instanceExtensions": inst,
        "device": llvm,
        "memoryProperties": mem,
    }


def canonical(d: dict) -> str:
    return json.dumps(d, indent=1, sort_keys=True) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="ssvk-ref:1")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    prof = build(a.image)
    body = {k: v for k, v in prof.items() if k != "_about"}
    digest = hashlib.sha256(canonical(body).encode()).hexdigest()
    prof["_about"]["sha256_of_body"] = digest
    if a.check:
        old = json.loads(OUT.read_text())
        same = old["_about"]["sha256_of_body"] == digest
        print(f"{'IDENTICAL' if same else 'DIFFERENT'} body sha256 {digest}")
        sys.exit(0 if same else 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(canonical(prof))
    dev = prof["device"]
    print(f"wrote {OUT}: {len(dev['extensions'])} device extensions, {len(prof['instanceExtensions'])} instance, "
          f"{len(dev['formats'])} formats, body sha256 {digest}")


if __name__ == "__main__":
    main()
