#!/usr/bin/env bash
# Build patched wheels for Real-ESRGAN's unmaintained dependencies.
#
# basicsr / gfpgan / facexlib read their version in setup.py via exec() and
# locals()['__version__']. Python 3.13+ made that locals() snapshot immutable
# for this use case, so those sdists fail on the Python 3.14 image. Patch the
# version reader to exec into an explicit dict and build reusable wheels.
set -euo pipefail

OUT="${1:-/wheels}"
mkdir -p "$OUT"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

SPECS="basicsr==1.4.2 gfpgan==1.3.8 facexlib==0.3.0"

for spec in $SPECS; do
  name="${spec%%==*}"
  ver="${spec##*==}"
  url="$(python - "$name" "$ver" <<'PY'
import json
import sys
import urllib.request

name, ver = sys.argv[1], sys.argv[2]
data = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json"))
for file in data["urls"]:
    if file["packagetype"] == "sdist":
        print(file["url"])
        break
else:
    sys.exit(f"no sdist found for {name}=={ver}")
PY
)"
  echo ">> fetching ${name} ${ver}: ${url}"
  curl -fsSL "$url" -o "${name}.tar.gz"
  tar xzf "${name}.tar.gz"
done

echo ">> patching get_version()"
python - <<'PY'
import pathlib

old_exec = "exec(compile(f.read(), version_file, 'exec'))"
new_exec = "_ver_ns = {}\n        exec(compile(f.read(), version_file, 'exec'), _ver_ns)"
old_ret = "return locals()['__version__']"
new_ret = "return _ver_ns['__version__']"
patched = 0

for setup in pathlib.Path(".").glob("*/setup.py"):
    source = setup.read_text()
    if old_exec in source and old_ret in source:
        setup.write_text(source.replace(old_exec, new_exec).replace(old_ret, new_ret))
        print("   patched", setup)
        patched += 1

assert patched == 3, f"expected to patch 3 setup.py files, patched {patched}"
PY

echo ">> building wheels into ${OUT}"
pip wheel --no-deps -w "$OUT" ./basicsr-* ./gfpgan-* ./facexlib-*
ls -l "$OUT"
