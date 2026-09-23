#!/usr/bin/env python3
"""
Regenerate Packages, Packages.gz and Release for tuffgit21-APT-repo
Supports multiple suites: stable, archives-stable, archives-unstable, testing
Run this after adding/removing .deb files in pool/

Requires: python3 (no dpkg-dev needed). Parses .deb as ar archives.
"""
import pathlib, hashlib, gzip, datetime, subprocess, os, re, sys

REPO = pathlib.Path(__file__).parent
GPG_KEYID = "38A975C07DA0CB7C82484843B88AE4094A478224"  # tuffgit21 <94118843+tuffgit21@users.noreply.github.com>
GPG_BIN = os.environ.get("GPG_BIN") or (
    r"C:\Program Files\Git\usr\bin\gpg.exe" if os.path.exists(r"C:\Program Files\Git\usr\bin\gpg.exe") else "gpg"
)

# Supported Debian architectures - generates binary-<arch>/Packages for each
SUPPORTED_ARCHES = ["amd64", "arm64"]
RELEASE_ARCHES = "amd64 arm64 all"

# Suite definitions
# - pool: where .debs for this suite live
# - dist: legacy dists/<suite>/main/binary-amd64 (kept for backward compat, now auto-derived to main/)
# - description: used in Release file
# - badge: short label for HTML
# - warning: optional warning text
SUITES = {
    "stable": {
        "pool": REPO / "pool" / "main",
        "dist": REPO / "dists" / "stable" / "main" / "binary-amd64",
        "description": "Stable - recommended, actively maintained",
        "badge": "Recommended",
        "badge_class": "badge",
        "optional": False,
        "warning": None,
    },
    "archives-stable": {
        "pool": REPO / "pool" / "archives-stable" / "main",
        "dist": REPO / "dists" / "archives-stable" / "main" / "binary-amd64",
        "description": "Archives Stable - discontinued stable projects (Optional, opt-in)",
        "badge": "Optional",
        "badge_class": "badge",
        "optional": True,
        "warning": "Discontinued but stable projects. Optional — enable only if you need a legacy package.",
    },
    "archives-unstable": {
        "pool": REPO / "pool" / "archives-unstable" / "main",
        "dist": REPO / "dists" / "archives-unstable" / "main" / "binary-amd64",
        "description": "Archives Unstable - discontinued unstable/buggy projects (Not recommended)",
        "badge": "Not recommended",
        "badge_class": "badge",
        "optional": True,
        "warning": "Discontinued unstable/buggy packages. NOT RECOMMENDED — may contain bugs or security issues. Opt-in only.",
    },
    "testing": {
        "pool": REPO / "pool" / "testing" / "main",
        "dist": REPO / "dists" / "testing" / "main" / "binary-amd64",
        "description": "Testing - upcoming/pre-release packages (Not recommended)",
        "badge": "Not recommended",
        "badge_class": "badge",
        "optional": True,
        "warning": "Upcoming/pre-release builds for testing. NOT RECOMMENDED for production. Opt-in only, expect breakage.",
    },
}

# Legacy alias for single-suite scripts (stable)
POOL = REPO / "pool"
DISTS = REPO / "dists" / "stable" / "main" / "binary-amd64"

def parse_deb_control(deb_path: pathlib.Path) -> dict:
    """Extract control fields from .deb without dpkg-deb if not available."""
    try:
        out = subprocess.check_output(["dpkg-deb", "-f", str(deb_path),
            "Package", "Version", "Architecture", "Maintainer", "Depends", "Section", "Priority", "Description"],
            text=True, stderr=subprocess.DEVNULL)
        fields = {}
        info = subprocess.check_output(["dpkg-deb", "-I", str(deb_path)], text=True, stderr=subprocess.DEVNULL)
        for line in info.splitlines():
            m = re.match(r"\s*(\w[\w-]*):\s*(.*)", line)
            if m:
                fields[m.group(1)] = m.group(2).strip()
        return fields
    except Exception:
        pass
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
        if control_name.endswith(".gz"):
            control_data = gz.decompress(control_data)
        elif control_name.endswith(".xz"):
            control_data = lzma.decompress(control_data)
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
                                fields[cur] += "\n" + line
                        break
        if not fields:
            raise ValueError("control not found in tar")
    except Exception as e:
        print(f"warn fallback failed for {deb_path}: {e}", file=sys.stderr)
    return fields

def generate_suite(suite: str, cfg: dict):
    """Generate Packages, Packages.gz and Release for a single suite (multi-arch: amd64 + arm64)."""
    pool = cfg["pool"]
    # Derive dists/main from legacy cfg["dist"] if present, else canonical
    if "dist" in cfg and str(cfg["dist"]).endswith(("binary-amd64", "binary-arm64")):
        dists_main = cfg["dist"].parent
    else:
        dists_main = REPO / "dists" / suite / "main"
    # ensure dirs exist for each supported arch
    for arch in SUPPORTED_ARCHES:
        (dists_main / f"binary-{arch}").mkdir(parents=True, exist_ok=True)
    # also keep legacy dist path for backward compat
    if "dist" in cfg:
        cfg["dist"].mkdir(parents=True, exist_ok=True)
    pool.mkdir(parents=True, exist_ok=True)
    # also ensure a-z letter dirs exist for browsing (empty placeholder)
    import string
    for letter in string.ascii_lowercase:
        (pool / letter).mkdir(parents=True, exist_ok=True)

    debs = sorted(pool.rglob("*.deb")) if pool.exists() else []
    print(f"[{suite}] Found {len(debs)} debs in {pool.relative_to(REPO) if pool.exists() else pool}")

    entries_per_arch = {arch: [] for arch in SUPPORTED_ARCHES}
    deb_infos = []  # for HTML
    for deb in debs:
        rel = deb.relative_to(REPO).as_posix()
        size = deb.stat().st_size
        sha256 = hashlib.sha256(deb.read_bytes()).hexdigest()
        md5 = hashlib.md5(deb.read_bytes()).hexdigest()
        fields = parse_deb_control(deb)
        if not fields or "Package" not in fields:
            print(f"  warn: fallback failed for {deb.name}, using filename", file=sys.stderr)
            fields = {"Package": deb.stem.split("_")[0], "Version": "1.0", "Architecture": "all", "Maintainer": "tuffgit21", "Description": deb.stem}
        deb_infos.append((deb, fields))
        entry = []
        for k in ["Package", "Version", "Section", "Priority", "Architecture", "Maintainer", "Depends", "Description"]:
            if k in fields and fields[k]:
                val = fields[k]
                entry.append(f"{k}: {val}")
        entry.append(f"Filename: {rel}")
        entry.append(f"Size: {size}")
        entry.append(f"MD5sum: {md5}")
        entry.append(f"SHA256: {sha256}")
        entry_text = "\n".join(entry)
        print(f"  {rel} -> {fields.get('Package','?')} {fields.get('Version','?')} [{fields.get('Architecture','all')}]")
        # Route entry to appropriate arch Packages
        arch_field = fields.get("Architecture", "all")
        if arch_field == "all":
            for arch in SUPPORTED_ARCHES:
                entries_per_arch[arch].append(entry_text)
        elif arch_field in SUPPORTED_ARCHES:
            entries_per_arch[arch_field].append(entry_text)
        else:
            # unknown arch (e.g. i386) — include in all arches for visibility
            for arch in SUPPORTED_ARCHES:
                entries_per_arch[arch].append(entry_text)

    # Write Packages per arch
    for arch in SUPPORTED_ARCHES:
        dist = dists_main / f"binary-{arch}"
        packages_text = "\n\n".join(entries_per_arch[arch]) + ("\n" if entries_per_arch[arch] else "")
        (dist / "Packages").write_text(packages_text, encoding="utf-8", newline="\n")
        with gzip.open(dist / "Packages.gz", "wb") as gz:
            gz.write(packages_text.encode())
        print(f"  Wrote {dist/'Packages'} ({len(packages_text)} bytes, {len(entries_per_arch[arch])} entries)")
        print(f"  Wrote {dist/'Packages.gz'}")

    # Release
    date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC")
    # Suite-specific description
    release = f"""Origin: tuffgit21 APT Repo
Label: tuffgit21
Suite: {suite}
Codename: {suite}
Version: 1.0
Architectures: {RELEASE_ARCHES}
Components: main
Description: {cfg['description']}
Date: {date_str}
"""
    # Files relative to dists/<suite>
    dists_suite = REPO / "dists" / suite
    files = []
    for arch in SUPPORTED_ARCHES:
        for fname in ["Packages", "Packages.gz"]:
            rel = f"main/binary-{arch}/{fname}"
            full = dists_suite / rel
            # ensure file exists (we just wrote it)
            data = full.read_bytes() if full.exists() else b""
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

    (dists_suite / "Release").write_text(release, encoding="utf-8", newline="\n")
    print(f"  Wrote {dists_suite/'Release'}")

    # Auto-sign Release if GPG key exists
    try:
        chk = subprocess.run([GPG_BIN, "--list-keys", GPG_KEYID], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if chk.returncode == 0:
            r1 = subprocess.run([GPG_BIN, "--batch", "--yes", "--pinentry-mode", "loopback", "--default-key", GPG_KEYID, "-abs", "-o", str(dists_suite / "Release.gpg"), str(dists_suite / "Release")])
            r2 = subprocess.run([GPG_BIN, "--batch", "--yes", "--pinentry-mode", "loopback", "--default-key", GPG_KEYID, "--clearsign", "-o", str(dists_suite / "InRelease"), str(dists_suite / "Release")])
            if r1.returncode == 0 and r2.returncode == 0:
                print(f"  Signed {dists_suite/'InRelease'} and {dists_suite/'Release.gpg'} with {GPG_KEYID}")
            else:
                print(f"  Warning: GPG signing failed for {suite}", file=sys.stderr)
                print(f"    {GPG_BIN} --default-key {GPG_KEYID} -abs -o dists/{suite}/Release.gpg dists/{suite}/Release", file=sys.stderr)
                print(f"    {GPG_BIN} --default-key {GPG_KEYID} --clearsign -o dists/{suite}/InRelease dists/{suite}/Release", file=sys.stderr)
        else:
            print(f"  GPG key {GPG_KEYID} not found - skipping signing for {suite}. Unsigned repo will need [trusted=yes].", file=sys.stderr)
    except FileNotFoundError:
        print(f"  GPG not found at {GPG_BIN} - skipping signing for {suite}.", file=sys.stderr)

    return deb_infos


def main():
    # Generate all suites
    all_infos = {}
    has_any = False
    for suite, cfg in SUITES.items():
        infos = generate_suite(suite, cfg)
        all_infos[suite] = infos
        if infos:
            has_any = True

    if not has_any:
        # still ok - we generated empty Packages for each suite, but warn if really no debs at all
        total = sum(len(v) for v in all_infos.values())
        if total == 0:
            print("No .deb files found in any pool/ - generated empty Packages for all suites", file=sys.stderr)

    # Update HTML indexes (root + pool letters + dists)
    try:
        update_html(all_infos)
    except Exception as e:
        print(f"warn: HTML update failed: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()

    print("\nDone. Commit and push the updated dists/ + pool/ + HTML if changed.")
    print("Suites generated:", ", ".join(SUITES.keys()))

def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024*1024:
        return f"{n/1024:.1f}K"
    return f"{n/1024/1024:.1f}M"

def _fmt_date(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

def update_html(all_infos):
    """Regenerate index.html Packages tables, pool letters, dists tables + per-letter pool pages for all suites."""
    import string
    # --- root index.html ---
    idx = REPO / "index.html"
    if idx.exists():
        html = idx.read_text(encoding="utf-8")

        # Update Packages tables per suite
        # For backwards compat: #pkgTable is stable, #pkgTable-archives-stable etc for others
        # We update each suite's table if exists in HTML

        for suite, infos in all_infos.items():
            # sort
            infos_sorted = sorted(infos, key=lambda x: x[1].get("Package","").lower())
            rows = []
            for deb, fields in infos_sorted:
                rel = deb.relative_to(REPO).as_posix()
                pkg = fields.get("Package","")
                ver = fields.get("Version","")
                arch = fields.get("Architecture","")
                size = _fmt_size(deb.stat().st_size)
                rows.append(f'                <tr><td><code>{pkg}</code></td><td>{ver}</td><td>{arch}</td><td><a href="./{rel}">{deb.name}</a> <span class="muted">{size}</span></td></tr>')
            new_tbody = "\n".join(rows) if rows else '                <tr><td colspan="4" class="muted">No packages yet — add .deb to pool/' + (suite + '/' if suite != 'stable' else '') + 'main/</td></tr>'
            # determine table id
            table_id = "pkgTable" if suite == "stable" else f"pkgTable-{suite}"
            # replace tbody for that table
            pattern = rf'(<table class="index" id="{re.escape(table_id)}">.*?<tbody>).*?(</tbody>)'
            if re.search(pattern, html, flags=re.DOTALL):
                html = re.sub(pattern, lambda m: m.group(1) + "\n" + new_tbody + "\n            " + m.group(2), html, flags=re.DOTALL)
                # also update count span if exists
                count_id = "pkgCount" if suite == "stable" else f"pkgCount-{suite}"
                # count will be updated by JS, but we set initial text
                html = re.sub(rf'(<span class="count" id="{re.escape(count_id)}">).*?(</span>)', lambda m: m.group(1) + f"{len(infos_sorted)} packages" + m.group(2), html, flags=re.DOTALL)
            else:
                # if suite table not found (only stable exists in old html), inject after stable table
                # We'll handle after loop by checking missing suites
                pass

        # Index of /pool - keep stable pool letters (a-z)
        pool_letters = list(string.ascii_lowercase)
        pool_rows = []
        for letter in pool_letters:
            pool_rows.append(f'                <tr><td><span class="icon">📁</span><a href="./pool/main/{letter}/{letter}.html">{letter}/</a></td></tr>')
        new_pool_tbody = "\n".join(pool_rows)
        html = re.sub(r'(<table class="index" id="poolTable">.*?<tbody>).*?(</tbody>)', lambda m: m.group(1) + "\n" + new_pool_tbody + "\n            " + m.group(2), html, flags=re.DOTALL)

        idx.write_text(html, encoding="utf-8")
        print(f"Updated {idx} Packages for suites: {', '.join(f'{k}={len(v)}' for k,v in all_infos.items())}")

    # --- dists/index.html: list all suites ---
    dists_idx = REPO / "dists" / "index.html"
    if dists_idx.exists():
        html = dists_idx.read_text(encoding="utf-8")
        # build rows for each suite
        rows = []
        for suite, cfg in SUITES.items():
            mtime = _fmt_date((REPO / "dists" / suite).stat().st_mtime) if (REPO / "dists" / suite).exists() else "-"
            desc = cfg["description"]
            badge = cfg["badge"]
            rows.append(f'                <tr><td><span class="icon">📁</span><a href="{suite}/">{suite}/</a></td><td>{mtime}</td><td class="size">-</td><td>{desc} <span class="badge">{badge}</span></td></tr>')
        new_tbody = "\n".join(rows)
        # replace tbody of pkgTable in dists/index.html
        html = re.sub(r'(<table class="index" id="pkgTable">.*?<tbody>).*?(</tbody>)', lambda m: m.group(1) + "\n" + '                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>\n' + new_tbody + "\n            " + m.group(2), html, flags=re.DOTALL)
        dists_idx.write_text(html, encoding="utf-8")
        print(f"Updated {dists_idx} with {len(SUITES)} suites")

    # ensure all a-z html pages exist for each suite's pool
    pool_main_variants = [
        (REPO / "pool" / "main", "../../../styles.css", "../../../index.html", "pool/main"),
        (REPO / "pool" / "archives-stable" / "main", "../../../../styles.css", "../../../../index.html", "pool/archives-stable/main"),
        (REPO / "pool" / "archives-unstable" / "main", "../../../../styles.css", "../../../../index.html", "pool/archives-unstable/main"),
        (REPO / "pool" / "testing" / "main", "../../../../styles.css", "../../../../index.html", "pool/testing/main"),
    ]
    # need template for empty letter pages
    tmpl_src = (REPO / "pool" / "main" / "c" / "c.html")
    tmpl_text = tmpl_src.read_text(encoding="utf-8") if tmpl_src.exists() else ""

    for pool_main, css_rel, back_rel, url_prefix in pool_main_variants:
        is_stable = (pool_main == REPO / "pool" / "main")
        # depth differs for css/back
        for letter in string.ascii_lowercase:
            html_path = pool_main / letter / f"{letter}.html"
            if not html_path.exists():
                (pool_main / letter).mkdir(parents=True, exist_ok=True)
                if tmpl_text:
                    tmpl = tmpl_text.replace("/pool/main/c ", f"/{url_prefix}/{letter} ").replace("Index of /pool/main/c", f"Index of /{url_prefix}/{letter}")
                    # adjust css path
                    tmpl = tmpl.replace('href="../../../styles.css"', f'href="{css_rel}"')
                    tmpl = tmpl.replace('href="../../../index.html"', f'href="{back_rel}"')
                    tmpl = tmpl.replace('href="../../../../index.html"', f'href="{back_rel}"')
                    html_path.write_text(tmpl, encoding="utf-8")
        # per-letter pool pages: show application folders
        for letter in string.ascii_lowercase:
            letter_dir = pool_main / letter
            letter_dir.mkdir(parents=True, exist_ok=True)
            html_path = letter_dir / f"{letter}.html"
            apps = sorted([p for p in letter_dir.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
            rows = []
            for app in apps:
                mtime = _fmt_date(app.stat().st_mtime)
                debs = list(app.glob("*.deb"))
                desc = f"{len(debs)} package" + ("s" if len(debs)!=1 else "") if debs else ""
                rows.append(f'                <tr><td><span class="icon">📁</span><a href="{app.name}/">{app.name}/</a></td><td>{mtime}</td><td class="size">-</td><td>{desc}</td></tr>')
            rows_str = "\n".join(rows)
            has_pkg = len(rows) > 0
            search_box = '<div class="search-box"><input type="search" id="pkgSearch" placeholder="Filter..." aria-label="Filter"><span class="count" id="pkgCount"></span><button class="badge" id="pkgClear" type="button" style="cursor:pointer;">Clear</button></div>' if has_pkg else ""
            empty_marker = "" if has_pkg else '\n        <p class="muted"><em>empty</em></p>'
            # suite name for title
            suite_title = url_prefix
            fav_p = css_rel.replace('styles.css','')
            html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Index of /{suite_title}/{letter} - tuffgit21 APT</title>
    <link rel="icon" type="image/svg+xml" href="{fav_p}favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="{fav_p}favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="{fav_p}favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="180x180" href="{fav_p}apple-touch-icon.png">
    <link rel="shortcut icon" href="{fav_p}favicon.ico">
    <link rel="manifest" href="{fav_p}manifest.json">
    <meta name="theme-color" content="#A80030">
    <link rel="stylesheet" href="{css_rel}">
</head>
<body>
    <header class="site-header">
        <h1>Index of /{suite_title}/{letter} <span>— tuffgit21 APT</span></h1>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">🌙 Dark</button>
        <p><a href="{back_rel}" style="color:white; text-decoration: underline;">&larr; Back to repository index</a></p>
    </header>
    <div class="container">
        {search_box}
        <div class="table-wrap"><table class="index" id="pkgTable">
            <thead><tr><th>Name</th><th>Last modified</th><th class="size">Size</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>
{rows_str}
            </tbody>
        </table></div>{empty_marker}
        <p id="pkgNoResults" class="muted" style="display:none; text-align:center; padding:0.75rem; border:1px dashed var(--border); border-radius:6px; margin-top:0.5rem;">No packages found for "<span id="pkgQuery"></span>" — try another name, version, arch or file.</p>
    </div>
    <script>
    (function(){{
        const k='tuffgit21-theme';
        const b=document.getElementById('themeToggle');
        function apply(t){{
            if(t==='dark') document.documentElement.setAttribute('data-theme','dark');
            else if(t==='light') document.documentElement.setAttribute('data-theme','light');
            else document.documentElement.removeAttribute('data-theme');
            if(b) b.textContent = (t==='dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? '☀️ Light' : '🌙 Dark';
        }}
        let cur=localStorage.getItem(k);
        apply(cur);
        if(b) b.onclick=()=>{{
            const isDark=document.documentElement.getAttribute('data-theme')==='dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const nxt=isDark?'light':'dark';
            localStorage.setItem(k,nxt); apply(nxt);
        }};
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{{ if(!localStorage.getItem(k)) apply(null); }});
    }})();
    </script>
    <script>
    (function(){{
        const q=document.getElementById('pkgSearch');
        const c=document.getElementById('pkgCount');
        const clear=document.getElementById('pkgClear');
        const tbl=document.getElementById('pkgTable');
        if(!q||!tbl) return;
        const rows=[...tbl.tBodies[0].rows].filter(r=> !r.textContent.includes('Parent Directory'));
        function filter(){{
            const s=q.value.trim().toLowerCase();
            let vis=0;
            rows.forEach(r=>{{
                const ok=!s || r.textContent.toLowerCase().includes(s);
                r.style.display=ok?'':'none';
                if(ok) vis++;
            }});
            if(c) c.textContent=vis+' / '+rows.length+' packages'+(s?' for "'+q.value+'"':'');
            const no=document.getElementById('pkgNoResults');
            const qSpan=document.getElementById('pkgQuery');
            const empty=s && vis===0;
            tbl.style.display=empty?'none':'';
            if(no){{ no.style.display=empty?'block':'none'; if(qSpan) qSpan.textContent=q.value; }}
        }}
        q.addEventListener('input',filter);
        if(clear) clear.addEventListener('click',()=>{{q.value='';filter();q.focus();}});
        filter();
    }})();
    </script>
</body>
</html>
'''
            html_path.write_text(html, encoding="utf-8")
            (letter_dir / "index.html").write_text(html, encoding="utf-8")
        print(f"  Updated pool {url_prefix} letters ({len(string.ascii_lowercase)} pages)")

        # generate per-application indexes (inside each app folder) for this pool variant
        for letter in string.ascii_lowercase:
            letter_dir = pool_main / letter
            for app in [p for p in letter_dir.iterdir() if p.is_dir()]:
                debs = sorted(app.glob("*.deb"))
                if not debs:
                    continue
                a_rows = []
                for deb in debs:
                    try:
                        flds = parse_deb_control(deb)
                    except Exception:
                        flds = {}
                    pkg = flds.get("Package", deb.stem.split("_")[0]) if flds else deb.stem.split("_")[0]
                    ver = flds.get("Version", "") if flds else ""
                    arch = flds.get("Architecture", "") if flds else ""
                    desc = f"{pkg} {ver} ({arch})".strip() if pkg else deb.name
                    size = _fmt_size(deb.stat().st_size)
                    mtime = _fmt_date(deb.stat().st_mtime)
                    a_rows.append(f'                <tr><td><span class="icon">📦</span><a href="{deb.name}" download>{deb.name}</a></td><td>{mtime}</td><td class="size">{size}</td><td>{desc}</td></tr>')
                a_rows_str = "\n".join(a_rows)
                # correct css depth for app folder
                css_app = css_rel.replace("../", "../../") if not is_stable else "../../../../styles.css"
                # for archives/testing pools, need one more ../
                if not is_stable:
                    css_app = "../../../../../styles.css"
                    back_app = "../../../../../index.html"
                else:
                    css_app = "../../../../styles.css"
                    back_app = "../../../../index.html"
                suite_title = url_prefix
                fav_app = css_app.replace('styles.css','')
                a_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Index of /{suite_title}/{letter}/{app.name} - tuffgit21 APT</title>
    <link rel="icon" type="image/svg+xml" href="{fav_app}favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="{fav_app}favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="{fav_app}favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="180x180" href="{fav_app}apple-touch-icon.png">
    <link rel="shortcut icon" href="{fav_app}favicon.ico">
    <link rel="manifest" href="{fav_app}manifest.json">
    <meta name="theme-color" content="#A80030">
    <link rel="stylesheet" href="{css_app}">
</head>
<body>
    <header class="site-header">
        <h1>Index of /{suite_title}/{letter}/{app.name} <span>— tuffgit21 APT</span></h1>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">🌙 Dark</button>
        <p><a href="{back_app}" style="color:white; text-decoration: underline;">&larr; Back to repository index</a></p>
    </header>
    <div class="container">
        <div class="search-box"><input type="search" id="pkgSearch" placeholder="Filter..." aria-label="Filter"><span class="count" id="pkgCount"></span><button class="badge" id="pkgClear" type="button" style="cursor:pointer;">Clear</button></div>
        <div class="table-wrap"><table class="index" id="pkgTable">
            <thead><tr><th>Name</th><th>Last modified</th><th class="size">Size</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>
{a_rows_str}
            </tbody>
        </table></div>
        <p id="pkgNoResults" class="muted" style="display:none; text-align:center; padding:0.75rem; border:1px dashed var(--border); border-radius:6px; margin-top:0.5rem;">No packages found for "<span id="pkgQuery"></span>" — try another name, version, arch or file.</p>
    </div>
    <script>
    (function(){{
        const k='tuffgit21-theme';
        const b=document.getElementById('themeToggle');
        function apply(t){{
            if(t==='dark') document.documentElement.setAttribute('data-theme','dark');
            else if(t==='light') document.documentElement.setAttribute('data-theme','light');
            else document.documentElement.removeAttribute('data-theme');
            if(b) b.textContent = (t==='dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? '☀️ Light' : '🌙 Dark';
        }}
        let cur=localStorage.getItem(k);
        apply(cur);
        if(b) b.onclick=()=>{{
            const isDark=document.documentElement.getAttribute('data-theme')==='dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const nxt=isDark?'light':'dark';
            localStorage.setItem(k,nxt); apply(nxt);
        }};
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{{ if(!localStorage.getItem(k)) apply(null); }});
    }})();
    </script>
    <script>
    (function(){{
        const q=document.getElementById('pkgSearch');
        const c=document.getElementById('pkgCount');
        const clear=document.getElementById('pkgClear');
        const tbl=document.getElementById('pkgTable');
        if(!q||!tbl) return;
        const rows=[...tbl.tBodies[0].rows].filter(r=>r.textContent.includes('📦'));
        function filter(){{
            const s=q.value.trim().toLowerCase();
            let vis=0;
            rows.forEach(r=>{{
                const ok=!s || r.textContent.toLowerCase().includes(s);
                r.style.display=ok?'':'none';
                if(ok) vis++;
            }});
            if(c) c.textContent=vis+' / '+rows.length+' packages'+(s?' for "'+q.value+'"':'');
            const no=document.getElementById('pkgNoResults');
            const qSpan=document.getElementById('pkgQuery');
            const empty=s && vis===0;
            tbl.style.display=empty?'none':'';
            if(no){{ no.style.display=empty?'block':'none'; if(qSpan) qSpan.textContent=q.value; }}
        }}
        q.addEventListener('input',filter);
        if(clear) clear.addEventListener('click',()=>{{q.value='';filter();q.focus();}});
        filter();
    }})();
    </script>
</body>
</html>
'''
                (app / "index.html").write_text(a_html, encoding="utf-8")

    # --- pool/index.html: list all suite pools ---
    pool_idx = REPO / "pool" / "index.html"
    # Build pool root listing: main + each archives/testing suite root dir (as seen under pool/)
    # stable is pool/main, others are pool/<suite>/
    pool_root_rows = []
    # stable entry (pool/main)
    mtime_main = _fmt_date((REPO / "pool" / "main").stat().st_mtime) if (REPO / "pool" / "main").exists() else "-"
    pool_root_rows.append(f'                <tr><td><span class="icon">📁</span><a href="main/">main/</a></td><td>{mtime_main}</td><td class="size">-</td><td>Stable — {SUITES["stable"]["description"]} <span class="badge badge-ok">Recommended</span></td></tr>')
    for suite in ["archives-stable", "archives-unstable", "testing"]:
        suite_pool = REPO / "pool" / suite
        mtime_s = _fmt_date(suite_pool.stat().st_mtime) if suite_pool.exists() else "-"
        desc = SUITES[suite]["description"]
        badge = SUITES[suite]["badge"]
        badge_cls = "badge-optional" if suite == "archives-stable" else "badge-warn"
        pool_root_rows.append(f'                <tr><td><span class="icon">📁</span><a href="{suite}/">{suite}/</a></td><td>{mtime_s}</td><td class="size">-</td><td>{desc} <span class="badge {badge_cls}">{badge}</span></td></tr>')
    pool_root_tbody = "\n".join(pool_root_rows)
    pool_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Index of /pool - tuffgit21 APT</title>
    <link rel="icon" type="image/svg+xml" href="../favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="../favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="../favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="180x180" href="../apple-touch-icon.png">
    <link rel="shortcut icon" href="../favicon.ico">
    <link rel="manifest" href="../manifest.json">
    <meta name="theme-color" content="#A80030">
    <link rel="stylesheet" href="../styles.css">
</head>
<body>
    <header class="site-header">
        <h1>Index of /pool <span>— tuffgit21 APT</span></h1>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">🌙 Dark</button>
        <p><a href="../index.html" style="color:white; text-decoration: underline;">&larr; Back to repository index</a></p>
    </header>
    <div class="container">
        <div class="search-box"><input type="search" id="pkgSearch" placeholder="Filter..." aria-label="Filter"><span class="count" id="pkgCount"></span><button class="badge" id="pkgClear" type="button" style="cursor:pointer;">Clear</button></div>
        <div class="table-wrap"><table class="index" id="pkgTable">
            <thead><tr><th>Name</th><th>Last modified</th><th class="size">Size</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>
{pool_root_tbody}
            </tbody>
        </table></div>
        <p id="pkgNoResults" class="muted" style="display:none; text-align:center; padding:0.75rem; border:1px dashed var(--border); border-radius:6px; margin-top:0.5rem;">No packages found for "<span id="pkgQuery"></span>" — try another name, version, arch or file.</p>
    </div>
    <script>
    (function(){{
        const k='tuffgit21-theme';
        const b=document.getElementById('themeToggle');
        function apply(t){{
            if(t==='dark') document.documentElement.setAttribute('data-theme','dark');
            else if(t==='light') document.documentElement.setAttribute('data-theme','light');
            else document.documentElement.removeAttribute('data-theme');
            if(b) b.textContent = (t==='dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? '☀️ Light' : '🌙 Dark';
        }}
        let cur=localStorage.getItem(k);
        apply(cur);
        if(b) b.onclick=()=>{{
            const isDark=document.documentElement.getAttribute('data-theme')==='dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const nxt=isDark?'light':'dark';
            localStorage.setItem(k,nxt); apply(nxt);
        }};
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{{ if(!localStorage.getItem(k)) apply(null); }});
    }})();
    </script>
    <script>
    (function(){{
        const q=document.getElementById('pkgSearch');
        const c=document.getElementById('pkgCount');
        const clear=document.getElementById('pkgClear');
        const tbl=document.getElementById('pkgTable');
        if(!q||!tbl) return;
        const rows=[...tbl.tBodies[0].rows].filter(r=> !r.textContent.includes('Parent Directory'));
        function filter(){{
            const s=q.value.trim().toLowerCase();
            let vis=0;
            rows.forEach(r=>{{
                const ok=!s || r.textContent.toLowerCase().includes(s);
                r.style.display=ok?'':'none';
                if(ok) vis++;
            }});
            if(c) c.textContent=vis+' / '+rows.length+' files'+(s?' for "'+q.value+'"':'');
            const no=document.getElementById('pkgNoResults');
            const qSpan=document.getElementById('pkgQuery');
            const empty=s && vis===0;
            tbl.style.display=empty?'none':'';
            if(no){{ no.style.display=empty?'block':'none'; if(qSpan) qSpan.textContent=q.value; }}
        }}
        q.addEventListener('input',filter);
        if(clear) clear.addEventListener('click',()=>{{q.value='';filter();q.focus();}});
        filter();
    }})();
    </script>
</body>
</html>
'''
    pool_idx.write_text(pool_html, encoding="utf-8")
    print(f"Updated {pool_idx.relative_to(REPO)} with {len(pool_root_rows)} entries")

    # --- pool/<suite>/index.html and pool/<suite>/main/index.html ---
    # For stable, pool/main is special (no suite folder). For other suites generate both levels.
    for suite in ["archives-stable", "archives-unstable", "testing"]:
        suite_dir = REPO / "pool" / suite
        suite_dir.mkdir(parents=True, exist_ok=True)
        main_dir = suite_dir / "main"
        main_dir.mkdir(parents=True, exist_ok=True)
        # pool/<suite>/index.html -> lists main/
        suite_mtime = _fmt_date(main_dir.stat().st_mtime) if main_dir.exists() else "-"
        suite_index = suite_dir / "index.html"
        suite_index_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Index of /pool/{suite} - tuffgit21 APT</title>
    <link rel="icon" type="image/svg+xml" href="../../favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="../../favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="../../favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="180x180" href="../../apple-touch-icon.png">
    <link rel="shortcut icon" href="../../favicon.ico">
    <link rel="manifest" href="../../manifest.json">
    <meta name="theme-color" content="#A80030">
    <link rel="stylesheet" href="../../styles.css">
</head>
<body>
    <header class="site-header">
        <h1>Index of /pool/{suite} <span>— tuffgit21 APT</span></h1>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">🌙 Dark</button>
        <p><a href="../../index.html" style="color:white; text-decoration: underline;">&larr; Back to repository index</a> &nbsp; <a href="../index.html" style="color:white; text-decoration: underline;">↑ pool</a></p>
    </header>
    <div class="container">
        <div class="search-box"><input type="search" id="pkgSearch" placeholder="Filter..." aria-label="Filter"><span class="count" id="pkgCount"></span><button class="badge" id="pkgClear" type="button" style="cursor:pointer;">Clear</button></div>
        <div class="table-wrap"><table class="index" id="pkgTable">
            <thead><tr><th>Name</th><th>Last modified</th><th class="size">Size</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>
                <tr><td><span class="icon">📁</span><a href="main/">main/</a></td><td>{suite_mtime}</td><td class="size">-</td><td>Component — {SUITES[suite]["description"]}</td></tr>
            </tbody>
        </table></div>
        <p id="pkgNoResults" class="muted" style="display:none; text-align:center; padding:0.75rem; border:1px dashed var(--border); border-radius:6px; margin-top:0.5rem;">No packages found for "<span id="pkgQuery"></span>" — try another name, version, arch or file.</p>
    </div>
    <script>
    (function(){{
        const k='tuffgit21-theme';
        const b=document.getElementById('themeToggle');
        function apply(t){{
            if(t==='dark') document.documentElement.setAttribute('data-theme','dark');
            else if(t==='light') document.documentElement.setAttribute('data-theme','light');
            else document.documentElement.removeAttribute('data-theme');
            if(b) b.textContent = (t==='dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? '☀️ Light' : '🌙 Dark';
        }}
        let cur=localStorage.getItem(k);
        apply(cur);
        if(b) b.onclick=()=>{{
            const isDark=document.documentElement.getAttribute('data-theme')==='dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const nxt=isDark?'light':'dark';
            localStorage.setItem(k,nxt); apply(nxt);
        }};
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{{ if(!localStorage.getItem(k)) apply(null); }});
    }})();
    </script>
    <script>
    (function(){{
        const q=document.getElementById('pkgSearch');
        const c=document.getElementById('pkgCount');
        const clear=document.getElementById('pkgClear');
        const tbl=document.getElementById('pkgTable');
        if(!q||!tbl) return;
        const rows=[...tbl.tBodies[0].rows].filter(r=> !r.textContent.includes('Parent Directory'));
        function filter(){{
            const s=q.value.trim().toLowerCase();
            let vis=0;
            rows.forEach(r=>{{
                const ok=!s || r.textContent.toLowerCase().includes(s);
                r.style.display=ok?'':'none';
                if(ok) vis++;
            }});
            if(c) c.textContent=vis+' / '+rows.length+' files'+(s?' for "'+q.value+'"':'');
            const no=document.getElementById('pkgNoResults');
            const qSpan=document.getElementById('pkgQuery');
            const empty=s && vis===0;
            tbl.style.display=empty?'none':'';
            if(no){{ no.style.display=empty?'block':'none'; if(qSpan) qSpan.textContent=q.value; }}
        }}
        q.addEventListener('input',filter);
        if(clear) clear.addEventListener('click',()=>{{q.value='';filter();q.focus();}});
        filter();
    }})();
    </script>
</body>
</html>
'''
        suite_index.write_text(suite_index_html, encoding="utf-8")
        # also copy to suite.html for direct link
        (suite_dir / f"{suite}.html").write_text(suite_index_html, encoding="utf-8")
        # pool/<suite>/main/index.html -> lists a-z letters
        import string
        letters = list(string.ascii_lowercase)
        letter_rows = []
        for letter in letters:
            ldir = main_dir / letter
            mtime = _fmt_date(ldir.stat().st_mtime) if ldir.exists() else "-"
            # count packages in letter
            debs_cnt = len(list(ldir.rglob("*.deb"))) if ldir.exists() else 0
            desc = f"{debs_cnt} package" + ("s" if debs_cnt!=1 else "") if debs_cnt else "empty"
            letter_rows.append(f'                <tr><td><span class="icon">📁</span><a href="{letter}/">{letter}/</a></td><td>{mtime}</td><td class="size">-</td><td>{desc}</td></tr>')
        letters_tbody = "\n".join(letter_rows)
        main_index = main_dir / "index.html"
        main_index_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Index of /pool/{suite}/main - tuffgit21 APT</title>
    <link rel="icon" type="image/svg+xml" href="../../../favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="../../../favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="../../../favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="180x180" href="../../../apple-touch-icon.png">
    <link rel="shortcut icon" href="../../../favicon.ico">
    <link rel="manifest" href="../../../manifest.json">
    <meta name="theme-color" content="#A80030">
    <link rel="stylesheet" href="../../../styles.css">
</head>
<body>
    <header class="site-header">
        <h1>Index of /pool/{suite}/main <span>— tuffgit21 APT</span></h1>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">🌙 Dark</button>
        <p><a href="../../../index.html" style="color:white; text-decoration: underline;">&larr; Back to repository index</a> &nbsp; <a href="../index.html" style="color:white; text-decoration: underline;">↑ {suite}</a></p>
    </header>
    <div class="container">
        <div class="search-box"><input type="search" id="pkgSearch" placeholder="Filter..." aria-label="Filter"><span class="count" id="pkgCount"></span><button class="badge" id="pkgClear" type="button" style="cursor:pointer;">Clear</button></div>
        <div class="table-wrap"><table class="index" id="pkgTable">
            <thead><tr><th>Name</th><th>Last modified</th><th class="size">Size</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>
{letters_tbody}
            </tbody>
        </table></div>
        <p id="pkgNoResults" class="muted" style="display:none; text-align:center; padding:0.75rem; border:1px dashed var(--border); border-radius:6px; margin-top:0.5rem;">No packages found for "<span id="pkgQuery"></span>" — try another name, version, arch or file.</p>
    </div>
    <script>
    (function(){{
        const k='tuffgit21-theme';
        const b=document.getElementById('themeToggle');
        function apply(t){{
            if(t==='dark') document.documentElement.setAttribute('data-theme','dark');
            else if(t==='light') document.documentElement.setAttribute('data-theme','light');
            else document.documentElement.removeAttribute('data-theme');
            if(b) b.textContent = (t==='dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? '☀️ Light' : '🌙 Dark';
        }}
        let cur=localStorage.getItem(k);
        apply(cur);
        if(b) b.onclick=()=>{{
            const isDark=document.documentElement.getAttribute('data-theme')==='dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const nxt=isDark?'light':'dark';
            localStorage.setItem(k,nxt); apply(nxt);
        }};
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{{ if(!localStorage.getItem(k)) apply(null); }});
    }})();
    </script>
    <script>
    (function(){{
        const q=document.getElementById('pkgSearch');
        const c=document.getElementById('pkgCount');
        const clear=document.getElementById('pkgClear');
        const tbl=document.getElementById('pkgTable');
        if(!q||!tbl) return;
        const rows=[...tbl.tBodies[0].rows].filter(r=> !r.textContent.includes('Parent Directory'));
        function filter(){{
            const s=q.value.trim().toLowerCase();
            let vis=0;
            rows.forEach(r=>{{
                const ok=!s || r.textContent.toLowerCase().includes(s);
                r.style.display=ok?'':'none';
                if(ok) vis++;
            }});
            if(c) c.textContent=vis+' / '+rows.length+' files'+(s?' for "'+q.value+'"':'');
            const no=document.getElementById('pkgNoResults');
            const qSpan=document.getElementById('pkgQuery');
            const empty=s && vis===0;
            tbl.style.display=empty?'none':'';
            if(no){{ no.style.display=empty?'block':'none'; if(qSpan) qSpan.textContent=q.value; }}
        }}
        q.addEventListener('input',filter);
        if(clear) clear.addEventListener('click',()=>{{q.value='';filter();q.focus();}});
        filter();
    }})();
    </script>
</body>
</html>
'''
        main_index.write_text(main_index_html, encoding="utf-8")
        (main_dir / "main.html").write_text(main_index_html, encoding="utf-8")
        print(f"Updated {suite_dir.relative_to(REPO)}/index.html and {main_dir.relative_to(REPO)}/index.html")

    # pool/main/index.html for stable pool listing letters
    pool_main = REPO / "pool" / "main"
    pool_main.mkdir(parents=True, exist_ok=True)
    letters = list(string.ascii_lowercase)
    letter_rows_pm = []
    for letter in letters:
        ldir = pool_main / letter
        mtime = _fmt_date(ldir.stat().st_mtime) if ldir.exists() else "-"
        debs_cnt = len(list(ldir.rglob("*.deb"))) if ldir.exists() else 0
        desc = f"{debs_cnt} package" + ("s" if debs_cnt!=1 else "") if debs_cnt else "empty"
        letter_rows_pm.append(f'                <tr><td><span class="icon">📁</span><a href="{letter}/">{letter}/</a></td><td>{mtime}</td><td class="size">-</td><td>{desc}</td></tr>')
    pm_tbody = "\n".join(letter_rows_pm)
    pm_index = pool_main / "index.html"
    pm_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Index of /pool/main - tuffgit21 APT</title>
    <link rel="icon" type="image/svg+xml" href="../../favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="../../favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="../../favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="180x180" href="../../apple-touch-icon.png">
    <link rel="shortcut icon" href="../../favicon.ico">
    <link rel="manifest" href="../../manifest.json">
    <meta name="theme-color" content="#A80030">
    <link rel="stylesheet" href="../../styles.css">
</head>
<body>
    <header class="site-header">
        <h1>Index of /pool/main <span>— tuffgit21 APT</span></h1>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">🌙 Dark</button>
        <p><a href="../../index.html" style="color:white; text-decoration: underline;">&larr; Back to repository index</a> &nbsp; <a href="../index.html" style="color:white; text-decoration: underline;">↑ pool</a></p>
    </header>
    <div class="container">
        <div class="search-box"><input type="search" id="pkgSearch" placeholder="Filter..." aria-label="Filter"><span class="count" id="pkgCount"></span><button class="badge" id="pkgClear" type="button" style="cursor:pointer;">Clear</button></div>
        <div class="table-wrap"><table class="index" id="pkgTable">
            <thead><tr><th>Name</th><th>Last modified</th><th class="size">Size</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>
{pm_tbody}
            </tbody>
        </table></div>
        <p id="pkgNoResults" class="muted" style="display:none; text-align:center; padding:0.75rem; border:1px dashed var(--border); border-radius:6px; margin-top:0.5rem;">No packages found for "<span id="pkgQuery"></span>" — try another name, version, arch or file.</p>
    </div>
    <script>
    (function(){{
        const k='tuffgit21-theme';
        const b=document.getElementById('themeToggle');
        function apply(t){{
            if(t==='dark') document.documentElement.setAttribute('data-theme','dark');
            else if(t==='light') document.documentElement.setAttribute('data-theme','light');
            else document.documentElement.removeAttribute('data-theme');
            if(b) b.textContent = (t==='dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? '☀️ Light' : '🌙 Dark';
        }}
        let cur=localStorage.getItem(k);
        apply(cur);
        if(b) b.onclick=()=>{{
            const isDark=document.documentElement.getAttribute('data-theme')==='dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const nxt=isDark?'light':'dark';
            localStorage.setItem(k,nxt); apply(nxt);
        }};
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{{ if(!localStorage.getItem(k)) apply(null); }});
    }})();
    </script>
    <script>
    (function(){{
        const q=document.getElementById('pkgSearch');
        const c=document.getElementById('pkgCount');
        const clear=document.getElementById('pkgClear');
        const tbl=document.getElementById('pkgTable');
        if(!q||!tbl) return;
        const rows=[...tbl.tBodies[0].rows].filter(r=> !r.textContent.includes('Parent Directory'));
        function filter(){{
            const s=q.value.trim().toLowerCase();
            let vis=0;
            rows.forEach(r=>{{
                const ok=!s || r.textContent.toLowerCase().includes(s);
                r.style.display=ok?'':'none';
                if(ok) vis++;
            }});
            if(c) c.textContent=vis+' / '+rows.length+' files'+(s?' for "'+q.value+'"':'');
            const no=document.getElementById('pkgNoResults');
            const qSpan=document.getElementById('pkgQuery');
            const empty=s && vis===0;
            tbl.style.display=empty?'none':'';
            if(no){{ no.style.display=empty?'block':'none'; if(qSpan) qSpan.textContent=q.value; }}
        }}
        q.addEventListener('input',filter);
        if(clear) clear.addEventListener('click',()=>{{q.value='';filter();q.focus();}});
        filter();
    }})();
    </script>
</body>
</html>
'''
    pm_index.write_text(pm_html, encoding="utf-8")
    (pool_main / "main.html").write_text(pm_html, encoding="utf-8")
    print(f"Updated {pm_index.relative_to(REPO)}")

    # keep dists/stable extra HTML etc - also create dists pages for other suites
    for suite in SUITES:
        d = REPO / "dists" / suite
        # ensure index html exists for dists/<suite> and dists/<suite>/main etc
        # generate proper browsable indexes for each suite, mirroring stable's templates
        # read stable's templates
        stable_suite = REPO / "dists" / "stable"
        # helper to create suite index
        suite_idx = d / "index.html"
        # Build suite index HTML (lists main/, Release, InRelease)
        # Use same structure as stable's index but suite-specific
        badge = SUITES[suite]["badge"]
        badge_cls = "badge-ok" if suite=="stable" else ("badge-optional" if suite=="archives-stable" else "badge-warn")
        dists_suite_mtime_main = _fmt_date((d / "main").stat().st_mtime) if (d / "main").exists() else "-"
        # compute sizes/dates for files if exist
        def f_info(p):
            if p.exists():
                return _fmt_date(p.stat().st_mtime), _fmt_size(p.stat().st_size)
            return "-", "-"
        rel_date, rel_size = f_info(d / "Release")
        inrel_date, inrel_size = f_info(d / "InRelease")
        gpg_date, gpg_size = f_info(d / "Release.gpg")
        suite_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Index of /dists/{suite} - tuffgit21 APT</title>
    <link rel="icon" type="image/svg+xml" href="../../favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="../../favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="../../favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="180x180" href="../../apple-touch-icon.png">
    <link rel="shortcut icon" href="../../favicon.ico">
    <link rel="manifest" href="../../manifest.json">
    <meta name="theme-color" content="#A80030">
    <link rel="stylesheet" href="../../styles.css">
</head>
<body>
    <header class="site-header">
        <h1>Index of /dists/{suite} <span>— tuffgit21 APT</span> <span class="badge {badge_cls}">{badge}</span></h1>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">🌙 Dark</button>
        <p><a href="../../index.html" style="color:white; text-decoration: underline;">&larr; Back to repository index</a></p>
        <p class="muted" style="color:rgba(255,255,255,0.9);">Suite: {suite} &middot; {SUITES[suite]["description"]}</p>
    </header>
    <div class="container">
        <div class="search-box"><input type="search" id="pkgSearch" placeholder="Filter..." aria-label="Filter"><span class="count" id="pkgCount"></span><button class="badge" id="pkgClear" type="button" style="cursor:pointer;">Clear</button></div>
        <div class="table-wrap"><table class="index" id="pkgTable">
            <thead><tr><th>Name</th><th>Last modified</th><th class="size">Size</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>
                <tr><td><span class="icon">📁</span><a href="main/">main/</a></td><td>{dists_suite_mtime_main}</td><td class="size">-</td><td>Component</td></tr>
                <tr><td><span class="icon">📄</span><a href="Release">Release</a></td><td>{rel_date}</td><td class="size">{rel_size}</td><td>Checksums</td></tr>
                <tr><td><span class="icon">📄</span><a href="InRelease">InRelease</a></td><td>{inrel_date}</td><td class="size">{inrel_size}</td><td>Clearsigned Release</td></tr>
                <tr><td><span class="icon">📄</span><a href="Release.gpg">Release.gpg</a></td><td>{gpg_date}</td><td class="size">{gpg_size}</td><td>Detached signature</td></tr>
            </tbody>
        </table></div>
        <p id="pkgNoResults" class="muted" style="display:none; text-align:center; padding:0.75rem; border:1px dashed var(--border); border-radius:6px; margin-top:0.5rem;">No packages found for "<span id="pkgQuery"></span>" — try another name, version, arch or file.</p>
    </div>
    <script>
    (function(){{
        const k='tuffgit21-theme';
        const b=document.getElementById('themeToggle');
        function apply(t){{
            if(t==='dark') document.documentElement.setAttribute('data-theme','dark');
            else if(t==='light') document.documentElement.setAttribute('data-theme','light');
            else document.documentElement.removeAttribute('data-theme');
            if(b) b.textContent = (t==='dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? '☀️ Light' : '🌙 Dark';
        }}
        let cur=localStorage.getItem(k);
        apply(cur);
        if(b) b.onclick=()=>{{
            const isDark=document.documentElement.getAttribute('data-theme')==='dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const nxt=isDark?'light':'dark';
            localStorage.setItem(k,nxt); apply(nxt);
        }};
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{{ if(!localStorage.getItem(k)) apply(null); }});
    }})();
    </script>
    <script>
    (function(){{
        const q=document.getElementById('pkgSearch');
        const c=document.getElementById('pkgCount');
        const clear=document.getElementById('pkgClear');
        const tbl=document.getElementById('pkgTable');
        if(!q||!tbl) return;
        const rows=[...tbl.tBodies[0].rows].filter(r=> !r.textContent.includes('Parent Directory'));
        function filter(){{
            const s=q.value.trim().toLowerCase();
            let vis=0;
            rows.forEach(r=>{{
                const ok=!s || r.textContent.toLowerCase().includes(s);
                r.style.display=ok?'':'none';
                if(ok) vis++;
            }});
            if(c) c.textContent=vis+' / '+rows.length+' files'+(s?' for "'+q.value+'"':'');
            const no=document.getElementById('pkgNoResults');
            const qSpan=document.getElementById('pkgQuery');
            const empty=s && vis===0;
            tbl.style.display=empty?'none':'';
            if(no){{ no.style.display=empty?'block':'none'; if(qSpan) qSpan.textContent=q.value; }}
        }}
        q.addEventListener('input',filter);
        if(clear) clear.addEventListener('click',()=>{{q.value='';filter();q.focus();}});
        filter();
    }})();
    </script>
</body>
</html>
'''
        suite_idx.write_text(suite_html, encoding="utf-8")
        (d / f"{suite}.html").write_text(suite_html, encoding="utf-8")
        # dists/<suite>/main/index.html - lists both archs
        main_dir = d / "main"
        main_dir.mkdir(parents=True, exist_ok=True)
        # ensure both arch dirs exist and build rows
        arch_rows = []
        for arch in SUPPORTED_ARCHES:
            bin_dir_tmp = main_dir / f"binary-{arch}"
            bin_dir_tmp.mkdir(parents=True, exist_ok=True)
            mtime = _fmt_date(bin_dir_tmp.stat().st_mtime) if bin_dir_tmp.exists() else "-"
            arch_rows.append(f'                <tr><td><span class="icon">📁</span><a href="binary-{arch}/">binary-{arch}/</a></td><td>{mtime}</td><td class="size">-</td><td>Binary packages ({arch})</td></tr>')
        arch_rows_str = "\n".join(arch_rows)
        main_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Index of /dists/{suite}/main - tuffgit21 APT</title>
    <link rel="icon" type="image/svg+xml" href="../../../favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="../../../favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="../../../favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="180x180" href="../../../apple-touch-icon.png">
    <link rel="shortcut icon" href="../../../favicon.ico">
    <link rel="manifest" href="../../../manifest.json">
    <meta name="theme-color" content="#A80030">
    <link rel="stylesheet" href="../../../styles.css">
</head>
<body>
    <header class="site-header">
        <h1>Index of /dists/{suite}/main <span>— tuffgit21 APT</span></h1>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">🌙 Dark</button>
        <p><a href="../../../index.html" style="color:white; text-decoration: underline;">&larr; Back to repository index</a></p>
    </header>
    <div class="container">
        <div class="search-box"><input type="search" id="pkgSearch" placeholder="Filter..." aria-label="Filter"><span class="count" id="pkgCount"></span><button class="badge" id="pkgClear" type="button" style="cursor:pointer;">Clear</button></div>
        <div class="table-wrap"><table class="index" id="pkgTable">
            <thead><tr><th>Name</th><th>Last modified</th><th class="size">Size</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>
{arch_rows_str}
            </tbody>
        </table></div>
        <p id="pkgNoResults" class="muted" style="display:none; text-align:center; padding:0.75rem; border:1px dashed var(--border); border-radius:6px; margin-top:0.5rem;">No packages found for "<span id="pkgQuery"></span>" — try another name, version, arch or file.</p>
    </div>
    <script>
    (function(){{
        const k='tuffgit21-theme';
        const b=document.getElementById('themeToggle');
        function apply(t){{
            if(t==='dark') document.documentElement.setAttribute('data-theme','dark');
            else if(t==='light') document.documentElement.setAttribute('data-theme','light');
            else document.documentElement.removeAttribute('data-theme');
            if(b) b.textContent = (t==='dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? '☀️ Light' : '🌙 Dark';
        }}
        let cur=localStorage.getItem(k);
        apply(cur);
        if(b) b.onclick=()=>{{
            const isDark=document.documentElement.getAttribute('data-theme')==='dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const nxt=isDark?'light':'dark';
            localStorage.setItem(k,nxt); apply(nxt);
        }};
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{{ if(!localStorage.getItem(k)) apply(null); }});
    }})();
    </script>
    <script>
    (function(){{
        const q=document.getElementById('pkgSearch');
        const c=document.getElementById('pkgCount');
        const clear=document.getElementById('pkgClear');
        const tbl=document.getElementById('pkgTable');
        if(!q||!tbl) return;
        const rows=[...tbl.tBodies[0].rows].filter(r=> !r.textContent.includes('Parent Directory'));
        function filter(){{
            const s=q.value.trim().toLowerCase();
            let vis=0;
            rows.forEach(r=>{{
                const ok=!s || r.textContent.toLowerCase().includes(s);
                r.style.display=ok?'':'none';
                if(ok) vis++;
            }});
            if(c) c.textContent=vis+' / '+rows.length+' files'+(s?' for "'+q.value+'"':'');
            const no=document.getElementById('pkgNoResults');
            const qSpan=document.getElementById('pkgQuery');
            const empty=s && vis===0;
            tbl.style.display=empty?'none':'';
            if(no){{ no.style.display=empty?'block':'none'; if(qSpan) qSpan.textContent=q.value; }}
        }}
        q.addEventListener('input',filter);
        if(clear) clear.addEventListener('click',()=>{{q.value='';filter();q.focus();}});
        filter();
    }})();
    </script>
</body>
</html>
'''
        (main_dir / "index.html").write_text(main_html, encoding="utf-8")
        (main_dir / "main.html").write_text(main_html, encoding="utf-8")
        # dists/<suite>/main/binary-<arch>/index.html for each arch
        for arch in SUPPORTED_ARCHES:
            bin_dir = main_dir / f"binary-{arch}"
            bin_dir.mkdir(parents=True, exist_ok=True)
            pkg_path = bin_dir / "Packages"
            pkg_gz_path = bin_dir / "Packages.gz"
            pkg_date, pkg_size = f_info(pkg_path)
            pkg_gz_date, pkg_gz_size = f_info(pkg_gz_path)
            bin_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Index of /dists/{suite}/main/binary-{arch} - tuffgit21 APT</title>
    <link rel="icon" type="image/svg+xml" href="../../../../favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="../../../../favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="../../../../favicon-16x16.png">
    <link rel="apple-touch-icon" sizes="180x180" href="../../../../apple-touch-icon.png">
    <link rel="shortcut icon" href="../../../../favicon.ico">
    <link rel="manifest" href="../../../../manifest.json">
    <meta name="theme-color" content="#A80030">
    <link rel="stylesheet" href="../../../../styles.css">
</head>
<body>
    <header class="site-header">
        <h1>Index of /dists/{suite}/main/binary-{arch} <span>— tuffgit21 APT</span></h1>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle theme">🌙 Dark</button>
        <p><a href="../../../../index.html" style="color:white; text-decoration: underline;">&larr; Back to repository index</a></p>
    </header>
    <div class="container">
        <div class="search-box"><input type="search" id="pkgSearch" placeholder="Filter..." aria-label="Filter"><span class="count" id="pkgCount"></span><button class="badge" id="pkgClear" type="button" style="cursor:pointer;">Clear</button></div>
        <div class="table-wrap"><table class="index" id="pkgTable">
            <thead><tr><th>Name</th><th>Last modified</th><th class="size">Size</th><th>Description</th></tr></thead>
            <tbody>
                <tr><td><span class="icon">⬆️</span><a href="../index.html">Parent Directory</a></td><td>-</td><td class="size">-</td><td></td></tr>
                <tr><td><span class="icon">📄</span><a href="Packages">Packages</a></td><td>{pkg_date}</td><td class="size">{pkg_size}</td><td>{arch}/all index</td></tr>
                <tr><td><span class="icon">📄</span><a href="Packages.gz">Packages.gz</a></td><td>{pkg_gz_date}</td><td class="size">{pkg_gz_size}</td><td>Compressed index</td></tr>
            </tbody>
        </table></div>
        <p id="pkgNoResults" class="muted" style="display:none; text-align:center; padding:0.75rem; border:1px dashed var(--border); border-radius:6px; margin-top:0.5rem;">No packages found for "<span id="pkgQuery"></span>" — try another name, version, arch or file.</p>
    </div>
    <script>
    (function(){{
        const k='tuffgit21-theme';
        const b=document.getElementById('themeToggle');
        function apply(t){{
            if(t==='dark') document.documentElement.setAttribute('data-theme','dark');
            else if(t==='light') document.documentElement.setAttribute('data-theme','light');
            else document.documentElement.removeAttribute('data-theme');
            if(b) b.textContent = (t==='dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? '☀️ Light' : '🌙 Dark';
        }}
        let cur=localStorage.getItem(k);
        apply(cur);
        if(b) b.onclick=()=>{{
            const isDark=document.documentElement.getAttribute('data-theme')==='dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const nxt=isDark?'light':'dark';
            localStorage.setItem(k,nxt); apply(nxt);
        }};
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{{ if(!localStorage.getItem(k)) apply(null); }});
    }})();
    </script>
    <script>
    (function(){{
        const q=document.getElementById('pkgSearch');
        const c=document.getElementById('pkgCount');
        const clear=document.getElementById('pkgClear');
        const tbl=document.getElementById('pkgTable');
        if(!q||!tbl) return;
        const rows=[...tbl.tBodies[0].rows].filter(r=> !r.textContent.includes('Parent Directory'));
        function filter(){{
            const s=q.value.trim().toLowerCase();
            let vis=0;
            rows.forEach(r=>{{
                const ok=!s || r.textContent.toLowerCase().includes(s);
                r.style.display=ok?'':'none';
                if(ok) vis++;
            }});
            if(c) c.textContent=vis+' / '+rows.length+' files'+(s?' for "'+q.value+'"':'');
            const no=document.getElementById('pkgNoResults');
            const qSpan=document.getElementById('pkgQuery');
            const empty=s && vis===0;
            tbl.style.display=empty?'none':'';
            if(no){{ no.style.display=empty?'block':'none'; if(qSpan) qSpan.textContent=q.value; }}
        }}
        q.addEventListener('input',filter);
        if(clear) clear.addEventListener('click',()=>{{q.value='';filter();q.focus();}});
        filter();
    }})();
    </script>
</body>
</html>
'''
            (bin_dir / "index.html").write_text(bin_html, encoding="utf-8")
            (bin_dir / f"binary-{arch}.html").write_text(bin_html, encoding="utf-8")
        print(f"  Updated dists/{suite}/ browsable indexes (arches: {', '.join(SUPPORTED_ARCHES)})")

if __name__ == "__main__":
    main()
