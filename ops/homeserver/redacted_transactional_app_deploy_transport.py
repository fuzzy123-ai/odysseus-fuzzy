#!/usr/bin/env python3
"""Fixed SSH stdin transport; programmatic execution remains owner-packet gated."""
from __future__ import annotations
import base64, hashlib, json, shlex, subprocess, sys
from typing import Any, Callable, Mapping
from ops.homeserver import redacted_transactional_app_deploy as executor

SCHEMA_ID="odysseus.redacted_transactional_app_deploy_transport.v2"
PUBLISHED_REF="refs/remotes/fuzzy/dev"
EXECUTOR_PATH="ops/homeserver/redacted_transactional_app_deploy.py"
READBACK_PATH="ops/homeserver/redacted_transactional_app_deploy_readback.py"
PUBLISHED_EXECUTOR_SHA256="1f35c5fb0157606587865719c5ec8e4af17812923e128e1d666eb50dbcc92071"
PUBLISHED_READBACK_SHA256="45650dfc03f3763e2952d7609230765418e6e7705881da3c8aa1768b650e2a65"
_CODES=frozenset({"published_blob_unavailable","published_blob_mismatch","transport_timeout","transport_failed","transport_invalid","invalid_invocation"})
_KEYS=frozenset({"schema_id","status","error_code","retry_permitted","evidence_sha256"})
_BOOTSTRAP="""import base64,hashlib,json,sys,types
sys.path.insert(0,'/opt/odysseus')
raw=sys.stdin.buffer.read(700001)
if len(raw)>700000: raise SystemExit(2)
b=json.loads(raw.decode('utf-8'))
if type(b) is not dict or set(b)!={'packet','execute','executor','readback'} or type(b['execute']) is not bool: raise SystemExit(2)
EXPECTED={'executor':'1f35c5fb0157606587865719c5ec8e4af17812923e128e1d666eb50dbcc92071','readback':'45650dfc03f3763e2952d7609230765418e6e7705881da3c8aa1768b650e2a65'}
for n in ('readback','executor'):
 s=base64.b64decode(b[n]['source']); h=b[n]['sha256']
 if h!=EXPECTED[n] or hashlib.sha256(s).hexdigest()!=h: raise SystemExit(2)
 m=types.ModuleType('ops.homeserver.redacted_transactional_app_deploy'+('_readback' if n=='readback' else '')); m.__file__='<published>'; sys.modules[m.__name__]=m; exec(compile(s,m.__file__,'exec'),m.__dict__)
m=sys.modules['ops.homeserver.redacted_transactional_app_deploy']; p=m.production_entrypoint(b['packet'],execute=b['execute']); print(json.dumps(p,ensure_ascii=True,sort_keys=True,separators=(',',':')))"""
SSH_COMMAND=("ssh","-F","ops/homeserver/ssh_config","odysseus-homeserver","cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 1200s /usr/bin/python3 -I -c "+shlex.quote(_BOOTSTRAP))
def _digest(p:Mapping[str,Any])->str:return hashlib.sha256(json.dumps({k:v for k,v in p.items() if k!='evidence_sha256'},ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def blocked(code:str)->dict[str,Any]:
 p={"schema_id":SCHEMA_ID,"status":"blocked","error_code":code if code in _CODES else "transport_invalid","retry_permitted":False};p['evidence_sha256']=_digest(p);return p
def validate_envelope(p:Any)->bool:return type(p)is dict and set(p)==_KEYS and p.get('schema_id')==SCHEMA_ID and p.get('status')=='blocked' and p.get('error_code') in _CODES and p.get('retry_permitted')is False and p.get('evidence_sha256')==_digest(p)
def _out(r:Any)->bytes|None:
 v=getattr(r,'stdout',None);return v if type(v)is bytes else None
def _blob(path:str,expected:str,runner:Callable[...,Any])->bytes|None:
 try:r=runner(['git','cat-file','blob',f'{PUBLISHED_REF}:{path}'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=False,timeout=5,check=False,shell=False)
 except Exception:return None
 v=_out(r);return v if getattr(r,'returncode',None)==0 and v and len(v)<=300000 and hashlib.sha256(v).hexdigest()==expected else None
def invoke_bundle(bundle:Any,*,production_entrypoint:Callable[...,dict[str,Any]]=executor.production_entrypoint)->dict[str,Any]:
 """Pure equivalent of the fixed remote bootstrap, retained for adversarial tests."""
 try:
  if type(bundle)is not dict or set(bundle)!={'packet','execute','executor','readback'} or bundle['execute'] is not True or executor.DeployPacket.from_mapping(bundle['packet']) is None:raise ValueError
  for key,expected in (('executor',PUBLISHED_EXECUTOR_SHA256),('readback',PUBLISHED_READBACK_SHA256)):
   item=bundle[key]
   if type(item)is not dict or set(item)!={'sha256','source'} or item['sha256']!=expected or hashlib.sha256(base64.b64decode(item['source'],validate=True)).hexdigest()!=expected:raise ValueError
  result=production_entrypoint(bundle['packet'],execute=True)
  return result if executor.validate_envelope(result) else blocked('transport_invalid')
 except Exception:return blocked('transport_invalid')
def collect_published_transactional_app_deploy(packet:Any=None,*,execute:bool=False,runner:Callable[...,Any]=subprocess.run)->dict[str,Any]:
 parsed=executor.DeployPacket.from_mapping(packet)
 if not execute:return blocked('invalid_invocation')
 if parsed is None:return blocked('invalid_invocation')
 source=_blob(EXECUTOR_PATH,PUBLISHED_EXECUTOR_SHA256,runner); readback=_blob(READBACK_PATH,PUBLISHED_READBACK_SHA256,runner)
 if source is None or readback is None:return blocked('published_blob_mismatch')
 bundle={"packet":packet,"execute":True,"executor":{"sha256":PUBLISHED_EXECUTOR_SHA256,"source":base64.b64encode(source).decode()},"readback":{"sha256":PUBLISHED_READBACK_SHA256,"source":base64.b64encode(readback).decode()}}
 try:r=runner(list(SSH_COMMAND),input=json.dumps(bundle,separators=(',',':')).encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=False,timeout=1230,check=False,shell=False)
 except subprocess.TimeoutExpired:return blocked('transport_timeout')
 except Exception:return blocked('transport_failed')
 raw=_out(r)
 try:
  if raw is None or len(raw)>8192 or raw.count(b'\n')!=1 or not raw.endswith(b'\n'):raise ValueError
  p=json.loads(raw.decode())
 except Exception:return blocked('transport_invalid')
 if not executor.validate_envelope(p) or getattr(r,'returncode',None) not in {0,1}:return blocked('transport_invalid')
 return {k:p[k] for k in sorted(p)}
def main(argv:list[str]|None=None)->int:
 p=blocked('invalid_invocation');print(json.dumps(p,ensure_ascii=True,sort_keys=True,separators=(',',':')));return 1
if __name__=='__main__':raise SystemExit(main())
