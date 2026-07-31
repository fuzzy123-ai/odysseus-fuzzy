from __future__ import annotations
import base64,hashlib,json,subprocess,sys
from pathlib import Path
from ops.homeserver import redacted_transactional_app_deploy as e
from ops.homeserver import redacted_transactional_app_deploy_transport as t
def packet():return {'schema_id':e.PACKET_SCHEMA_ID,'old_revision':'a'*40,'new_revision':'b'*40,'snapshot_evidence_sha256':'c'*64,'snapshot_id':'f'*64,'manifest_sha256':'d'*64,'old_manifest_sha256':'e'*64,'delivery_disabled':True}
def test_transport_never_executes_without_explicit_valid_packet_and_cli_is_disabled(monkeypatch,capsys):
 assert t.collect_published_transactional_app_deploy() ['status']=='blocked';assert t.main(['--execute'])==1;assert 'execute' not in capsys.readouterr().out
def test_transport_builds_fixed_stdin_bundle_only_for_explicit_packet(monkeypatch):
 source=b'x'; monkeypatch.setattr(t,'PUBLISHED_EXECUTOR_SHA256',hashlib.sha256(source).hexdigest());monkeypatch.setattr(t,'PUBLISHED_READBACK_SHA256',hashlib.sha256(source).hexdigest());calls=[]
 def run(c,**kw):
  calls.append((tuple(c),kw))
  if c[:3]==['git','cat-file','blob']:return type('R',(),{'stdout':source,'returncode':0})()
  p=e._envelope('blocked','preflight','failed',False);return type('R',(),{'stdout':json.dumps(p,separators=(',',':')).encode()+b'\n','returncode':1})()
 p=t.collect_published_transactional_app_deploy(packet(),execute=True,runner=run);assert e.validate_envelope(p) and len(calls)==3 and calls[-1][0]==t.SSH_COMMAND and b'"execute":true' in calls[-1][1]['input']
def test_validated_bundle_alone_reaches_the_production_entrypoint(monkeypatch):
 source=b'x';digest=hashlib.sha256(source).hexdigest();monkeypatch.setattr(t,'PUBLISHED_EXECUTOR_SHA256',digest);monkeypatch.setattr(t,'PUBLISHED_READBACK_SHA256',digest);called=[]
 bundle={'packet':packet(),'execute':True,'executor':{'sha256':digest,'source':__import__('base64').b64encode(source).decode()},'readback':{'sha256':digest,'source':__import__('base64').b64encode(source).decode()}}
 result=t.invoke_bundle(bundle,production_entrypoint=lambda p,execute: called.append((p,execute)) or e._envelope('blocked','preflight','failed',False));assert called and e.validate_envelope(result)
 bundle['execute']=False;assert t.invoke_bundle(bundle,production_entrypoint=lambda **_:(_ for _ in ()).throw(AssertionError()))['status']=='blocked'
def test_literal_bootstrap_loads_actual_pinned_sources_on_safe_execute_false_path():
 root=Path.cwd(); source=(root/t.EXECUTOR_PATH).read_bytes(); readback=(root/t.READBACK_PATH).read_bytes()
 assert hashlib.sha256(source).hexdigest()==t.PUBLISHED_EXECUTOR_SHA256 and hashlib.sha256(readback).hexdigest()==t.PUBLISHED_READBACK_SHA256
 bundle={'packet':packet(),'execute':False,'executor':{'sha256':t.PUBLISHED_EXECUTOR_SHA256,'source':base64.b64encode(source).decode()},'readback':{'sha256':t.PUBLISHED_READBACK_SHA256,'source':base64.b64encode(readback).decode()}}
 bootstrap=t._BOOTSTRAP.replace('/opt/odysseus',str(root).replace('\\','/'))
 result=subprocess.run([sys.executable,'-I','-c',bootstrap],input=json.dumps(bundle).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
 payload=json.loads(result.stdout);assert result.returncode==0 and result.stderr==b'' and e.validate_envelope(payload) and payload['status']=='not_executed'
 bundle['executor']['sha256']='0'*64;bad=subprocess.run([sys.executable,'-I','-c',bootstrap],input=json.dumps(bundle).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False);assert bad.returncode!=0 and bad.stdout==b''
