"""Generate vkreplay's Vulkan tables from the pinned registry (vk.xml).

    python3 gen_vk_tables.py <vk.xml> <out.inc> [<formats.json>]

Emits C++ (included once, by src/vk_tables.cpp):

  kEnums[]        every enum and flag-bit value: (type, name, value)
  kFlagTypes[]    VkFooFlags -> VkFooFlagBits (and whether it is 64-bit)
  kFormats[]      VkFormat layout from the registry's <formats> section
  ser_<Struct>()  JSON serializers for the physical-device query structs
  kStructs[]      struct registry: name, sType, size, serializer, VkBool32
                  fields (for enabling features by name), and which API
                  version / extensions make the struct usable

Only structs vkreplay queries are generated: VkPhysicalDeviceFeatures and
VkPhysicalDeviceProperties, everything that extends VkPhysicalDeviceFeatures2
or VkPhysicalDeviceProperties2, the format / queue family / memory / image
format property structs, and whatever they nest. Provisional (beta), platform
and Vulkan SC-only definitions are skipped: they are not in vulkan_core.h.

The optional third argument also writes the format table as JSON, for
documentation and cross-checking the scorer's decoder.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET

SCALARS = {
    "uint8_t": "u", "int8_t": "i", "uint16_t": "u", "int16_t": "i", "uint32_t": "u", "int32_t": "i",
    "uint64_t": "u", "int64_t": "i", "size_t": "u", "float": "f", "double": "f",
    "VkDeviceSize": "u", "VkDeviceAddress": "u", "VkBool32": "b",
}
ROOT_STRUCTS = ["VkPhysicalDeviceFeatures", "VkPhysicalDeviceProperties", "VkFormatProperties",
                "VkFormatProperties3", "VkQueueFamilyProperties", "VkPhysicalDeviceMemoryProperties",
                "VkImageFormatProperties", "VkQueueFamilyGlobalPriorityProperties"]
EXTENDS = {"VkPhysicalDeviceFeatures2": "feature", "VkPhysicalDeviceProperties2": "property"}


def api_ok(el) -> bool:
    api = el.get("api")
    return api is None or "vulkan" in api.split(",")


def main(xml_path: str, out_path: str, formats_json: str | None) -> None:
    reg = ET.parse(xml_path).getroot()

    # ---------------------------------------------------------------- exclusions
    excluded_types: set[str] = set()
    included_by: dict[str, set[str]] = {}          # type -> requirement names (versions / extensions)
    ext_numbers: dict[str, int] = {}
    for ext in reg.find("extensions"):
        name = ext.get("name")
        ext_numbers[name] = int(ext.get("number"))
        supported = (ext.get("supported") or "").split(",")
        bad = "vulkan" not in supported or ext.get("platform") or ext.get("provisional") == "true"
        for req in ext.findall("require"):
            if not api_ok(req):
                continue
            for t in req.findall("type"):
                if bad:
                    excluded_types.add(t.get("name"))
                else:
                    included_by.setdefault(t.get("name"), set()).add(name)
    for feat in reg.findall("feature"):
        if not api_ok(feat):
            continue
        for req in feat.findall("require"):
            if not api_ok(req):
                continue
            for t in req.findall("type"):
                included_by.setdefault(t.get("name"), set()).add(feat.get("name"))
    # a type pulled in by any good requirement stays
    excluded_types -= set(included_by)

    # ---------------------------------------------------------------- enums
    enums: dict[str, list[tuple[str, int]]] = {}
    enum_kind: dict[str, str] = {}
    aliases: list[tuple[str, str, str]] = []       # (type, alias, target)
    for block in reg.findall("enums"):
        kind = block.get("type")
        if kind not in ("enum", "bitmask"):
            continue
        tname = block.get("name")
        enum_kind[tname] = kind
        vals = enums.setdefault(tname, [])
        for e in block.findall("enum"):
            if not api_ok(e):
                continue
            if e.get("alias"):
                aliases.append((tname, e.get("name"), e.get("alias")))
            elif e.get("bitpos") is not None:
                vals.append((e.get("name"), 1 << int(e.get("bitpos"))))
            elif e.get("value") is not None:
                vals.append((e.get("name"), int(e.get("value"), 0)))

    def ext_enum(e, extnumber):
        if e.get("alias"):
            aliases.append((e.get("extends"), e.get("name"), e.get("alias")))
            return
        if e.get("bitpos") is not None:
            v = 1 << int(e.get("bitpos"))
        elif e.get("offset") is not None:
            n = int(e.get("extnumber") or extnumber)
            v = 1000000000 + (n - 1) * 1000 + int(e.get("offset"))
            if e.get("dir") == "-":
                v = -v
        elif e.get("value") is not None:
            v = int(e.get("value"), 0)
        else:
            return
        lst = enums.setdefault(e.get("extends"), [])
        if (e.get("name"), v) not in lst:
            lst.append((e.get("name"), v))

    for ext in reg.find("extensions"):
        supported = (ext.get("supported") or "").split(",")
        if "vulkan" not in supported:
            continue
        for req in ext.findall("require"):
            if not api_ok(req):
                continue
            for e in req.findall("enum"):
                if e.get("extends") and api_ok(e):
                    ext_enum(e, ext.get("number"))
    for feat in reg.findall("feature"):
        if not api_ok(feat):
            continue
        for req in feat.findall("require"):
            if not api_ok(req):
                continue
            for e in req.findall("enum"):
                if e.get("extends") and api_ok(e):
                    ext_enum(e, None)
    # resolve aliases to values
    for tname, alias, target in aliases:
        if tname is None or tname not in enums:
            continue
        values = dict(enums[tname])
        seen = 0
        while target not in values and seen < 8:
            nxt = next((t for (tn, a, t) in aliases if tn == tname and a == target), None)
            if nxt is None:
                break
            target, seen = nxt, seen + 1
        if target in values and alias not in values:
            enums[tname].append((alias, values[target]))

    # ---------------------------------------------------------------- types
    types: dict[str, ET.Element] = {}
    type_alias: dict[str, str] = {}
    flag_types: dict[str, tuple[str, bool]] = {}   # VkFooFlags -> (VkFooFlagBits, is64)
    for t in reg.find("types"):
        if not api_ok(t):
            continue
        cat = t.get("category")
        name = t.get("name") or (t.find("name").text if t.find("name") is not None else None)
        if not name:
            continue
        if t.get("alias"):
            type_alias[name] = t.get("alias")
            continue
        if cat == "struct":
            types[name] = t
        elif cat == "bitmask":
            bits = t.get("requires") or t.get("bitvalues")
            base = t.find("type").text if t.find("type") is not None else "VkFlags"
            if bits:
                flag_types[name] = (bits, base == "VkFlags64")
        elif cat == "enum":
            enums.setdefault(name, enums.get(name, []))
    for alias, target in type_alias.items():
        if target in flag_types:
            flag_types[alias] = flag_types[target]

    def members(sname: str):
        out = []
        for m in types[sname].findall("member"):
            if not api_ok(m):
                continue
            mtype = m.find("type").text
            mname = m.find("name").text
            tail = (m.find("type").tail or "")
            pointer = "*" in tail
            ntail = m.find("name").tail or ""
            arr = None
            if ntail.startswith("["):
                en = m.find("enum")
                if en is not None:
                    arr = en.text
                else:
                    arr = re.match(r"\[(\d+)\]", ntail).group(1)
                if ntail.count("[") > 1 or (en is not None and (en.tail or "").count("[") > 0):
                    arr = "2d"
            out.append({"type": mtype, "name": mname, "pointer": pointer, "array": arr,
                        "values": m.get("values")})
        return out

    wanted: list[str] = []
    category: dict[str, str] = {}
    for sname, t in types.items():
        ext = (t.get("structextends") or "").split(",")
        for parent, cat in EXTENDS.items():
            # only structs some Vulkan (not Vulkan SC) version or supported extension requires
            if parent in ext and sname not in excluded_types and sname in included_by:
                wanted.append(sname)
                category[sname] = cat
    for r in ROOT_STRUCTS:
        r = type_alias.get(r, r)
        if r in types and r not in wanted:
            wanted.append(r)
            category.setdefault(r, "feature" if r == "VkPhysicalDeviceFeatures" else
                                "property" if r == "VkPhysicalDeviceProperties" else "other")

    # nested structs, depth-first so definitions precede use
    ordered: list[str] = []

    def visit(sname):
        if sname in ordered:
            return
        for m in members(sname):
            mt = type_alias.get(m["type"], m["type"])
            if mt in types and not m["pointer"] and mt != sname:
                visit(mt)
        ordered.append(sname)

    for s in wanted:
        visit(s)

    def is_serializable_member(m):
        return not m["pointer"] and m["name"] not in ("sType", "pNext") and m["array"] != "2d"

    lines: list[str] = []
    w = lines.append
    w("// GENERATED by codegen/gen_vk_tables.py from the pinned vk.xml -- do not edit.")
    w("namespace vkt {")
    # enums
    w("const EnumEntry kEnums[] = {")
    n_enum = 0
    for tname in sorted(enums):
        for name, v in enums[tname]:
            w(f'  {{"{tname}", "{name}", {v}LL}},')
            n_enum += 1
    w("};")
    w(f"const size_t kEnumCount = {n_enum};")
    w("const FlagType kFlagTypes[] = {")
    for fname in sorted(flag_types):
        bits, is64 = flag_types[fname]
        w(f'  {{"{fname}", "{bits}", {str(is64).lower()}}},')
    w("};")
    w(f"const size_t kFlagTypeCount = {len(flag_types)};")

    # formats
    fmt_values = dict(enums.get("VkFormat", []))
    formats = []
    for f in reg.find("formats"):
        comps = [{"name": c.get("name"), "bits": c.get("bits"), "numeric": c.get("numericFormat")}
                 for c in f.findall("component")]
        formats.append({
            "name": f.get("name"), "value": fmt_values.get(f.get("name")), "class": f.get("class"),
            "block_size": int(f.get("blockSize")), "texels_per_block": int(f.get("texelsPerBlock")),
            "block_extent": [int(x) for x in (f.get("blockExtent") or "1,1,1").split(",")],
            "packed": int(f.get("packed") or 0), "compressed": f.get("compressed") or "",
            "chroma": f.get("chroma") or "", "components": comps})
    w("const FormatInfo kFormats[] = {")
    for f in formats:
        if f["value"] is None:
            continue
        cs = ", ".join('{"%s", %s, "%s"}' % (c["name"], c["bits"] if c["bits"] != "compressed" else "0",
                                             c["numeric"]) for c in f["components"][:4])
        while cs.count("{") < 4:
            cs += (", " if cs else "") + '{"", 0, ""}'
        be = f["block_extent"]
        w(f'  {{"{f["name"]}", {f["value"]}, "{f["class"]}", {f["block_size"]}, {f["texels_per_block"]}, '
          f'{{{be[0]}, {be[1]}, {be[2]}}}, {f["packed"]}, "{f["compressed"]}", {min(4, len(f["components"]))}, '
          f'{{{cs}}}}},')
    w("};")
    w(f"const size_t kFormatCount = {sum(1 for f in formats if f['value'] is not None)};")

    # serializers
    enum_types = set(enums)
    for s in ordered:
        w(f"void ser_{s}(const {s}& s, json& j) {{")
        ms = members(s)
        names = {m["name"] for m in ms}
        for m in ms:
            if not is_serializable_member(m):
                continue
            mt = type_alias.get(m["type"], m["type"])
            n = m["name"]
            count_member = n[:-1] + "Count" if n.endswith("s") else None
            limit = f"std::min<size_t>(s.{count_member}, std::size(s.{n}))" if (
                m["array"] and count_member in names) else f"std::size(s.{n})"
            if m["array"]:
                if mt == "char":
                    w(f'  j["{n}"] = std::string(s.{n}, strnlen(s.{n}, std::size(s.{n})));')
                elif mt in SCALARS:
                    conv = "(bool)" if SCALARS[mt] == "b" else ""
                    w(f'  {{ json a = json::array(); for (size_t i = 0; i < {limit}; ++i) a.push_back({conv}s.{n}[i]); j["{n}"] = a; }}')
                elif mt in types:
                    w(f'  {{ json a = json::array(); for (size_t i = 0; i < {limit}; ++i) {{ json e; ser_{mt}(s.{n}[i], e); a.push_back(e); }} j["{n}"] = a; }}')
                elif mt in enum_types:
                    w(f'  {{ json a = json::array(); for (size_t i = 0; i < {limit}; ++i) a.push_back(enum_name("{mt}", (int64_t)s.{n}[i])); j["{n}"] = a; }}')
                continue
            if mt in SCALARS:
                if SCALARS[mt] == "b":
                    w(f'  j["{n}"] = (bool)s.{n};')
                else:
                    w(f'  j["{n}"] = s.{n};')
            elif mt in flag_types:
                bits, is64 = flag_types[mt]
                w(f'  j["{n}"] = flag_names("{bits}", (uint64_t)s.{n});')
            elif mt in enum_types:
                w(f'  j["{n}"] = enum_name("{mt}", (int64_t)s.{n});')
            elif mt in types:
                w(f'  ser_{mt}(s.{n}, j["{n}"]);')
            elif mt in ("VkFlags", "VkFlags64"):
                w(f'  j["{n}"] = (uint64_t)s.{n};')
        w("}")

    # bool field tables and the registry
    reg_entries = []
    for s in ordered:
        ms = members(s)
        stype = next((m["values"] for m in ms if m["name"] == "sType"), None)
        bools = [m["name"] for m in ms if m["type"] == "VkBool32" and not m["array"] and not m["pointer"]]
        if bools and category.get(s) == "feature":
            w(f"const BoolField kBF_{s}[] = {{")
            for b in bools:
                w(f'  {{"{b}", offsetof({s}, {b})}},')
            w("};")
        reqs = sorted(included_by.get(s, set()))
        reg_entries.append((s, stype, category.get(s, "nested"), bool(bools) and category.get(s) == "feature",
                            "|".join(reqs)))
    w("const StructInfo kStructs[] = {")
    for s, stype, cat, has_bools, reqs in reg_entries:
        st = stype if stype else "VkStructureType(0)"
        bf = f"kBF_{s}, std::size(kBF_{s})" if has_bools else "nullptr, 0"
        w(f'  {{"{s}", {st}, sizeof({s}), "{cat}", '
          f'[](const void* p, json& j) {{ ser_{s}(*static_cast<const {s}*>(p), j); }}, {bf}, "{reqs}"}},')
        for alias, target in type_alias.items():
            if target == s and alias not in types and alias not in excluded_types:
                w(f'  {{"{alias}", {st}, sizeof({s}), "{cat}", '
                  f'[](const void* p, json& j) {{ ser_{s}(*static_cast<const {s}*>(p), j); }}, {bf}, "{reqs}"}},')
    w("};")
    w("const size_t kStructCount = std::size(kStructs);")
    w("}  // namespace vkt")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    if formats_json:
        with open(formats_json, "w") as f:
            json.dump({"source": "vk.xml <formats>", "formats": formats}, f, indent=1, sort_keys=True)
    print(f"gen_vk_tables: {n_enum} enum values, {len(flag_types)} flag types, {len(formats)} formats, "
          f"{len(ordered)} structs ({sum(1 for s in ordered if category.get(s) == 'feature')} feature, "
          f"{sum(1 for s in ordered if category.get(s) == 'property')} property)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
