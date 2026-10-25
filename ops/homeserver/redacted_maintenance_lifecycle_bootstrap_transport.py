#!/usr/bin/env python3
"""Bounded SSH transport for the frozen root-only lifecycle bootstrap."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
import os
import selectors
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping

SCHEMA_ID = "odysseus.redacted_maintenance_lifecycle_bootstrap_transport.v1"
_BLOCKED_CODES = frozenset({"execution_disabled", "invalid_invocation", "preconditions_not_met"})
_UNKNOWN_CODES = frozenset({"pre_readback_invalid", "manual_recovery_required", "post_readback_invalid", "post_state_not_verified", "transport_internal_failure", "transport_timeout", "transport_failed", "transport_invalid"})
_KEYS = frozenset({"schema_id", "status", "error_code", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "secret_values_visible", "evidence_sha256"})


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _envelope(status: str, code: str) -> dict[str, Any]:
    unknown = status == "unknown"
    value = {
        "schema_id": SCHEMA_ID,
        "status": status,
        "error_code": code,
        "effect_may_have_occurred": unknown,
        "manual_recovery_required": unknown,
        "retry_permitted": False,
        "raw_output_visible": False,
        "secret_values_visible": False,
    }
    value["evidence_sha256"] = _digest(value)
    return value


def blocked(code: str) -> dict[str, Any]:
    safe = code if type(code) is str and code in _BLOCKED_CODES else "invalid_invocation"
    return _envelope("blocked", safe)


def unknown(code: str) -> dict[str, Any]:
    safe = code if type(code) is str and code in _UNKNOWN_CODES else "transport_invalid"
    return _envelope("unknown", safe)


def validate_envelope(value: Any) -> bool:
    if type(value) is not dict or set(value) != _KEYS:
        return False
    if value.get("schema_id") != SCHEMA_ID:
        return False
    status = value.get("status")
    code = value.get("error_code")
    if type(status) is not str or type(code) is not str:
        return False
    for key in ("effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "secret_values_visible"):
        if type(value.get(key)) is not bool:
            return False
    digest = value.get("evidence_sha256")
    if type(digest) is not str or digest != _digest(value):
        return False
    if status == "blocked":
        return code in _BLOCKED_CODES and value["effect_may_have_occurred"] is False and value["manual_recovery_required"] is False and value["retry_permitted"] is False
    if status == "unknown":
        return code in _UNKNOWN_CODES and value["effect_may_have_occurred"] is True and value["manual_recovery_required"] is True and value["retry_permitted"] is False
    if status == "ready":
        return code == "none" and value["effect_may_have_occurred"] is False and value["manual_recovery_required"] is False and value["retry_permitted"] is False
    return False


def _render_remote_parent() -> str:
    return r'''import hashlib,json,re,subprocess,sys,time
SCHEMA='odysseus.redacted_maintenance_lifecycle_bootstrap_transport.v1'
READBACK='/usr/local/libexec/odysseus-maintenance-bootstrap/readback.py'
UNIT='odysseus-maintenance-lifecycle-bootstrap.service'
READ_SCHEMA='odysseus.redacted_maintenance_lifecycle_bootstrap_readback.v1'
READ_KEYS={'schema_id','status','error_code','manual_recovery_required','retry_permitted','units','asset_pins_valid','arm','consumed','receipt','state_validity','shared_metadata','temporary_absent','raw_output_visible','secret_values_visible','evidence_sha256'}
UNIT_KEYS={'odysseus-maintenance-lifecycle-bootstrap.service','odysseus-predeploy-backup-root-helper.service'}
def emit(status,code):
 value={'schema_id':SCHEMA,'status':status,'error_code':code,'effect_may_have_occurred':status=='unknown','manual_recovery_required':status=='unknown','retry_permitted':False,'raw_output_visible':False,'secret_values_visible':False}
 value['evidence_sha256']=hashlib.sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()
 sys.stdout.write(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'))+'\n')
def readback():
 try:
  result=subprocess.run(['/usr/bin/sudo','-n','/usr/bin/python3','-I',READBACK],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=False,timeout=8,check=False)
  raw=result.stdout
  if result.returncode!=0 or type(raw) is not bytes or len(raw)>8192 or raw.count(b'\n')!=1 or not raw.endswith(b'\n'): return None
  value=json.loads(raw.decode('ascii'))
  if type(value) is not dict or set(value)!=READ_KEYS or value.get('schema_id')!=READ_SCHEMA: return None
  if type(value.get('status')) is not str or value.get('status') not in {'armed','completed','manual_recovery_required','blocked'} or type(value.get('error_code')) is not str: return None
  digest=value.get('evidence_sha256')
  body={k:v for k,v in value.items() if k!='evidence_sha256'}
  if type(digest) is not str or hashlib.sha256(json.dumps(body,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()!=digest: return None
  if type(value.get('units')) is not dict or set(value['units'])!=UNIT_KEYS: return None
  if type(value.get('asset_pins_valid')) is not bool or type(value.get('temporary_absent')) is not bool: return None
  for key in ('manual_recovery_required','retry_permitted','raw_output_visible','secret_values_visible'):
   if type(value.get(key)) is not bool: return None
  if value['retry_permitted'] is not False or value['raw_output_visible'] or value['secret_values_visible']: return None
  if value['manual_recovery_required']!=(value['status']=='manual_recovery_required'): return None
  validity=value.get('state_validity')
  validity_keys={'arm_present','arm_valid','consumed_present','consumed_valid','receipt_present','receipt_valid'}
  if type(validity) is not dict or set(validity)!=validity_keys or any(type(v) is not bool for v in validity.values()): return None
  for name in ('arm','consumed','receipt'):
   if validity[name+'_present']!=(value.get(name) is not None) and validity[name+'_valid']: return None
   if validity[name+'_valid']!=(not validity[name+'_present'] or value.get(name) is not None): return None
  names={'lifecycle.py','bootstrap.py','supervisor.py','readback.py'}
  def hexid(item): return type(item) is str and re.fullmatch('[0-9a-f]{64}',item) is not None
  active_states={'active','reloading','inactive','failed','activating','deactivating'}
  sub_states={'dead','running','exited','failed','start-pre','start','start-post','stop','stop-sigterm','stop-sigkill','stop-post','final-sigterm','final-sigkill','auto-restart','condition','reload','maintenance','refreshing'}
  for item in value['units'].values():
   if item is None: continue
   if type(item) is not dict or set(item)!={'loaded','active_state','sub_state','main_pid','control_pid','inactive_dead'} or item.get('loaded') is not True or type(item.get('active_state')) is not str or item.get('active_state') not in active_states or type(item.get('sub_state')) is not str or item.get('sub_state') not in sub_states or type(item.get('inactive_dead')) is not bool or type(item.get('main_pid')) is not int or not 0<=item['main_pid']<=2147483647 or type(item.get('control_pid')) is not int or not 0<=item['control_pid']<=2147483647: return None
   if item['inactive_dead']!=(item['active_state']=='inactive' and item['sub_state']=='dead' and item['main_pid']==0 and item['control_pid']==0): return None
  if value.get('error_code') not in {'ready_to_start','bootstrap_completed','unit_not_quiescent','state_mismatch_or_consumed','not_armed'}: return None
  metadata=value.get('shared_metadata')
  metadata_fields={'directory_uid','directory_gid','directory_mode','lock_uid','lock_gid','lock_mode','lock_nlink'}
  def valid_metadata(item, include_valid):
   expected=metadata_fields|({'valid'} if include_valid else set())
   if type(item) is not dict or set(item)!=expected or (include_valid and item.get('valid') is not True): return False
   limits={'directory_uid':2147483647,'directory_gid':2147483647,'directory_mode':511,'lock_uid':2147483647,'lock_gid':2147483647,'lock_mode':511,'lock_nlink':1}
   if any(type(item.get(k)) is not int or not 0<=item[k]<=v for k,v in limits.items()): return False
   return item['directory_uid']==item['lock_uid']==0 and item['directory_gid']>0 and item['directory_gid']==item['lock_gid'] and item['directory_mode']==488 and item['lock_mode']==432 and item['lock_nlink']==1
  for key, schema, allowed in (('arm','odysseus.redacted_maintenance_lifecycle_bootstrap_arm.v1',None),('consumed','odysseus.redacted_maintenance_lifecycle_bootstrap_arm.v1',None)):
   item=value.get(key)
   if item is not None and (type(item) is not dict or set(item)!={'schema_id','grant_id','invocation_id','expires_at','asset_pins'} or item.get('schema_id')!=schema or not hexid(item.get('grant_id')) or not hexid(item.get('invocation_id')) or type(item.get('expires_at')) is not int or not 0<=item['expires_at']<=4102444800 or type(item.get('asset_pins')) is not dict or set(item['asset_pins'])!=names or any(not hexid(x) for x in item['asset_pins'].values())): return None
  if metadata is not None and metadata!={'valid':False} and not valid_metadata(metadata,True): return None
  receipt=value.get('receipt')
  if receipt is not None:
   if type(receipt) is not dict or set(receipt)!={'schema_id','grant_id','invocation_id','asset_pins','outcome','error_code','shared_metadata','recorded_at','receipt_sha256'} or receipt.get('schema_id')!='odysseus.redacted_maintenance_lifecycle_bootstrap_receipt.v1' or not hexid(receipt.get('grant_id')) or not hexid(receipt.get('invocation_id')) or type(receipt.get('asset_pins')) is not dict or set(receipt['asset_pins'])!=names or any(not hexid(x) for x in receipt['asset_pins'].values()) or receipt.get('outcome') not in {'ready','blocked','unknown'} or receipt.get('error_code') not in {'none','execution_disabled','identity_unavailable','lock_busy','unit_not_quiescent','unsafe_existing_object','publication_failed_before_exposure','publication_ambiguous','postflight_not_quiescent'} or type(receipt.get('recorded_at')) is not int or not 0<=receipt['recorded_at']<=4102444800: return None
   if receipt['outcome']=='ready':
    if receipt['error_code']!='none' or not valid_metadata(receipt.get('shared_metadata'),False): return None
   elif receipt.get('shared_metadata') is not None: return None
  pins=value.get('arm',{}).get('asset_pins') if type(value.get('arm')) is dict else value.get('consumed',{}).get('asset_pins') if type(value.get('consumed')) is dict else value.get('receipt',{}).get('asset_pins') if type(value.get('receipt')) is dict else None
  if type(pins) is not dict or set(pins)!=names or any(not hexid(x) for x in pins.values()): return None
  if value.get('status')=='armed':
   arm=value.get('arm')
   if type(arm) is not dict or set(arm)!={'schema_id','grant_id','invocation_id','expires_at','asset_pins'} or arm.get('schema_id')!='odysseus.redacted_maintenance_lifecycle_bootstrap_arm.v1' or not hexid(arm.get('grant_id')) or not hexid(arm.get('invocation_id')) or type(arm.get('expires_at')) is not int or not int(time.time())<=arm['expires_at']<=int(time.time())+600 or arm['asset_pins']!=pins or value.get('consumed') is not None or value.get('receipt') is not None or value.get('shared_metadata') is not None or validity!={'arm_present':True,'arm_valid':True,'consumed_present':False,'consumed_valid':True,'receipt_present':False,'receipt_valid':True}: return None
  elif value.get('status')=='completed':
   consumed=value.get('consumed'); receipt=value.get('receipt'); meta=value.get('shared_metadata')
   if value.get('arm') is not None or type(consumed) is not dict or set(consumed)!={'schema_id','grant_id','invocation_id','expires_at','asset_pins'} or consumed.get('schema_id')!='odysseus.redacted_maintenance_lifecycle_bootstrap_arm.v1' or not hexid(consumed.get('grant_id')) or not hexid(consumed.get('invocation_id')) or type(consumed.get('expires_at')) is not int or not 0<=consumed['expires_at']<=4102444800 or consumed.get('asset_pins')!=pins or validity!={'arm_present':False,'arm_valid':True,'consumed_present':True,'consumed_valid':True,'receipt_present':True,'receipt_valid':True}: return None
   if type(receipt) is not dict or set(receipt)!={'schema_id','grant_id','invocation_id','asset_pins','outcome','error_code','shared_metadata','recorded_at','receipt_sha256'} or receipt.get('schema_id')!='odysseus.redacted_maintenance_lifecycle_bootstrap_receipt.v1' or receipt.get('grant_id')!=consumed.get('grant_id') or receipt.get('invocation_id')!=consumed.get('invocation_id') or receipt.get('asset_pins')!=pins or receipt.get('outcome')!='ready' or receipt.get('error_code')!='none' or type(receipt.get('recorded_at')) is not int: return None
   body={k:v for k,v in receipt.items() if k!='receipt_sha256'}
   if receipt.get('receipt_sha256')!=hashlib.sha256(json.dumps(body,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest(): return None
   if type(meta) is not dict or set(meta)!={'valid','directory_uid','directory_gid','directory_mode','lock_uid','lock_gid','lock_mode','lock_nlink'} or meta.get('valid') is not True or receipt.get('shared_metadata')!={k:v for k,v in meta.items() if k!='valid'}: return None
  else: return None
  return value
 except BaseException:
  return None
try:
 before=readback()
 if before is None: emit('unknown','pre_readback_invalid'); raise SystemExit(1)
 if before.get('status')!='armed' or before.get('error_code')!='ready_to_start' or before.get('manual_recovery_required') is not False or before.get('asset_pins_valid') is not True or before.get('temporary_absent') is not True or any(x.get('inactive_dead') is not True or x.get('main_pid')!=0 or x.get('control_pid')!=0 for x in before['units'].values()):
  if before.get('status')=='completed' and before.get('error_code')=='bootstrap_completed': emit('ready','none')
  elif before.get('manual_recovery_required') is True: emit('unknown','manual_recovery_required')
  else: emit('blocked','preconditions_not_met')
  raise SystemExit(0 if before.get('status')=='completed' else 1)
 try:
  subprocess.run(['/usr/bin/sudo','-n','/usr/bin/systemctl','start','--wait',UNIT],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,shell=False,timeout=38,check=False)
 except BaseException:
  pass
 after=readback()
 if after is None: emit('unknown','post_readback_invalid'); raise SystemExit(1)
 if after.get('status')=='completed' and after.get('error_code')=='bootstrap_completed' and after.get('manual_recovery_required') is False and after.get('asset_pins_valid') is True and after.get('temporary_absent') is True and all(x.get('inactive_dead') is True and x.get('main_pid')==0 and x.get('control_pid')==0 for x in after['units'].values()):
  emit('ready','none'); raise SystemExit(0)
 emit('unknown','post_state_not_verified'); raise SystemExit(1)
except SystemExit:
 raise
except BaseException:
 emit('unknown','transport_internal_failure'); raise SystemExit(1)
'''


_REMOTE_PARENT = _render_remote_parent()
SSH_COMMAND = (
    "ssh",
    "-F",
    "ops/homeserver/ssh_config",
    "odysseus-homeserver",
    "cd /opt/odysseus && exec /usr/bin/timeout --signal=TERM --kill-after=5s 45s /usr/bin/python3 -I -c " + shlex.quote(_REMOTE_PARENT),
)


def _out(result: Any) -> bytes | None:
    value = getattr(result, "stdout", None)
    return value if type(value) is bytes else None


def collect_published_lifecycle_bootstrap(*, execute: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if execute is not True:
        return blocked("invalid_invocation")
    try:
        if runner is subprocess.run:
            result = _run_local_process_group(SSH_COMMAND, input_bytes=b"", timeout=50)
        else:
            result = runner(
                list(SSH_COMMAND),
                input=b"",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,
                timeout=50,
                check=False,
                shell=False,
            )
    except subprocess.TimeoutExpired:
        return unknown("transport_timeout")
    except Exception:
        return unknown("transport_failed")
    if getattr(result, "timed_out", False) is True:
        return unknown("transport_timeout")
    raw = _out(result)
    try:
        if raw is None or len(raw) > 8192 or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
            raise ValueError
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return unknown("transport_invalid")
    if not validate_envelope(payload) or type(getattr(result, "returncode", None)) is not int or result.returncode not in {0, 1}:
        return unknown("transport_invalid")
    return {key: payload[key] for key in sorted(payload)}


_WINDOWS_JOB_LAUNCHER = r'''import base64,json,subprocess,sys
raw=sys.stdin.buffer.read(1700001)
if len(raw)>1700000: raise SystemExit(2)
try:
 packet=json.loads(raw.decode('ascii'))
 if type(packet) is not dict or set(packet)!={'command','input'}: raise ValueError
 command=packet['command']; encoded=packet['input']
 if type(command) is not list or not command or any(type(part) is not str or '\x00' in part for part in command): raise ValueError
 if type(encoded) is not str: raise ValueError
 data=base64.b64decode(encoded,validate=True)
 child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=None,stderr=subprocess.DEVNULL,shell=False,close_fds=True)
 try:
  child.stdin.write(data); child.stdin.close()
 except BaseException:
  child.kill(); child.wait(); raise
 raise SystemExit(child.wait())
except SystemExit:
 raise
except BaseException:
 raise SystemExit(2)
'''


class _WindowsKillOnCloseJob:
    """A native job owns the blocked launcher and all children it later starts."""

    def __init__(self) -> None:
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class BasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        self._extended_type = ExtendedLimitInformation
        self._accounting_type = BasicAccountingInformation
        kernel32 = self._kernel32
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise RuntimeError("Windows job creation failed")
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise RuntimeError("Windows job configuration failed")

    def assign(self, process_handle: int) -> None:
        if not self._kernel32.AssignProcessToJobObject(self._handle, self._wintypes.HANDLE(process_handle)):
            raise RuntimeError("Windows job assignment failed")

    def terminate_and_verify(self, *, timeout: float = 2.0) -> None:
        if not self._kernel32.TerminateJobObject(self._handle, 1):
            raise RuntimeError("Windows job termination failed")
        deadline = time.monotonic() + timeout
        while True:
            accounting = self._accounting_type()
            if not self._kernel32.QueryInformationJobObject(
                self._handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
            ):
                raise RuntimeError("Windows job status query failed")
            if accounting.ActiveProcesses == 0:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Windows job cleanup was not verified")
            time.sleep(0.01)

    def close(self) -> None:
        handle = self._handle
        if not handle:
            return
        self._handle = None
        if not self._kernel32.CloseHandle(handle):
            raise RuntimeError("Windows job handle close failed")


def _run_windows_job_process_group(
    command: tuple[str, ...], *, input_bytes: bytes, timeout: int, output_limit: int = 8192
) -> subprocess.CompletedProcess[bytes]:
    """Run under a kill-on-close job after a blocked launcher is assigned."""
    if not command or any(type(part) is not str or "\x00" in part for part in command):
        raise ValueError("invalid bounded child command")
    if type(input_bytes) is not bytes or len(input_bytes) > 1_200_000:
        raise ValueError("bounded child input exceeded limit")
    if (
        type(timeout) not in (int, float)
        or timeout <= 0
        or timeout > 1230
        or not math.isfinite(float(timeout))
    ):
        raise ValueError("invalid bounded child timeout")
    started_at = time.monotonic()
    deadline = started_at + float(timeout)
    packet = json.dumps(
        {"command": list(command), "input": base64.b64encode(input_bytes).decode("ascii")},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    if len(packet) > 1_700_000:
        raise ValueError("bounded launcher packet exceeded limit")

    job: _WindowsKillOnCloseJob | None = None
    process: subprocess.Popen[bytes] | None = None
    assigned = False
    reader: threading.Thread | None = None
    writer: threading.Thread | None = None
    captured = bytearray()
    capture_lock = threading.Lock()
    output_exceeded = threading.Event()
    reader_done = threading.Event()
    writer_done = threading.Event()
    thread_errors: list[str] = []
    timed_out = False
    failure = False
    cleanup_verified = True

    def drain_stdout() -> None:
        try:
            assert process is not None and process.stdout is not None
            while True:
                chunk = process.stdout.read1(4096)
                if not chunk:
                    break
                with capture_lock:
                    available = output_limit + 1 - len(captured)
                    if available > 0:
                        captured.extend(chunk[:available])
                    if len(captured) > output_limit:
                        output_exceeded.set()
        except BaseException:
            thread_errors.append("reader")
        finally:
            reader_done.set()

    def write_packet() -> None:
        try:
            assert process is not None and process.stdin is not None
            process.stdin.write(packet)
            process.stdin.close()
        except BaseException:
            thread_errors.append("writer")
        finally:
            writer_done.set()

    try:
        job = _WindowsKillOnCloseJob()
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _WINDOWS_JOB_LAUNCHER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
        try:
            assert process._handle is not None
            job.assign(int(process._handle))
            assigned = True
        except BaseException:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            raise RuntimeError("Windows launcher ownership failed") from None

        reader = threading.Thread(target=drain_stdout, name="lifecycle-stdout-reader", daemon=True)
        writer = threading.Thread(target=write_packet, name="lifecycle-launcher-writer", daemon=True)
        reader.start()
        writer.start()

        while True:
            if output_exceeded.is_set() or thread_errors:
                failure = True
                break
            if process.poll() is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            time.sleep(min(0.01, remaining))
    except BaseException:
        failure = True
    finally:
        if job is not None and assigned:
            try:
                job.terminate_and_verify(timeout=2.0)
            except BaseException:
                cleanup_verified = False
        elif process is not None:
            try:
                process.kill()
            except BaseException:
                cleanup_verified = False

        if process is not None:
            try:
                process.wait(timeout=2.0)
            except BaseException:
                cleanup_verified = False
                try:
                    process.kill()
                    process.wait(timeout=2.0)
                except BaseException:
                    cleanup_verified = False

        if job is not None:
            try:
                job.close()
            except BaseException:
                cleanup_verified = False

        for thread in (writer, reader):
            if thread is not None:
                thread.join(timeout=2.0)
                if thread.is_alive():
                    cleanup_verified = False

        if process is not None:
            live_io_threads = {
                "stdin": writer is not None and writer.is_alive(),
                "stdout": reader is not None and reader.is_alive(),
            }
            for stream_name in ("stdin", "stdout", "stderr"):
                if live_io_threads.get(stream_name, False):
                    # Closing a buffered stream while its I/O thread owns its
                    # lock can block forever. Leave it to process teardown.
                    cleanup_verified = False
                    continue
                stream = getattr(process, stream_name, None)
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException:
                        cleanup_verified = False

    # The stdout reader can observe buffered bytes only after the launcher has
    # exited. Its join above is therefore part of the output-limit decision.
    if output_exceeded.is_set():
        failure = True
    if not cleanup_verified:
        raise RuntimeError("Windows child cleanup was not verified")
    if output_exceeded.is_set():
        raise ValueError("transport child output exceeded fixed bound")
    if failure or thread_errors:
        raise RuntimeError("Windows child execution failed")
    if process is None or process.returncode is None:
        raise RuntimeError("Windows child result was not reaped")
    with capture_lock:
        output = bytes(captured[:output_limit])
    result = subprocess.CompletedProcess(command, process.returncode, output, None)
    result.timed_out = timed_out
    return result


def _run_local_process_group(command: tuple[str, ...], *, input_bytes: bytes, timeout: int) -> subprocess.CompletedProcess[bytes]:
    if os.name == "nt":
        return _run_windows_job_process_group(command, input_bytes=input_bytes, timeout=timeout)
    if os.name != "posix":
        raise RuntimeError("bounded process groups are unavailable")
    if type(input_bytes) is not bytes or len(input_bytes) > 1_700_000:
        raise ValueError("transport child input exceeded fixed bound")
    try:
        finite_timeout = math.isfinite(float(timeout))
    except (OverflowError, TypeError, ValueError):
        finite_timeout = False
    if type(timeout) not in (int, float) or not finite_timeout or timeout <= 0 or timeout > 1230:
        raise ValueError("invalid bounded child timeout")

    started = time.monotonic()
    deadline = started + float(timeout)
    cleanup_deadline = deadline + 2.0
    process = None
    selector = None
    captured = bytearray()
    timed_out = False
    output_too_large = False
    input_closed_early = False
    stdout_eof = False
    input_offset = 0
    cleanup_verified = True

    def signal_group(sig: int) -> None:
        assert process is not None
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def group_exists() -> bool:
        assert process is not None
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def terminate_group() -> bool:
        """Terminate residual same-session children and confirm the group is gone."""
        assert process is not None
        end = min(cleanup_deadline, time.monotonic() + 2.0)
        signal_group(signal.SIGTERM)
        term_end = min(end, time.monotonic() + 0.1)
        try:
            process.wait(timeout=max(0.0, term_end - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        while time.monotonic() < term_end and group_exists():
            time.sleep(0.01)
        if group_exists():
            signal_group(signal.SIGKILL)
        try:
            process.wait(timeout=max(0.0, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            return False
        while time.monotonic() < end:
            if not group_exists():
                return True
            time.sleep(0.01)
        return not group_exists()

    def drain_available() -> None:
        nonlocal stdout_eof, output_too_large
        assert process is not None and process.stdout is not None and selector is not None
        while not stdout_eof:
            try:
                chunk = os.read(process.stdout.fileno(), 4096)
            except BlockingIOError:
                return
            if not chunk:
                stdout_eof = True
                try:
                    selector.unregister(process.stdout)
                except KeyError:
                    pass
                return
            remaining = 8193 - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
            if len(captured) > 8192:
                output_too_large = True
                return

    try:
        if time.monotonic() >= deadline:
            raise TimeoutError("transport deadline expired before process start")
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
        assert process.stdin is not None and process.stdout is not None
        selector = selectors.DefaultSelector()
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        if input_bytes:
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()

        while True:
            now = time.monotonic()
            if now >= deadline:
                timed_out = process.poll() is None or not stdout_eof or input_offset < len(input_bytes)
                break
            returncode = process.poll()
            if returncode is not None:
                if input_offset < len(input_bytes):
                    input_closed_early = True
                if stdout_eof or not selector.get_map() or not any(key.data == "stdout" for key in selector.get_map().values()):
                    break
                # Drain bytes already written by the leader before cleaning up
                # descendants that may still hold the pipe open.
                events = selector.select(0)
                if not events:
                    break
            else:
                remaining = deadline - now
                events = selector.select(min(remaining, 0.05)) if selector.get_map() else []
                if not selector.get_map() and not events:
                    time.sleep(min(0.01, remaining))
                    continue

            for key, _mask in events:
                if key.data == "stdout":
                    drain_available()
                    if output_too_large:
                        break
                    continue
                try:
                    count = os.write(process.stdin.fileno(), input_bytes[input_offset : input_offset + 65536])
                except BrokenPipeError:
                    input_closed_early = input_offset < len(input_bytes)
                    try:
                        selector.unregister(process.stdin)
                    except KeyError:
                        pass
                    process.stdin.close()
                    continue
                if count <= 0:
                    input_closed_early = True
                    try:
                        selector.unregister(process.stdin)
                    except KeyError:
                        pass
                    process.stdin.close()
                    continue
                input_offset += count
                if input_offset == len(input_bytes):
                    selector.unregister(process.stdin)
                    process.stdin.close()

            if output_too_large:
                break

        if process.poll() is not None and input_offset < len(input_bytes):
            input_closed_early = True
        if process.poll() is None and (timed_out or output_too_large or input_closed_early):
            signal_group(signal.SIGTERM)

        # A completed leader can leave same-session descendants behind even
        # when stdout is DEVNULL, closed, or already at EOF.
        cleanup_verified = terminate_group()
        remaining_cleanup = max(0.0, cleanup_deadline - time.monotonic())
        try:
            process.wait(timeout=remaining_cleanup)
        except subprocess.TimeoutExpired:
            cleanup_verified = False

        if not stdout_eof and process.stdout is not None and selector is not None:
            drain_deadline = min(cleanup_deadline, time.monotonic() + 0.25)
            while time.monotonic() < drain_deadline and not stdout_eof and not output_too_large:
                if selector.get_map():
                    events = selector.select(min(0.02, drain_deadline - time.monotonic()))
                    for key, _mask in events:
                        if key.data == "stdout":
                            drain_available()
                else:
                    break

        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if selector is not None:
            selector.close()
            selector = None

        # Once the fixed output bound is crossed, stop consuming the pipe on
        # purpose.  Group termination and leader reaping above still prove
        # cleanup; reaching EOF is not required for this rejected result.
        if not stdout_eof and not output_too_large:
            cleanup_verified = False
        if input_closed_early:
            cleanup_verified = cleanup_verified and process.returncode is not None
        if not cleanup_verified:
            raise RuntimeError("transport child cleanup was not verified")
        if output_too_large:
            raise ValueError("transport child output exceeded fixed bound")
        if input_closed_early:
            raise BrokenPipeError("transport child closed bounded input early")
        result = subprocess.CompletedProcess(command, process.returncode, bytes(captured), None)
        result.timed_out = timed_out
        return result
    except BaseException:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                pass
            try:
                process.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
            for stream_name in ("stdin", "stdout", "stderr"):
                stream = getattr(process, stream_name, None)
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
        raise
    finally:
        if selector is not None:
            selector.close()


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    payload = blocked("invalid_invocation") if not arguments else blocked("invalid_invocation")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
