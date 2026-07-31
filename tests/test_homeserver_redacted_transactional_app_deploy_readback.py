from __future__ import annotations
from ops.homeserver import redacted_transactional_app_deploy_readback as r
E=r.ReadbackExpectation('a'*40,'b'*64)
def test_concrete_readback_checks_all_fixed_proofs_and_never_exposes_raw():
 def run(c,**kw):
  cmd=tuple(c)
  if cmd[:2]==('git','-C'):out='a'*40+'\n'
  elif cmd[:3]==('podman','exec',r.APP_CONTAINER):out='disabled\n' if cmd[-1]==r._DELIVERY_PROGRAM else 'ok\n'
  elif cmd[:3]==('podman','inspect','--format'):out=('c'*64 if cmd[-1]==r.CHROMA_CONTAINER else 'd'*64)+' true\n' if cmd[3]=='{{.Id}} {{.State.Running}}' else 'healthy\n'
  else:out=''
  return type('X',(),{'stdout':out,'returncode':0})()
 baseline=r.capture_dependency_baseline(runner=run);p=r.collect_host_readback(E,baseline,runner=run);assert r.validate_envelope(p) and p['status']=='ok'
def test_drift_delivery_or_manifest_failure_is_observed_not_ok():
 for bad in ('enabled\n','bad\n'):
  def run(c,**kw):
   cmd=tuple(c);out='a'*40+'\n' if cmd[:2]==('git','-C') else bad if cmd[:3]==('podman','exec',r.APP_CONTAINER) else ('c'*64+' true\n' if cmd[-1]==r.CHROMA_CONTAINER else 'd'*64+' true\n') if cmd[:3]==('podman','inspect','--format') and cmd[3]=='{{.Id}} {{.State.Running}}' else 'healthy\n'
   return type('X',(),{'stdout':out,'returncode':0})()
  p=r.collect_host_readback(E,r.capture_dependency_baseline(runner=run),runner=run);assert p['status']=='observed'
def test_frozen_runtime_baseline_requires_exact_old_identity_and_mounts():
 def run(c,**kw):
  cmd=tuple(c)
  if cmd[:2]==('git','-C'):out='a'*40+'\n'
  elif cmd[:3]==('podman','exec',r.APP_CONTAINER):out='b'*64+'\n'
  elif cmd[:3]==('podman','inspect','--format') and cmd[3]=='{{.State.Running}}':out='true\n'
  elif cmd[:3]==('podman','inspect','--format') and cmd[3]=='{{.Image}}':out='sha256:'+'d'*64+'\n'
  elif cmd[:3]==('podman','inspect','--format') and cmd[3]=='{{range .Mounts}}{{.Source}}:{{.Destination}};{{end}}':out='/opt/odysseus/data:/app/data;/opt/odysseus/logs:/app/logs;/opt/odysseus/data/universal-inbox:/app/universal-inbox;\n'
  elif cmd[:3]==('podman','inspect','--format'):out='c'*64+' true\n'
  else:out=''
  return type('X',(),{'stdout':out,'returncode':0})()
 baseline=r.capture_runtime_baseline(runner=run);assert baseline and baseline.revision=='a'*40 and baseline.manifest_sha256=='b'*64 and baseline.expected_mounts
def test_ready_uses_twenty_attempt_bounded_cadence_without_real_sleep():
 delays=[];calls=[]
 def late(*_args,**_kwargs):calls.append(1);return type('X',(),{'stdout':'','returncode':0 if len(calls)==4 else 1})()
 assert r._ready(late,'http://fixed',sleeper=delays.append) and delays==[2,2,2]
 delays.clear();assert not r._ready(lambda *_a,**_k:type('X',(),{'stdout':'','returncode':1})(),'http://fixed',sleeper=delays.append) and len(delays)==19
def test_isolated_container_programs_prefix_fixed_app_root_before_src_imports():
 assert all("sys.path.insert(0,'/app')" in program for program in (r._MANIFEST_PROGRAM,r._VERSION_PROGRAM,r._BASELINE_PROGRAM))
def test_baseline_rejects_each_missing_or_wrong_mount_and_not_running():
 required=['/opt/odysseus/data:/app/data','/opt/odysseus/logs:/app/logs','/opt/odysseus/data/universal-inbox:/app/universal-inbox']
 for broken in [None,*range(3)]:
  def run(c,**kw):
   cmd=tuple(c); mounts=';'.join(x for i,x in enumerate(required) if i!=broken) if isinstance(broken,int) else ';'.join(required)
   if cmd[:2]==('git','-C'):out='a'*40+'\n'
   elif cmd[:3]==('podman','inspect','--format') and cmd[3]=='{{.State.Running}}':out='false\n' if broken is None else 'true\n'
   elif cmd[:3]==('podman','inspect','--format') and cmd[3]=='{{.Image}}':out='sha256:'+'d'*64+'\n'
   elif cmd[:3]==('podman','inspect','--format') and 'Mounts' in cmd[3]:out=mounts+'\n'
   elif cmd[:3]==('podman','inspect','--format'):out='c'*64+' true\n'
   elif cmd[:3]==('podman','exec',r.APP_CONTAINER):out='b'*64+'\n'
   else:out=''
   return type('X',(),{'stdout':out,'returncode':0})()
  assert r.capture_runtime_baseline(runner=run) is None
