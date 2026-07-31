from __future__ import annotations
import hashlib,json
from ops.homeserver import redacted_transactional_app_deploy as d
OLD,NEW='a'*40,'b'*40
def packet():return {'schema_id':d.PACKET_SCHEMA_ID,'old_revision':OLD,'new_revision':NEW,'snapshot_evidence_sha256':'c'*64,'snapshot_id':'f'*64,'manifest_sha256':'d'*64,'old_manifest_sha256':'e'*64,'delivery_disabled':True}
class R:
 def __init__(self,out='',code=0):self.stdout,self.returncode=out,code
class L:
 def __enter__(self):return self
 def __exit__(self,*x):pass
def runner(calls,fail=()):
 state={'merged':False}
 def run(cmd,**kw):
  cmd=tuple(cmd);calls.append((cmd,kw))
  if cmd in fail:return R(code=1)
  if 'merge' in cmd:state['merged']=True
  if cmd==('git','-C',d.TARGET_ROOT,'remote','get-url','fuzzy'):return R('https://github.com/fuzzy123-ai/odysseus-fuzzy.git\n')
  if cmd[-2:]==('rev-parse','HEAD'):return R((NEW if state['merged'] else OLD)+'\n')
  if cmd[-2:]==('rev-parse','fuzzy/dev'):return R(NEW+'\n')
  if cmd[0]=='sha256sum':return R('d'*64+'  x\n')
  if cmd[:4]==('podman','image','inspect','--format'):return R('sha256:'+'e'*64+'\n')
  return R()
 return run
def ex(calls,fail=(),readback=lambda p,r:r==NEW):return d.TransactionalAppDeployExecutor(runner=runner(calls,fail),readback=readback,snapshot_validator=lambda p:True,baseline_factory=lambda:type('B',(),{'image_id':'sha256:'+'e'*64})(),path_exists=lambda p:False,lock_factory=L)
def test_real_fixed_compose_env_manifest_and_app_only_commands():
 calls=[];p=ex(calls).run(packet(),execute=True);assert d.validate_envelope(p) and p['status']=='succeeded'
 commands=[x for x,_ in calls];assert ('/usr/bin/python3',d.RELEASE_WORKTREE+'/'+d.MANIFEST_GENERATOR_NAME,'--repo',d.RELEASE_WORKTREE,'--output',d.RELEASE_WORKTREE+'/runtime/release-manifest.json','--revision',NEW,'--ref','dev','--max-commits','100') in commands
 compose=[x for x in commands if x[:3]==d.COMPOSE_COMMAND];assert len(compose)==2
 assert all(d.PRODUCTION_ENV_FILE in x and x[-1]=='odysseus' for x in compose) and '--no-deps' in compose[-1] and '--no-build' in compose[-1]
 assert not any(any(v in x for v in ('pull','prune','down')) for x in commands)
def test_bad_source_url_is_blocked_before_fetch_and_never_emitted(monkeypatch):
 calls=[];e=ex(calls);monkeypatch.setattr(e,'_run',lambda cmd,**kw:'https://user:secret@github.com/fuzzy123-ai/odysseus-fuzzy.git\n' if 'remote' in cmd else '')
 p=e.run(packet(),execute=True);assert p['status']=='blocked' and 'secret' not in json.dumps(p)
 for value in ('http://github.com/fuzzy123-ai/odysseus-fuzzy.git','https://github.com:443/fuzzy123-ai/odysseus-fuzzy.git','https://github.com/fuzzy123-ai/other.git?x=1') :assert d._safe_fetch_url(value) is False
def test_ambiguous_switch_rolls_once_then_readbacks_old_when_rollback_succeeds():
 calls=[];prefix=(*d.COMPOSE_COMMAND,'--project-name',d.PROJECT,'--env-file',d.PRODUCTION_ENV_FILE,'-f',d.RELEASE_WORKTREE+'/'+d.COMPOSE_FILE);up=(*prefix,'up','-d','--no-deps','--no-build','--force-recreate','odysseus')
 p=ex(calls,fail=(up,),readback=lambda p,r:r==OLD).run(packet(),execute=True);assert p['status']=='rolled_back' and p['rollback_attempted'] is True
 assert len([x for x,_ in calls if x[:3]==d.COMPOSE_COMMAND and x[-1]=='odysseus' and 'up' in x])==2
def test_snapshot_envelope_mismatch_and_envelope_contradiction_are_rejected(monkeypatch):
 p=packet();bad={'schema_id':'x'};monkeypatch.setattr(d.snapshot_observer,'collect_backup_snapshot_observation',lambda:bad);assert d._validate_snapshot(d.DeployPacket.from_mapping(p)) is False
 x=d._envelope('succeeded','post_health','succeeded',False);x['rollback_attempted']=True;x['evidence_sha256']=d._digest(x);assert d.validate_envelope(x) is False
def test_snapshot_identity_is_stable_across_age_digest_but_rejects_identity_and_source_changes(monkeypatch):
 p=d.DeployPacket.from_mapping(packet())
 def observed(age=1,snapshot='f'*64,source='odysseus_protected_source_v1'):
  value={key:False for key in d.snapshot_observer._OK_KEYS};value.update(schema_id=d.snapshot_observer.SCHEMA_ID,status='ok',repository_identity='restic_homeserver_backup_v1',protected_source_identity=source,snapshot_id=snapshot,source_included=True,snapshot_age_seconds=age,snapshot_fresh=True);value['evidence_sha256']=d.snapshot_observer._digest(value);return value
 monkeypatch.setattr(d.snapshot_observer,'collect_backup_snapshot_observation',lambda:observed(9));assert d._validate_snapshot(p)
 monkeypatch.setattr(d.snapshot_observer,'collect_backup_snapshot_observation',lambda:observed(10,'0'*64));assert not d._validate_snapshot(p)
 monkeypatch.setattr(d.snapshot_observer,'collect_backup_snapshot_observation',lambda:observed(10,source='wrong'));assert not d._validate_snapshot(p)
def test_every_cli_envelope_is_schema_valid(capsys):
 assert d.main([])==1;assert d.validate_envelope(__import__('json').loads(capsys.readouterr().out))
 assert d.main(['x'])==1;assert d.validate_envelope(__import__('json').loads(capsys.readouterr().out))
def test_snapshot_stale_false_digest_and_missing_visibility_reject(monkeypatch):
 p=d.DeployPacket.from_mapping(packet());base={key:False for key in d.snapshot_observer._OK_KEYS};base.update(schema_id=d.snapshot_observer.SCHEMA_ID,status='ok',repository_identity='restic_homeserver_backup_v1',protected_source_identity='odysseus_protected_source_v1',snapshot_id='f'*64,source_included=True,snapshot_age_seconds=1,snapshot_fresh=True);base['evidence_sha256']=d.snapshot_observer._digest(base)
 for mutate in (lambda x:x.update(snapshot_fresh=False),lambda x:x.update(snapshot_age_seconds=d.snapshot_observer.MAX_SNAPSHOT_AGE_SECONDS+1),lambda x:x.update(evidence_sha256='0'*64),lambda x:x.pop('raw_stdout_visible')):
  value=dict(base);mutate(value);monkeypatch.setattr(d.snapshot_observer,'collect_backup_snapshot_observation',lambda value=value:value);assert not d._validate_snapshot(p)
def test_exception_after_switch_rolls_once_and_reads_old_before_rolled_back(monkeypatch):
 e=ex([]);monkeypatch.setattr(e,'_preflight',lambda p:(True,'sha256:'+'e'*64));monkeypatch.setattr(e,'_switch',lambda i,p:'switched');roll=[];monkeypatch.setattr(e,'_rollback',lambda p:roll.append(1) is None)
 e._readback=lambda p,r:r==OLD;monkeypatch.setattr(e,'_lock_factory',lambda:L());monkeypatch.setattr(e,'_run',lambda *a,**k:(_ for _ in ()).throw(RuntimeError()))
 result=e.run(packet(),execute=True);assert result['status']=='rolled_back' and roll==[1]
def test_post_merge_uncertainty_is_unknown_without_container_rollback(monkeypatch):
 e=ex([]);monkeypatch.setattr(e,'_preflight',lambda p:(True,'sha256:'+'e'*64));monkeypatch.setattr(e,'_switch',lambda i,p:'switched');e._readback=lambda p,r:r==NEW
 calls=[]
 def run(cmd,**kw):calls.append(cmd);return '' if 'merge' in cmd else None
 monkeypatch.setattr(e,'_run',run);monkeypatch.setattr(e,'_rollback',lambda p:(_ for _ in ()).throw(AssertionError('no rollback')))
 result=e.run(packet(),execute=True);assert result['status']=='unknown' and result['rollback_attempted'] is False
def test_merge_failure_head_old_rolls_back_once_and_reads_old(monkeypatch):
 e=ex([]);monkeypatch.setattr(e,'_preflight',lambda p:(True,'sha256:'+'e'*64));monkeypatch.setattr(e,'_switch',lambda i,p:'switched');e._readback=lambda p,r:r==NEW or r==OLD
 monkeypatch.setattr(e,'_run',lambda cmd,**kw: None if 'merge' in cmd else OLD+'\n' if cmd[-2:]==('rev-parse','HEAD') else '')
 calls=[];monkeypatch.setattr(e,'_rollback',lambda p:calls.append(p) is None)
 result=e.run(packet(),execute=True);assert result['status']=='rolled_back' and len(calls)==1
