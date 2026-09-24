"""D-342 durable child-result handoff (file-backed JSONL store)."""
import json,time
from pathlib import Path
SUCCESS="completed"
class HandoffStore:
 def __init__(self,path): self._p=Path(path);self._p.parent.mkdir(parents=True,exist_ok=True)
 def _all(self):
  if not self._p.exists(): return []
  return [json.loads(x) for x in self._p.read_text(encoding="utf-8").splitlines() if x.strip()]
 def record(self,task_id,agent,parent_id,child_id,status,result_text,provenance):
  es=self._all()
  for e in es:
   if e["task_id"]==task_id and e["child_id"]==child_id: return {"duplicate":True,"entry":e}
  ok=status==SUCCESS and bool((result_text or "").strip())
  e={"task_id":task_id,"agent":agent,"parent_id":parent_id,"child_id":child_id,"status":status,"result_text":result_text or "","provenance":provenance,"success":ok}
  es.append(e)
  self._p.write_text("\n".join(json.dumps(x,sort_keys=True) for x in es)+"\n",encoding="utf-8")
  return {"duplicate":False,"entry":e}
 def get(self,task_id,child_id):
  return next((e for e in self._all() if e["task_id"]==task_id and e["child_id"]==child_id),None)
 def successes(self,task_id): return [e for e in self._all() if e["task_id"]==task_id and e.get("success")]
