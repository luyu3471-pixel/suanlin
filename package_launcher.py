"""Portable A/B launcher. Credentials and runtime databases are created locally."""
import argparse,json,os,subprocess,sys,urllib.request,urllib.parse
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('mode',choices=['A','B'])
 parser.add_argument('--check',action='store_true',help='Validate settings without starting services')
 args=parser.parse_args()
 if sys.version_info<(3,10):raise SystemExit('Python 3.10+ is required')
 cfg=json.loads((ROOT/'settings.json').read_text(encoding='utf-8-sig'))
 if type(cfg['concurrency']) is not int or not 1<=cfg['concurrency']<=32:raise ValueError('Invalid concurrency')
 if type(cfg['port']) is not int or not 1<=cfg['port']<=65535:raise ValueError('Invalid port')
 for key in ['coordinator_url','model_url']:
  if urllib.parse.urlsplit(cfg[key]).scheme not in ('http','https'):raise ValueError('Invalid '+key)
 if args.check:
  print('Settings valid; mode='+args.mode+'; Python='+sys.version.split()[0]);return 0
 env=dict(os.environ,SUANLIN_SCHEDULER_URL=cfg['model_url'],SUANLIN_SCHEDULER_MODEL=cfg['model_name'])
 if args.mode=='A':
  try:
   request=urllib.request.Request(cfg['model_url'].rstrip('/')+'/models')
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=5) as response:json.load(response)
  except Exception as exc:
   print('Qwen service unavailable: '+type(exc).__name__)
   print('Start the model service first; see README.txt.');return 2
  command=[sys.executable,str(ROOT/'launch.py'),'--host','0.0.0.0','--port',str(cfg['port']),
           '--workers','1','--concurrency',str(cfg['concurrency']),'--scheduler','qwen','--data-dir',str(ROOT/'.local/runtime')]
 else:
  token=Path(cfg['token_file'])
  if not token.is_absolute():token=ROOT/token
  if not token.is_file():
   print('Missing token file: '+str(token));print('Copy the A runtime access_token to this file using a trusted method.');return 2
  command=[sys.executable,'-m','suanlin.worker','--coordinator',cfg['coordinator_url'],
           '--token-file',str(token),'--id',cfg['worker_id'],'--concurrency',str(cfg['concurrency'])]
 try:return subprocess.call(command,cwd=ROOT,env=env)
 except KeyboardInterrupt:return 130

if __name__=='__main__':
 try:raise SystemExit(main())
 except (ValueError,OSError,KeyError) as exc:
  print('Startup error: '+str(exc));raise SystemExit(2)
