# tuffgit21 APT Repo

Debian APT repository hosted on GitHub Pages: `https://tuffgit21.github.io/tuffgit21-APT-repo`

## Suites (distributions)

| Suite | Status | Description | Pool |
|-------|--------|-------------|------|
| `stable` | **Recommended** | Actively maintained, stable releases | `pool/main/` |
| `archives-stable` | **Optional** — can be opt in | Discontinued stable projects (last known stable, no further updates) | `pool/archives-stable/main/` |
| `archives-unstable` | **Not recommended** — can be opt in | Discontinued unstable / buggy projects (may have known bugs / security issues) | `pool/archives-unstable/main/` |
| `testing` | **Not recommended** — can be opt in | Upcoming / pre-release builds for early testing | `pool/testing/main/` |

All suites use component `main` and archs `amd64 arm64 all`, signed with the same GPG key.

## Structure
```
/
├── index.html              # GitHub Pages entry point
├── .nojekyll               # bypass Jekyll so pool/ is served
├── dists/
│   ├── stable/main/binary-amd64/            # Recommended (amd64)
│   │   ├── Packages (+ .gz)
│   ├── stable/main/binary-arm64/            # Recommended (arm64)
│   │   ├── Packages (+ .gz)
│   ├── stable/Release / InRelease / Release.gpg (covers amd64 arm64 all)
│   ├── archives-stable/main/binary-amd64/   # Optional — discontinued stable (amd64)
│   ├── archives-stable/main/binary-arm64/   # Optional — discontinued stable (arm64)
│   ├── archives-unstable/main/binary-amd64/ # Not recommended — buggy (amd64)
│   ├── archives-unstable/main/binary-arm64/ # Not recommended — buggy (arm64)
│   ├── testing/main/binary-amd64/           # Not recommended — upcoming (amd64)
│   └── testing/main/binary-arm64/           # Not recommended — upcoming (arm64)
├── pool/
│   ├── main/<letter>/<pkg>/*.deb                         # stable
│   ├── archives-stable/main/<letter>/<pkg>/*.deb         # archives-stable
│   ├── archives-unstable/main/<letter>/<pkg>/*.deb       # archives-unstable
│   └── testing/main/<letter>/<pkg>/*.deb                 # testing
└── pool/main/<letter>/<letter>.html  # browsable indexes (per suite)
```

## Usage (client) — signed (recommended)

### stable (recommended)
```bash
curl -fsSL https://tuffgit21.github.io/tuffgit21-APT-repo/public.key | sudo gpg --dearmor -o /usr/share/keyrings/tuffgit21.gpg
echo "deb [signed-by=/usr/share/keyrings/tuffgit21.gpg] https://tuffgit21.github.io/tuffgit21-APT-repo stable main" | sudo tee /etc/apt/sources.list.d/tuffgit21.list
sudo apt update
sudo apt install pyshell   # example
```
Fingerprint: `38A9 75C0 7DA0 CB7C 8248 4843 B88A E409 4A47 8224` — verify with `gpg --show-keys public.key`

### archives-stable — discontinued stable (Optional, can be opt in)
```bash
echo "deb [signed-by=/usr/share/keyrings/tuffgit21.gpg] https://tuffgit21.github.io/tuffgit21-APT-repo archives-stable main" | sudo tee /etc/apt/sources.list.d/tuffgit21-archives-stable.list
sudo apt update
# then e.g. sudo apt install <legacy-package>
```
Discontinued but last-known-stable. No further updates. Enable only if you need the legacy version.

### archives-unstable — discontinued unstable/buggy (Not recommended, can be opt in)
```bash
echo "deb [signed-by=/usr/share/keyrings/tuffgit21.gpg] https://tuffgit21.github.io/tuffgit21-APT-repo archives-unstable main" | sudo tee /etc/apt/sources.list.d/tuffgit21-archives-unstable.list
sudo apt update
```
**Not recommended** — may contain bugs or security issues. Opt-in only if you understand the risks.

### testing — upcoming projects (Not recommended, can be opt in)
```bash
echo "deb [signed-by=/usr/share/keyrings/tuffgit21.gpg] https://tuffgit21.github.io/tuffgit21-APT-repo testing main" | sudo tee /etc/apt/sources.list.d/tuffgit21-testing.list
sudo apt update
```
**Not recommended** for production — pre-release, expect breakage. Use APT pinning (`man apt_preferences`) to keep priority low.

### Unsigned fallback (any suite)
```bash
echo "deb [trusted=yes] https://tuffgit21.github.io/tuffgit21-APT-repo stable main" | sudo tee /etc/apt/sources.list.d/tuffgit21.list
# replace 'stable' with archives-stable / archives-unstable / testing as needed
```

You can enable multiple suites at once by adding multiple `.list` files.

## Maintainer: adding a package

1. Put `.deb` under the correct pool:
   - stable: `pool/main/<first-letter>/<package-name>/` e.g. `pool/main/p/pyshell/pyshell_1.0.0_all.deb`
   - archives-stable: `pool/archives-stable/main/<first-letter>/<package-name>/`
   - archives-unstable: `pool/archives-unstable/main/<first-letter>/<package-name>/`
   - testing: `pool/testing/main/<first-letter>/<package-name>/`
2. Regenerate metadata:
   ```bash
   python3 update-repo.py
   # regenerates Packages / Packages.gz / Release / InRelease / Release.gpg for ALL suites
   # also regenerates HTML indexes (root + per-letter pool pages + dists/)
   ```
    Manual alternative (Debian with dpkg-dev, stable only):
    ```bash
    dpkg-scanpackages --multiversion pool/main /dev/null > dists/stable/main/binary-amd64/Packages
    gzip -k -f dists/stable/main/binary-amd64/Packages
    # arm64 (filter by Architecture):
    dpkg-scanpackages --multiversion --arch arm64 pool/main /dev/null > dists/stable/main/binary-arm64/Packages
    gzip -k -f dists/stable/main/binary-arm64/Packages
    # then recreate dists/stable/Release hashes for both arches (amd64 arm64 all)
    ```
3. Commit & push to `main`. Enable GitHub Pages: Settings → Pages → Source: `main` branch, `/ (root)`.

## GitHub Pages setup
- Repo: `tuffgit21/tuffgit21-APT-repo`
- Branch: `main`, Folder: `/ (root)` (not `/website`)
- The old `website/index.html` is now superseded by root `index.html`.
