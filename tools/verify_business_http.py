"""Check real model scheduling and real business execution through the preview UI API."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.performance_suite import Client, wait

client=Client('http://127.0.0.1:8769',Path('.local/business-preview/access_token'))
tasks=[]
for kind,units in [('log_analysis',10000),('document_search',1000),('archive',10000)]:
    tasks.append(client.call('/api/tasks',{'name':'业务链路验证-'+kind,'workload':{'kind':kind,'units':units,'seed':2030},'policy':'ai','deadline_ms':30000}))
rows=wait(client,tasks,60)
Path('.local/business-http.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
for row in rows:
    print(row['name'],row['status'],row.get('scheduler_decision'),flush=True)
assert all(row['status']=='completed' and row['result']['model']=='business-algorithms-v1' for row in rows)
assert any(row.get('scheduler_decision',{}).get('source')=='qwen' for row in rows)
