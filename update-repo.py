#!/usr/bin/env python3
"""
Regenerate Packages, Packages.gz and Release for tuffgit21-APT-repo
Run this after adding/removing .deb files in pool/

Requires: python3 (no dpkg-dev needed). Parses .deb as ar archives.
"""
import pathlib, hashlib, gzip, datetime, subprocess, os, re, sys

REPO = pathlib.Path(__file__).parent
DISTS = REPO / "dists" / "stable" / "main" / "binary-amd64"
POOL = REPO / "pool"

def parse_deb_control(deb_path: pathlib.Path) -> dict:
    """Extract control fields from .deb without dpkg-deb if not available."""
    # Try dpkg-deb first (most accurate)
    try:
        out = subprocess.check_output(["dpkg-deb", "-f", str(deb_path),
            "Package", "Version", "Architecture", "Maintainer", "Depends", "Section", "Priority", "Description"],
            text=True, stderr=subprocess.DEVNULL)
        # dpkg-deb -f with multiple fields prints each on new line, empty if missing
        # fallback: use dpkg-deb -I
        fields = {}
        # Use dpkg-deb -I parsing instead for robustness
        info = subprocess.check_output(["dpkg-deb", "-I", str(deb_path)], text=True, stderr=subprocess.DEVNULL)
        for line in info.splitlines():
            m = re.match(r"\s*(\w[\w-]*):\s*(.*)", line)
            if m:
                fields[m.group(1)] = m.group(2).strip()
        return fields
    except Exception:
        pass
    # Pure-python fallback: ar + tar
    import tarfile, io
    fields = {}
    try:
        # .deb is ar archive
        data = deb_path.read_bytes()
        # find control.tar.* member
        # simple: look for control.tar
        for name in [b"control.tar.gz", b"control.tar.xz", b"control.tar.zst", b"control.tar"]:
            idx = data.find(name)
            if idx != -1:
                break
        # brute: try extracting with ar if available
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["ar", "x", str(deb_path)], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for ctrl in pathlib.Path(tmp).glob("control.tar*"):
                # decompress
                try:
                    import gzip as gz
                    if ctrl.suffix == ".gz":
                        with gz.open(ctrl, "rb") as f:
                            tar_bytes = f.read()
                        # write temp tar
                        tar_path = pathlib.Path(tmp) / "control.tar"
                        tar_path.write_bytes(tar_bytes)
                        ctrl = tar_path
                    # xz/zst not handled here - use tar auto
                    with tarfile.open(ctrl) as tf:
                        for m in tf.getmembers():
                            if m.name in ("control", "./control"):
                                f = tf.extractfile(m)
                                if f:
                                    for line in f.read().decode(errors="ignore").splitlines():
                                        m2 = re.match(r"(\w[\w-]*):\s*(.*)", line)
                                        if m2:
                                            fields[m2.group(1)] = m2.group(2).strip()
                                            if m2.group(1) == "Description":
                                                break
                except Exception as e:
                    print(f"warn {deb_path.name}: {e}", file=sys.stderr)
                break
    except Exception as e:
        print(f"warn fallback failed for {deb_path}: {e}", file=sys.stderr)
    return fields

def main():
    debs = sorted(POOL.rglob("*.deb"))
    if not debs:
        print("No .deb files found in pool/")
        sys.exit(1)

    print(f"Found {len(debs)} debs")
    entries = []
    for deb in debs:
        rel = deb.relative_to(REPO).as_posix()
        size = deb.stat().st_size
        sha256 = hashlib.sha256(deb.read_bytes()).hexdigest()
        md5 = hashlib.md5(deb.read_bytes()).hexdigest()
        # try to get control fields
        fields = {}
        try:
            info = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Package,Version,Architecture,Maintainer,Depends,Section,Priority,Description"], text=True, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        # Use dpkg-deb -I as source of truth
        try:
            raw = subprocess.check_output(["dpkg-deb", "-I", str(deb)], text=True, stderr=subprocess.DEVNULL)
            cur = None
            desc_lines = []
            for line in raw.splitlines():
                m = re.match(r"\s*(\w[\w-]*):\s*(.*)", line)
                if m:
                    k, v = m.group(1), m.group(2)
                    fields[k] = v
                    cur = k
                elif line.startswith(" ") and cur == "Description":
                    desc_lines.append(line.strip())
            if desc_lines:
                fields["Description"] = fields.get("Description","") + "\n " + "\n ".join(desc_lines) if "Description" in fields else "\n ".join(desc_lines)
        except Exception:
            fields = {"Package": deb.stem.split("_")[0], "Version": "1.0", "Architecture": "all", "Maintainer": "tuffgit21", "Description": deb.stem}

        entry = []
        for k in ["Package", "Version", "Section", "Priority", "Architecture", "Maintainer", "Depends", "Description"]:
            if k in fields and fields[k]:
                # Description may be multiline - ensure continuation lines start with space
                val = fields[k]
                entry.append(f"{k}: {val}")
        entry.append(f"Filename: {rel}")
        entry.append(f"Size: {size}")
        entry.append(f"MD5sum: {md5}")
        entry.append(f"SHA256: {sha256}")
        entries.append("\n".join(entry))
        print(f"  {rel} -> {fields.get('Package','?')} {fields.get('Version','?')}")

    DISTS.mkdir(parents=True, exist_ok=True)
    packages_text = "\n\n".join(entries) + "\n"
    (DISTS / "Packages").write_text(packages_text, encoding="utf-8", newline="\n")
    with gzip.open(DISTS / "Packages.gz", "wb") as gz:
        gz.write(packages_text.encode())
    print(f"Wrote {DISTS/'Packages'} ({len(packages_text)} bytes)")
    print(f"Wrote {DISTS/'Packages.gz'}")

    # Release
    date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC")
    release = f"""Origin: tuffgit21 APT Repo
Label: tuffgit21
Suite: stable
Codename: stable
Version: 1.0
Architectures: amd64 all
Components: main
Description: tuffgit21's APT repository
Date: {date_str}
"""
    files = []
    for rel in ["main/binary-amd64/Packages", "main/binary-amd64/Packages.gz"]:
        data = (DISTS.parent.parent / rel).read_bytes() if (DISTS.parent.parent / rel).exists() else (DISTS / pathlib.Path(rel).name).read_bytes()
        # correct path: dists/stable is parent.parent of DISTS
        full = REPO / "dists" / "stable" / rel
        data = full.read_bytes()
        files.append((rel, data))

    release += "MD5Sum:\n"
    for rel, d in files:
        release += f" {hashlib.md5(d).hexdigest()} {len(d)} {rel}\n"
    release += "SHA1:\n"
    for rel, d in files:
        release += f" {hashlib.sha1(d).hexdigest()} {len(d)} {rel}\n"
    release += "SHA256:\n"
    for rel, d in files:
        release += f" {hashlib.sha256(d).hexdigest()} {len(d)} {rel}\n"

    (REPO / "dists" / "stable" / "Release").write_text(release, encoding="utf-8", newline="\n")
    print(f"Wrote {REPO/'dists/stable/Release'}")
    print("\nDone. To sign: gpg --default-key <KEYID> -abs -o dists/stable/Release.gpg dists/stable/Release && gpg --clearsign -o dists/stable/InRelease dists/stable/Release")

if __name__ == "__main__":
    main()
