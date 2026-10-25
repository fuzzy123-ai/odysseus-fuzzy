#!/usr/bin/env python3
"""Explicitly gated transport for one fixed transient diagnostic service.

No backup or installation. The transient service reproduces the canonical
sandbox but does not own RuntimeDirectory, so existing incident receipts remain.
Debian semantics: https://manpages.debian.org/trixie/systemd/systemd-run.1.en.html
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import threading
from types import SimpleNamespace
from ops.homeserver import redacted_predeploy_backup_root_helper_preexec_stage_diagnostic as diagnostic

SOURCE_SHA256="1b845efa76d9d3cdadfd9642575e49de5e74eed1bfde717a64a751352406d78d"
SOURCE_PATH=Path(__file__).with_name("redacted_predeploy_backup_root_helper_preexec_stage_diagnostic.py")
TIMEOUT_SECONDS=70
PROPERTIES=(
 "User=root","Group=root","UMask=0077","Type=oneshot","TimeoutStartSec=40s",
 "TimeoutStopSec=5s","KillMode=control-group","SendSIGKILL=yes",
 "NoNewPrivileges=yes","PrivateTmp=yes","PrivateNetwork=yes","PrivateDevices=yes",
 "ProtectSystem=strict","ProtectHome=read-only","ProtectKernelTunables=yes",
 "ProtectKernelModules=yes","ProtectControlGroups=yes",
 "CapabilityBoundingSet=CAP_SYS_ADMIN CAP_SETUID CAP_SETGID CAP_CHOWN CAP_DAC_READ_SEARCH",
 "ReadWritePaths=/mnt/backup /run/odysseus-predeploy-backup-root-helper /var/lib/odysseus-predeploy-backup-root-helper",
)
BOOTSTRAP="""import sys,hashlib,json,types
raw=sys.stdin.buffer.read(400001)
if not raw or len(raw)>400000 or hashlib.sha256(raw).hexdigest()!="""+repr(SOURCE_SHA256)+""":raise SystemExit(70)
m=types.ModuleType("fixed_stage_diagnostic")
try:
 exec(compile(raw,"<fixed-stage-diagnostic>","exec"),m.__dict__)
 value=m.collect(execute=True)
 if not m.validate_envelope(value):raise SystemExit(70)
 print(json.dumps(value,sort_keys=True,separators=(",",":")))
except Exception:raise SystemExit(70)
"""
REMOTE_ARGV=(
 "/usr/bin/timeout","--signal=KILL","65s","/usr/bin/sudo","-n",
 "/usr/bin/systemd-run","--quiet","--wait","--pipe","--collect",
 "--unit="+diagnostic.TRANSIENT_UNIT,
)+tuple("--property="+p for p in PROPERTIES)+(
 "--","/usr/bin/python3","-I","-c",BOOTSTRAP,
)
SSH_COMMAND=("ssh","-F","ops/homeserver/ssh_config","odysseus-homeserver",shlex.join(REMOTE_ARGV))

def _bounded_process(command,payload,timeout):
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,shell=False)
    output=bytearray();failed=threading.Event();oversized=threading.Event()
    def write():
        try: process.stdin.write(payload);process.stdin.close()
        except Exception: failed.set()
    def read():
        try:
            while True:
                part=process.stdout.read(min(4096,8193-len(output)))
                if not part: break
                output.extend(part)
                if len(output)>8192:
                    oversized.set();process.kill();break
        except Exception: failed.set()
    writer=threading.Thread(target=write,daemon=True);reader=threading.Thread(target=read,daemon=True)
    writer.start();reader.start()
    try: code=process.wait(timeout=timeout)
    except Exception:
        process.kill();process.wait();raise
    finally:
        writer.join(1);reader.join(1)
        for stream in (process.stdin,process.stdout):
            try: stream.close()
            except Exception: pass
    if failed.is_set() or writer.is_alive() or reader.is_alive():raise RuntimeError()
    return SimpleNamespace(returncode=code,stdout=bytes(output),oversized=oversized.is_set())

def collect(*,execute=False,runner=_bounded_process):
    if execute is not True:return diagnostic.envelope("blocked","execution_disabled")
    try:
        source=SOURCE_PATH.read_bytes()
        if not source or len(source)>400000 or hashlib.sha256(source).hexdigest()!=SOURCE_SHA256:
            return diagnostic.envelope("blocked","preflight_failed")
        result=runner(SSH_COMMAND,source,TIMEOUT_SECONDS)
        raw=result.stdout
        if (result.returncode!=0 or result.oversized or type(raw) is not bytes or len(raw)>8192
            or raw.count(b"\n")!=1 or not raw.endswith(b"\n") or b"\r" in raw):
            raise ValueError()
        value=json.loads(raw.decode("ascii"))
        if not diagnostic.validate_envelope(value):raise ValueError()
        return dict(value)
    except Exception:
        # Transport failure cannot prove whether the transient unit started.
        return {"schema_id":"odysseus.stage_diagnostic_transport.v1",
            "status":"unknown","error_code":"transport_ambiguous",
            "retry_permitted":False,"raw_output_visible":False}

if __name__=="__main__":
    print(json.dumps(collect(),sort_keys=True,separators=(",",":")))
