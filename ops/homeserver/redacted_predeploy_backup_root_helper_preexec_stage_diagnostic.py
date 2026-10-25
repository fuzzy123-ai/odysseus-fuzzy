#!/usr/bin/env python3
"""Fixed-incident stage observer. Live execution requires a separate approval.

Uses the pinned installed helper, never consumes an arm or starts a backup.
The transport supplies the canonical systemd sandbox. Temporary credential
materialization happens only in the child's private mount namespace.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
import select
import stat
import subprocess
import sys
import time
import types
from typing import Any, Mapping

SCHEMA_ID = "odysseus.predeploy_backup_root_helper_preexec_stage_diagnostic.v2"
HELPER_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py"
HELPER_SHA256 = "abf8f859384a9ab21d2c5fb682aabaaff522464eef5d035126065021de373d31"
INCIDENT_REF = "predeploy_backup_root_helper_v1:817874fc0b8049aea74a9e56df28c21c78331282d6492def4b72012b7c2938ec"
INCIDENT_RECEIPT_SHA256 = "6e5aa60ee3d5a2f1d05e00392c17fb953e9e7f7205039121352672280141d069"
UNIT = "odysseus-predeploy-backup-root-helper.service"
TRANSIENT_UNIT = "odysseus-predeploy-backup-root-helper-stage-diagnostic.service"
TIMEOUT_SECONDS = 20
MAX_PROTOCOL_BYTES = 2048
STAGES = (
 "mount_namespace", "private_propagation", "private_view_setup", "view_materialization",
 "credential_directory_owner_mode", "source_repository_identity", "source_bind",
 "source_readonly_remount", "repository_bind", "credential_materialization",
 "credential_self_bind", "credential_readonly_remount", "mount_postflight",
 "close_source_parent", "close_source", "close_repository", "close_credential",
 "identity_drop", "repository_access_after_drop", "execveat",
)
FLAGS = ("incident_verified", "unit_quiescent", "execution_lock_held", "identity_bound",
         "read_command_invoked")
HIDDEN = ("raw_stdout_visible", "raw_stderr_visible", "environment_visible", "paths_visible",
          "hostnames_visible", "secret_values_visible", "repository_write_invoked",
          "effect_may_have_occurred", "manual_recovery_required", "retry_permitted")

def _digest(value):
    return hashlib.sha256(json.dumps({k:v for k,v in value.items() if k != "evidence_sha256"},
        ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()

def envelope(status, code, stage="none", *, ready=False, bound=False, invoked=False):
    value = dict(schema_id=SCHEMA_ID, status=status, error_code=code, first_failed_stage=stage,
        incident_verified=ready, unit_quiescent=ready, execution_lock_held=ready,
        identity_bound=bound, read_command_invoked=invoked)
    value.update({key:False for key in HIDDEN})
    value["evidence_sha256"] = _digest(value)
    return value

def validate_envelope(value):
    keys = set(FLAGS)|set(HIDDEN)|{"schema_id","status","error_code","first_failed_stage","evidence_sha256"}
    if type(value) is not dict or set(value) != keys:
        return False
    if (value["schema_id"] != SCHEMA_ID or any(type(value[k]) is not bool for k in FLAGS+HIDDEN)
        or any(value[k] for k in HIDDEN) or value["evidence_sha256"] != _digest(value)):
        return False
    status, code, stage = value["status"], value["error_code"], value["first_failed_stage"]
    if status == "blocked":
        return code in {"execution_disabled","preflight_failed"} and stage == "none" and not any(value[k] for k in FLAGS)
    if not all(value[k] for k in FLAGS[:4]):
        return False
    if status == "unknown":
        return code in {"timeout","child_signal","protocol_invalid"} and stage == "none" and not value["read_command_invoked"]
    if status != "observed":
        return False
    if code == "stage_failed":
        return stage in STAGES+("exec_returned",) and not value["read_command_invoked"]
    return ((code,stage) in {("none","none"),("restic_read_failed","restic_read")}
            and value["read_command_invoked"])

def _read_fixed(path, maximum, mode):
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        before = os.fstat(fd)
        if not (stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
                and stat.S_IMODE(before.st_mode) == mode and before.st_nlink == 1
                and 0 < before.st_size <= maximum):
            return None
        raw = bytearray()
        while len(raw) <= maximum:
            part = os.read(fd,min(8192,maximum+1-len(raw)))
            if not part: break
            raw.extend(part)
        after = os.fstat(fd)
        fields = ("st_dev","st_ino","st_size","st_mtime_ns","st_ctime_ns")
        if len(raw) != before.st_size or os.read(fd,1) or any(getattr(before,k)!=getattr(after,k) for k in fields):
            return None
        return bytes(raw)
    except Exception:
        return None
    finally:
        if fd is not None: os.close(fd)

def _load_helper():
    raw = _read_fixed(HELPER_PATH,400000,0o700)
    if raw is None or hashlib.sha256(raw).hexdigest() != HELPER_SHA256: return None
    name = "_pinned_stage_helper"
    module = types.ModuleType(name); module.__file__ = "<pinned-helper>"
    try:
        sys.modules[name] = module
        exec(compile(raw,module.__file__,"exec"),module.__dict__)
        return module.__dict__
    except Exception: return None
    finally: sys.modules.pop(name,None)

def _unit_quiescent(runner=subprocess.run):
    keys = ("ActiveState","SubState","Result","ExecMainCode","ExecMainStatus","MainPID","ControlPID")
    try:
        result = runner(["/usr/bin/systemctl","show",UNIT,"--no-pager"]+
            ["--property="+k for k in keys], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=3,check=False,
            env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"},shell=False)
        raw = result.stdout
        if result.returncode != 0 or type(raw) is not bytes or len(raw)>1024: return False
        rows = raw.decode("ascii").splitlines()
        if len(rows)!=len(keys): return False
        fields = dict(row.split("=",1) for row in rows)
        return fields == dict(ActiveState="failed",SubState="failed",Result="exit-code",
            ExecMainCode="1",ExecMainStatus="1",MainPID="0",ControlPID="0")
    except Exception: return False

def _expected_context():
    try:
        with open("/proc/self/cgroup","rb") as stream: raw=stream.read(4097)
        expected = ("0::/system.slice/"+TRANSIENT_UNIT).encode("ascii")
        return os.geteuid()==0 and len(raw)<=4096 and expected in raw.splitlines()
    except Exception: return False

def _validate_incident(helper, receipt, arm, used, now):
    try:
        return (helper["validate_envelope"](receipt) and receipt["status"]=="unknown"
            and receipt["error_code"]=="backup_failed"
            and receipt["action_provenance_ref"]==INCIDENT_REF
            and receipt["evidence_sha256"]==INCIDENT_RECEIPT_SHA256
            and type(arm) is dict and set(arm)=={"schema_id","grant_id","expires_at_epoch","helper_sha256"}
            and arm["schema_id"]==helper["ARM_SCHEMA_ID"] and arm["helper_sha256"]==HELPER_SHA256
            and type(arm["expires_at_epoch"]) is int and arm["expires_at_epoch"]<=now
            and type(arm["grant_id"]) is str and helper["_HEX"].fullmatch(arm["grant_id"]) is not None
            and helper["_arm_reference"](arm["grant_id"],HELPER_SHA256)==INCIDENT_REF
            and used==b"used\n")
    except Exception: return False

def _incident_ready(helper):
    try:
        receipt_raw = _read_fixed(helper["PUBLIC_RECEIPT_PATH"],8192,0o600)
        arm_raw = _read_fixed(helper["STATE_DIR"]+"/arm.json",1024,0o600)
        if receipt_raw is None or arm_raw is None: return False
        arm=json.loads(arm_raw.decode("ascii"))
        if type(arm) is not dict or type(arm.get("grant_id")) is not str or not helper["_HEX"].fullmatch(arm["grant_id"]): return False
        used=_read_fixed(helper["STATE_DIR"]+"/used-"+arm["grant_id"],5,0o600)
        helper["_open_reusable_view_anchor"]()
        return _validate_incident(helper,json.loads(receipt_raw.decode("ascii")),arm,used,time.time())
    except Exception: return False

def _acquire_existing_lock(helper):
    import fcntl
    directory=descriptor=None
    try:
        directory=os.open(helper["STATE_DIR"],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        info=os.fstat(directory)
        if not helper["_safe_dir"](info,0,0o700) or info.st_gid!=0: return None
        descriptor=os.open("execution.lock",os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory)
        info=os.fstat(descriptor)
        if not helper["_safe_file"](info,0,0o600) or info.st_gid!=0: raise OSError()
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=descriptor;descriptor=None
        return result
    except Exception: return None
    finally:
        for fd in (descriptor,directory):
            if fd is not None: os.close(fd)

def _frames(raw):
    if type(raw) is not bytes or not raw or len(raw)>MAX_PROTOCOL_BYTES or not raw.endswith(b"\n"): return None
    try: rows=raw.decode("ascii").splitlines()
    except Exception: return None
    cursor=0
    for stage in STAGES:
        if cursor>=len(rows) or rows[cursor]!="B:"+stage: return None
        cursor+=1
        if stage=="execveat":
            if cursor==len(rows): return "none",True
            if cursor+1==len(rows) and rows[cursor] in {"F:execveat","F:exec_returned"}:
                return rows[cursor][2:],False
            return None
        if cursor>=len(rows): return None
        if rows[cursor]=="F:"+stage:
            return (stage,False) if cursor+1==len(rows) else None
        if rows[cursor]!="D:"+stage: return None
        cursor+=1
    return None

def _interpret(exit_code,raw,*,timed_out=False,signaled=False,truncated=False):
    if timed_out: return "unknown","timeout","none",False
    if signaled: return "unknown","child_signal","none",False
    parsed=None if truncated or exit_code is None else _frames(raw)
    if parsed is None: return "unknown","protocol_invalid","none",False
    stage,invoked=parsed
    if not invoked: return "observed","stage_failed",stage,False
    return ("observed","none","none",True) if exit_code==0 else ("observed","restic_read_failed","restic_read",True)

def _child(helper,bound,write_fd):
    current="mount_namespace"
    def emit(kind,stage):
        raw=(kind+":"+stage+"\n").encode("ascii")
        if os.write(write_fd,raw)!=len(raw): os._exit(125)
    def transition(stage):
        nonlocal current
        emit("D",current);current=stage;emit("B",current)
    try:
        null=os.open(os.devnull,os.O_WRONLY);os.dup2(null,1);os.dup2(null,2);os.close(null)
        emit("B",current)
        helper_os=helper["os"];native_fchown=helper_os.fchown;native_fchmod=helper_os.fchmod
        libc=helper["ctypes"].CDLL(None,use_errno=True)
        native_mount=libc.mount;native_unshare=libc.unshare
        mount_count=chown_count=chmod_count=0
        def unshare_call(flags):
            result=native_unshare(flags)
            if result==0: transition("private_propagation")
            return result
        def mount_call(*args):
            nonlocal mount_count
            mount_count+=1
            expected={1:"private_propagation",2:"private_view_setup",3:"source_bind",
                4:"source_readonly_remount",5:"repository_bind",6:"credential_self_bind",
                7:"credential_readonly_remount"}.get(mount_count)
            if expected is None: raise OSError()
            if current!=expected: transition(expected)
            result=native_mount(*args)
            if result==0 and mount_count in {1,2,5}:
                transition({1:"private_view_setup",2:"view_materialization",5:"credential_materialization"}[mount_count])
            return result
        def fchown_call(*args):
            nonlocal chown_count
            chown_count+=1
            if chown_count==1: transition("credential_directory_owner_mode")
            return native_fchown(*args)
        def fchmod_call(*args):
            nonlocal chmod_count
            chmod_count+=1
            result=native_fchmod(*args)
            if chmod_count==1: transition("source_repository_identity")
            return result
        helper_os.fchown=fchown_call;helper_os.fchmod=fchmod_call
        helper["_mount_setup"](bound,mount_call=mount_call,unshare_call=unshare_call)
        transition("mount_postflight");transition("close_source_parent")
        for descriptor,next_stage in ((bound.source_parent_fd,"close_source"),(bound.source_fd,"close_repository"),
                                      (bound.repository_fd,"close_credential"),(bound.credential_fd,"identity_drop")):
            helper["_close_verified"](descriptor);transition(next_stage)
        helper["_drop_identity"](bound);transition("repository_access_after_drop")
        helper["_prove_repository_access_after_drop"]();transition("execveat")
        native=helper["_execveat_syscall"]()
        if native is None: raise OSError()
        command=(helper["RESTIC_BINARY"],"-r",helper["VIEW_REPOSITORY"],"--no-lock","snapshots","--latest","1","--json")
        argv=(ctypes.c_char_p*(len(command)+1))(*[p.encode("ascii") for p in command],None)
        env=(ctypes.c_char_p*3)(b"PATH=/usr/bin:/bin",("RESTIC_PASSWORD_FILE="+helper["VIEW_CREDENTIAL"]).encode("ascii"),None)
        native[0](native[1],bound.restic_fd,ctypes.c_char_p(b""),argv,env,helper["EXECVEAT_EMPTY_PATH"])
        emit("F","exec_returned")
    except Exception:
        try: emit("F",current)
        except Exception: pass
    os._exit(125)

def _run_child(helper,bound,timeout=TIMEOUT_SECONDS):
    read_fd,write_fd=os.pipe2(os.O_CLOEXEC)
    try: pid=os.fork()
    except Exception:
        os.close(read_fd);os.close(write_fd);raise
    if pid==0:
        os.close(read_fd);_child(helper,bound,write_fd)
    os.close(write_fd)
    raw=bytearray();deadline=time.monotonic()+timeout;reaped=False;eof=False
    try:
        while True:
            if not reaped:
                done,status=os.waitpid(pid,os.WNOHANG)
                if done: reaped=True
            remaining=deadline-time.monotonic()
            if remaining<=0:
                return _interpret(None,bytes(raw),timed_out=True)
            if not eof:
                ready,_,_=select.select([read_fd],[],[],max(0,min(remaining,0.05)))
                if ready:
                    part=os.read(read_fd,min(256,MAX_PROTOCOL_BYTES+1-len(raw)))
                    if part: raw.extend(part)
                    else: eof=True
            elif not reaped:
                time.sleep(max(0,min(remaining,0.01)))
            if len(raw)>MAX_PROTOCOL_BYTES: return _interpret(None,b"",truncated=True)
            if reaped and eof:
                return _interpret(os.waitstatus_to_exitcode(status),bytes(raw),signaled=os.WIFSIGNALED(status))
    finally:
        if not reaped:
            try: os.kill(pid,9)
            except ProcessLookupError: pass
            os.waitpid(pid,0)
        os.close(read_fd)

def _run_read_only():
    helper=_load_helper()
    if helper is None or not _expected_context(): return envelope("blocked","preflight_failed")
    lock=_acquire_existing_lock(helper)
    if lock is None: return envelope("blocked","preflight_failed")
    bound=None
    try:
        if not _unit_quiescent() or not _incident_ready(helper): return envelope("blocked","preflight_failed")
        bound=helper["_bind_identities"]()
        status,code,stage,invoked=_run_child(helper,bound)
        return envelope(status,code,stage,ready=True,bound=True,invoked=invoked)
    except Exception: return envelope("blocked","preflight_failed")
    finally:
        if bound is not None:
            for fd in (bound.source_parent_fd,bound.source_fd,bound.repository_fd,bound.credential_fd,bound.restic_fd):
                try: os.close(fd)
                except Exception: pass
        os.close(lock)

def collect(*,execute=False,runner=_run_read_only):
    if execute is not True: return envelope("blocked","execution_disabled")
    try: value=runner()
    except Exception: return envelope("blocked","preflight_failed")
    return dict(value) if validate_envelope(value) else envelope("blocked","preflight_failed")

if __name__=="__main__":
    print(json.dumps(collect(),sort_keys=True,separators=(",",":")))
