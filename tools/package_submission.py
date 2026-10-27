"""Package deliverables, never include local credentials or developer runtimes."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--team',help='实际团队编号，确认后再传入');args=p.parse_args()
    if args.team and not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',args.team):p.error('团队编号仅支持字母数字下划线和短横线')
    delivery=ROOT/'delivery';delivery.mkdir(exist_ok=True)
    expected=['01-场景设计方案.pdf','02-答辩展示.pdf','02-答辩展示.html','03-佐证材料.pdf','04-演示视频.mp4','05-操作与提交手册.pdf','06-零基础图文使用教程.pdf']
    missing=[x for x in expected if not (delivery/x).exists()]
    if missing:raise RuntimeError('缺少最终材料：'+', '.join(missing))
    if (delivery/expected[0]).stat().st_size>15*1024*1024:raise RuntimeError('方案超过15MB')
    if (delivery/'04-演示视频.mp4').stat().st_size>300*1024*1024:raise RuntimeError('视频超过300MB')
    excluded={'.local','.tools','tmp','.git','.codex','.agents','__pycache__','node_modules','submission'}
    files=[]
    for path in ROOT.rglob('*'):
        rel=path.relative_to(ROOT)
        if any(part in excluded for part in rel.parts) or not path.is_file():continue
        if path.suffix in ('.zip','.pyc') or path.name in ('交付清单.json','token.txt','access_token'):continue
        files.append(path)
    files.sort()
    manifest={'project':'多终端任务调度系统','date':datetime.now(timezone(timedelta(hours=8))).date().isoformat(),'evidence':['actual same-host process execution','parameterized discrete-event simulation'],'not_completed':['real user demand interviews','multiple physical-device measurement','6G radio verification','competition registration and upload'],
              'files':[{'path':x.relative_to(ROOT).as_posix(),'bytes':x.stat().st_size,'sha256':hashlib.sha256(x.read_bytes()).hexdigest()} for x in files]}
    manifest_path=delivery/'交付清单.json';manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=delivery/'多终端任务调度系统-交付包.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for path in files+[manifest_path]:z.write(path,'多终端任务调度系统/'+path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip():raise RuntimeError('ZIP校验失败')
        if any(any(part in excluded for part in Path(n).parts) or n.endswith(('/access_token','/token.txt')) for n in z.namelist()):raise RuntimeError('压缩包含不应交付的内容')
    if args.team:
        prefix=f'{args.team}-场景设计-多终端任务调度系统'
        target=ROOT/'submission'/prefix
        if target.exists():raise RuntimeError('同编号提交目录已存在，请人工核查后使用新目录')
        target.mkdir(parents=True)
        for source,label in [('01-场景设计方案.pdf','作品方案.pdf'),('02-答辩展示.pdf','答辩材料.pdf'),('03-佐证材料.pdf','佐证材料.pdf'),('04-演示视频.mp4','演示视频.mp4')]:
            shutil.copy2(delivery/source,target/f'{prefix}-{label}')
        shutil.copy2(archive,target/f'{prefix}-代码与数据.zip')
        shutil.copy2(ROOT/'docs/作品简介.txt',target/f'{prefix}-作品简介.txt')
    print(f'Packaged {len(files)+1} files. ZIP size: {archive.stat().st_size} bytes. No access tokens, database or tool runtimes included.')

if __name__=='__main__':main()
