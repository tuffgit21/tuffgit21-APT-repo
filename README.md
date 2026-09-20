# tuffgit21 APT Repo

Debian APT repository hosted on GitHub Pages: `https://tuffgit21.github.io/tuffgit21-APT-repo`

## Structure
```
/
├── index.html              # GitHub Pages entry point
├── .nojekyll               # bypass Jekyll so pool/ is served
├── dists/stable/
│   ├── Release             # generated checksums
│   ├── InRelease           # (optional) GPG clearsigned Release
│   └── main/binary-amd64/
│       ├── Packages
│       └── Packages.gz
├── pool/main/<letter>/<pkg>/*.deb
└── pool/main/<letter>/<letter>.html  # browsable indexes
```

## Usage (client) — signed (recommended)

```bash
curl -fsSL https://tuffgit21.github.io/tuffgit21-APT-repo/public.key | sudo gpg --dearmor -o /usr/share/keyrings/tuffgit21.gpg
echo "deb [signed-by=/usr/share/keyrings/tuffgit21.gpg] https://tuffgit21.github.io/tuffgit21-APT-repo stable main" | sudo tee /etc/apt/sources.list.d/tuffgit21.list
sudo apt update
sudo apt install pyshell   # example
```
Fingerprint: `38A9 75C0 7DA0 CB7C 8248 4843 B88A E409 4A47 8224` — verify with `gpg --show-keys public.key`

Unsigned fallback:
```bash
echo "deb [trusted=yes] https://tuffgit21.github.io/tuffgit21-APT-repo stable main" | sudo tee /etc/apt/sources.list.d/tuffgit21.list
```

## Maintainer: adding a package

1. Put `.deb` under `pool/main/<first-letter>/<package-name>/` e.g. `pool/main/p/pyshell/pyshell_1.0.0_all.deb`
2. Regenerate metadata:
   ```bash
   python3 update-repo.py
   # or on Debian with dpkg-dev:
   dpkg-scanpackages --multiversion pool /dev/null > dists/stable/main/binary-amd64/Packages
   gzip -k -f dists/stable/main/binary-amd64/Packages
   # then recreate dists/stable/Release hashes
   ```
3. Commit & push to `main`. Enable GitHub Pages: Settings → Pages → Source: `main` branch, `/ (root)`.

## GitHub Pages setup
- Repo: `tuffgit21/tuffgit21-APT-repo`
- Branch: `main`, Folder: `/ (root)` (not `/website`)
- The old `website/index.html` is now superseded by root `index.html`.
