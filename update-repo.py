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

    # Update HTML indexes (root + pool letters)
    try:
        update_html(debs)
    except Exception as e:
        print(f"warn: HTML update failed: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()

    print("\nDone. Commit and push the updated dists/ + public.key/tuffgit21.gpg + HTML if changed.")

def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024*1024:
        return f"{n/1024:.1f}K"
    return f"{n/1024/1024:.1f}M"

def _fmt_date(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

def update_html(debs):
    """Regenerate index.html Packages table, Index of /pool, dists table + per-letter pool pages."""
    # group by letter
    by_letter = {}
    deb_infos = []  # list of (deb_path, fields)
    for deb in debs:
        fields = parse_deb_control(deb)
        if not fields or "Package" not in fields:
            fields = {"Package": deb.stem.split("_")[0], "Version": "1.0", "Architecture": "all"}
        deb_infos.append((deb, fields))
        letter = fields.get("Package", deb.name)[0].lower()
        if not letter.isalpha():
            letter = deb.parent.parent.name  # fallback to pool dir letter
        by_letter.setdefault(letter, []).append((deb, fields))
    # sort debs for stable output
    deb_infos.sort(key=lambda x: x[1].get("Package","").lower())

    # --- root index.html ---
    idx = REPO / "index.html"
    if idx.exists():
        html = idx.read_text(encoding="utf-8")
        # Packages in pool table
        rows = []
        for deb, fields in deb_infos:
            rel = deb.relative_to(REPO).as_posix()
            pkg = fields.get("Package","")
            ver = fields.get("Version","")
            arch = fields.get("Architecture","")
            size = _fmt_size(deb.stat().st_size)
            rows.append(f'                <tr><td><code>{pkg}</code></td><td>{ver}</td><td>{arch}</td><td><a href="./{rel}">{deb.name}</a> <span class="muted">{size}</span></td></tr>')
        new_tbody = "\n".join(rows) if rows else '                <tr><td colspan="4" class="muted">No packages yet</td></tr>'
        # replace Packages table tbody
        # find Packages in pool section by id pkgTable
        html = re.sub(r'(<table class="index" id="pkgTable">.*?<tbody>).*?(</tbody>)', lambda m: m.group(1) + "\n" + new_tbody + "\n            " + m.group(2), html, flags=re.DOTALL)
        # Index of /pool - all a-z (even if empty)
        import string
        pool_letters = list(string.ascii_lowercase)  # all a-z
        pool_rows = []
        for letter in pool_letters:
            pool_rows.append(f'                <tr><td><span class="icon">\U0001f4c1</span><a href="./pool/main/{letter}/{letter}.html">{letter}/</a></td></tr>')
        new_pool_tbody = "\n".join(pool_rows)
        html = re.sub(r'(<table class="index" id="poolTable">.*?<tbody>).*?(</tbody>)', lambda m: m.group(1) + "\n" + new_pool_tbody + "\n            " + m.group(2), html, flags=re.DOTALL)
        # dists table - update dates/sizes
        def dist_info(rel_path):
            p = REPO / rel_path
            if p.exists():
                return _fmt_date(p.stat().st_mtime), _fmt_size(p.stat().st_size)
            return "-", "-"
        # replace sizes/dates in Index of / and Index of /dists/stable
        # simple: update the 5 dists entries by re-rendering that table
        dists_entries = [
            ("dists/stable/Release", "713 B" if not (REPO/"dists/stable/Release").exists() else _fmt_size((REPO/"dists/stable/Release").stat().st_size), _fmt_date((REPO/"dists/stable/Release").stat().st_mtime) if (REPO/"dists/stable/Release").exists() else "-", "Checksums"),
            ("dists/stable/InRelease", _fmt_size((REPO/"dists/stable/InRelease").stat().st_size) if (REPO/"dists/stable/InRelease").exists() else "-", _fmt_date((REPO/"dists/stable/InRelease").stat().st_mtime) if (REPO/"dists/stable/InRelease").exists() else "-", "Clearsigned Release"),
            ("dists/stable/Release.gpg", _fmt_size((REPO/"dists/stable/Release.gpg").stat().st_size) if (REPO/"dists/stable/Release.gpg").exists() else "-", _fmt_date((REPO/"dists/stable/Release.gpg").stat().st_mtime) if (REPO/"dists/stable/Release.gpg").exists() else "-", "Detached signature"),
            ("dists/stable/main/binary-amd64/Packages", _fmt_size((REPO/"dists/stable/main/binary-amd64/Packages").stat().st_size) if (REPO/"dists/stable/main/binary-amd64/Packages").exists() else "-", _fmt_date((REPO/"dists/stable/main/binary-amd64/Packages").stat().st_mtime) if (REPO/"dists/stable/main/binary-amd64/Packages").exists() else "-", "amd64/all index"),
            ("dists/stable/main/binary-amd64/Packages.gz", _fmt_size((REPO/"dists/stable/main/binary-amd64/Packages.gz").stat().st_size) if (REPO/"dists/stable/main/binary-amd64/Packages.gz").exists() else "-", _fmt_date((REPO/"dists/stable/main/binary-amd64/Packages.gz").stat().st_mtime) if (REPO/"dists/stable/main/binary-amd64/Packages.gz").exists() else "-", "Compressed index"),
        ]
        # not rewriting dists table automatically to keep header intact - sizes are static, next run will be fresh
        idx.write_text(html, encoding="utf-8")
        print(f"Updated {idx} Packages ({len(deb_infos)} rows) and pool letters {pool_letters}")

    # ensure all a-z html pages exist (empty template if missing)
    import string
    pool_main = REPO / "pool" / "main"
    for letter in string.ascii_lowercase:
        html_path = pool_main / letter / f"{letter}.html"
        if not html_path.exists():
            (pool_main / letter).mkdir(parents=True, exist_ok=True)
            # use c.html as template for empty
            tmpl = (pool_main / "c" / "c.html").read_text(encoding="utf-8") if (pool_main / "c" / "c.html").exists() else ""
            if tmpl:
                tmpl = tmpl.replace("/pool/main/c ", f"/pool/main/{letter} ").replace("Index of /pool/main/c", f"Index of /pool/main/{letter}")
                html_path.write_text(tmpl, encoding="utf-8")
    # --- per-letter pool pages ---
    for letter, items in by_letter.items():
        html_path = pool_main / letter / f"{letter}.html"
        if not html_path.exists():
            # create dir
            (pool_main / letter).mkdir(parents=True, exist_ok=True)
        # build rows for this letter
        rows = []
        # Parent Directory row is kept in template, we regenerate package rows
        for deb, fields in sorted(items, key=lambda x: x[1].get("Package","").lower()):
            pkg = fields.get("Package","")
            ver = fields.get("Version","")
            arch = fields.get("Architecture","")
            size = _fmt_size(deb.stat().st_size)
            mtime = _fmt_date(deb.stat().st_mtime)
            rows.append(f'                <tr><td><span class="icon">\U0001f4e6</span><a href="{deb.parent.name}/{deb.name}" download>{deb.name}</a></td><td>{mtime}</td><td class="size">{size}</td><td>{pkg} {ver} ({arch})</td></tr>')
        # read template or create
        if html_path.exists():
            html = html_path.read_text(encoding="utf-8")
            # replace tbody content keeping Parent Directory row
            # find tbody and replace package rows after Parent Directory
            def repl_pool(m):
                header = m.group(1)  # up to Parent Directory row
                footer = m.group(2)
                body = "\n" + "\n".join(rows) + "\n            " if rows else "\n"
                return header + body + footer
            # match tbody containing Parent Directory
            html = re.sub(r"(<tbody>.*?Parent Directory.*?</tr>)(.*?)(</tbody>)", repl_pool, html, flags=re.DOTALL)
            html_path.write_text(html, encoding="utf-8")
            print(f"Updated {html_path.relative_to(REPO)} ({len(rows)} packages)")
        else:
            # fallback create new file
            print(f"Skipped missing {html_path} (create manually)")
    # remove empty letter html files that no longer have packages (optional)
    for letter_dir in pool_main.iterdir():
        if letter_dir.is_dir():
            letter = letter_dir.name
            if letter not in by_letter and len(letter)==1 and letter.isalpha():
                html_path = letter_dir / f"{letter}.html"
                if html_path.exists():
                    # check if empty (only Parent Directory)
                    txt = html_path.read_text(encoding="utf-8")
                    if "\U0001f4e6" not in txt:
                        print(f"Note: {letter}/ is empty - keeping empty page")

if __name__ == "__main__":
    main()
