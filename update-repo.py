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
GPG_KEYID = "38A975C07DA0CB7C82484843B88AE4094A478224"  # tuffgit21 <94118843+tuffgit21@users.noreply.github.com>
GPG_BIN = os.environ.get("GPG_BIN") or (
    r"C:\Program Files\Git\usr\bin\gpg.exe" if os.path.exists(r"C:\Program Files\Git\usr\bin\gpg.exe") else "gpg"
)

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
    # Pure-python fallback: parse ar without external 'ar' tool
    import tarfile, io, gzip as gz, lzma
    fields = {}
    try:
        data = deb_path.read_bytes()
        if not data.startswith(b"!<arch>\n"):
            raise ValueError("not an ar archive")
        off = 8
        control_data = None
        control_name = None
        while off + 60 <= len(data):
            hdr = data[off:off+60]
            name = hdr[0:16].decode().strip()
            size = int(hdr[48:58].decode().strip())
            off += 60
            chunk = data[off:off+size]
            if name.startswith("control.tar"):
                control_data = chunk
                control_name = name
                break
            off += size
            if size % 2 == 1:
                off += 1
        if control_data is None:
            raise ValueError("control.tar* not found")
        # decompress if needed
        if control_name.endswith(".gz"):
            control_data = gz.decompress(control_data)
        elif control_name.endswith(".xz"):
            control_data = lzma.decompress(control_data)
        # .zst not handled (rare)
        with tarfile.open(fileobj=io.BytesIO(control_data)) as tf:
            for m in tf.getmembers():
                if m.name in ("control", "./control"):
                    f = tf.extractfile(m)
                    if f:
                        text = f.read().decode(errors="ignore")
                        cur = None
                        for line in text.splitlines():
                            m2 = re.match(r"(\w[\w-]*):\s*(.*)", line)
                            if m2:
                                fields[m2.group(1)] = m2.group(2).strip()
                                cur = m2.group(1)
                            elif line.startswith(" ") and cur:
                                # continuation of previous field (e.g. Description)
                                fields[cur] += "\n" + line
                        break
        if not fields:
            raise ValueError("control not found in tar")
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
        fields = parse_deb_control(deb)
        if not fields or "Package" not in fields:
            # fallback if parsing failed (e.g. no dpkg-deb on Windows)
            print(f"  warn: fallback failed for {deb.name}, using filename", file=sys.stderr)
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

    # Auto-sign Release if GPG key exists
    try:
        # check key exists
        chk = subprocess.run([GPG_BIN, "--list-keys", GPG_KEYID], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if chk.returncode == 0:
            r1 = subprocess.run([GPG_BIN, "--batch", "--yes", "--pinentry-mode", "loopback", "--default-key", GPG_KEYID, "-abs", "-o", str(REPO / "dists/stable/Release.gpg"), str(REPO / "dists/stable/Release")])
            r2 = subprocess.run([GPG_BIN, "--batch", "--yes", "--pinentry-mode", "loopback", "--default-key", GPG_KEYID, "--clearsign", "-o", str(REPO / "dists/stable/InRelease"), str(REPO / "dists/stable/Release")])
            if r1.returncode == 0 and r2.returncode == 0:
                print(f"Signed {REPO/'dists/stable/InRelease'} and {REPO/'dists/stable/Release.gpg'} with {GPG_KEYID}")
            else:
                print("Warning: GPG signing failed - you may need to enter passphrase. Run manually:", file=sys.stderr)
                print(f"  {GPG_BIN} --default-key {GPG_KEYID} -abs -o dists/stable/Release.gpg dists/stable/Release", file=sys.stderr)
                print(f"  {GPG_BIN} --default-key {GPG_KEYID} --clearsign -o dists/stable/InRelease dists/stable/Release", file=sys.stderr)
        else:
            print(f"GPG key {GPG_KEYID} not found - skipping signing. Unsigned repo will need [trusted=yes].", file=sys.stderr)
    except FileNotFoundError:
        print(f"GPG not found at {GPG_BIN} - skipping signing.", file=sys.stderr)
        print("Done. To sign manually: gpg --default-key <KEYID> -abs -o dists/stable/Release.gpg dists/stable/Release && gpg --clearsign -o dists/stable/InRelease dists/stable/Release")

    print("\nDone. Commit and push the updated dists/ + public.key/tuffgit21.gpg if changed.")

if __name__ == "__main__":
    main()
