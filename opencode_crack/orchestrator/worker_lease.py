"""D-354 worker lease: task/lease mapping for real child sessions (file-backed)."""
import json,time
from pathlib import Path
STATES=("running","done","failed","recoverable")
class WorkerLeases:
 def __init__(self,path): self._p=Path(path);self._p.parent.mkdir(parents=True,exist_ok=True)
 def _all(self):
  if not self._p.exists(): return {}
  return {e["task_id"]:e for e in (json.loads(x) for x in self._p.read_text(encoding="utf-8").splitlines() if x.strip())}
 def _save(self,es): self._p.write_text("\n".join(json.dumps(es[k],sort_keys=True) for k in sorted(es))+"\n",encoding="utf-8")
 def register(self,task_id,agent,parent_id,child_id):
  es=self._all()
  if task_id in es: return {"duplicate":True,"entry":es[task_id]}
  e={"task_id":task_id,"agent":agent,"parent_id":parent_id,"child_id":child_id,"state":"running","hb":time.time(),"result":None,"fails":0}
  es[task_id]=e;self._save(es);return {"duplicate":False,"entry":e}
 def heartbeat(self,task_id,child_id):
  es=self._all();e=es.get(task_id)
  if e is None or e["child_id"]!=child_id or e["state"]!="running": return False
  e["hb"]=time.time();self._save(es);return True
 def complete(self,task_id,child_id,result_text):
  es=self._all();e=es.get(task_id)
  if e is None or e["child_id"]!=child_id or e["state"]!="running": return None
  e["state"]="done";e["result"]=result_text;e["hb"]=time.time();self._save(es);return e
 def fail(self,task_id,child_id,reason):
  es=self._all();e=es.get(task_id)
  if e is None or e["child_id"]!=child_id or e["state"]!="running": return None
  e["state"]="failed";e["fails"]+=1;e["reason"]=reason;self._save(es);return e
 def recover(self,task_id):
  es=self._all();e=es.get(task_id)
  if e is None or e["state"]!="failed": return None
  e["state"]="recoverable";self._save(es);return e
 def replace(self,task_id,new_child_id):
  es=self._all();e=es.get(task_id)
  if e is None or e["state"]!="recoverable": return None
  e["child_id"]=new_child_id;e["state"]="running";e["hb"]=time.time();self._save(es);return e
 def get(self,task_id): return self._all().get(task_id)
 def stale(self,max_age_s):
  now=time.time();return [t for t,e in self._all().items() if e["state"]=="running" and now-e["hb"]>max_age_s]
