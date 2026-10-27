"""Record genuine local browser interactions as a captioned 3-5 minute MP4."""
import json
import os
from pathlib import Path
import subprocess
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.tools'))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.tools/browsers'))
from playwright.sync_api import sync_playwright
import imageio_ffmpeg

def main(url='http://127.0.0.1:8765'):
    directory=ROOT/'tmp'/'video';directory.mkdir(parents=True,exist_ok=True)
    scenes=[]
    started=time.monotonic()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-proxy-server'])
        context=browser.new_context(viewport={'width':1280,'height':720},device_scale_factor=1,record_video_dir=str(directory),record_video_size={'width':1280,'height':720})
        page=context.new_page();video=page.video
        def caption(text,seconds):
            page.evaluate('''text=>{let n=document.querySelector('#demo-caption');if(!n){n=document.createElement('div');n.id='demo-caption';n.style.cssText='position:fixed;bottom:0;left:0;right:0;z-index:999999;background:#183f35f5;color:#f2f7e8;font:18px/1.65 "Microsoft YaHei",sans-serif;padding:15px 36px;text-align:center;box-shadow:0 -2px 20px #193c2d14;pointer-events:none;';document.body.append(n);}n.textContent=text;}''',text)
            scenes.append({'at':round(time.monotonic()-started,2),'seconds':seconds,'caption':text})
            page.wait_for_timeout(seconds*1000)
        def slide(index):
            page.goto((ROOT/'delivery/02-答辩展示.html').as_uri(),wait_until='load')
            page.add_style_tag(content='@media screen{.slide{margin:0;box-shadow:none}}html{scroll-behavior:auto}')
            page.evaluate('(i)=>window.scrollTo(0,i*720)',index)
        def live(hash='overview'):
            page.goto(url+'/#'+hash,wait_until='networkidle')
            page.locator('h1').wait_for()
        slide(0);caption('多终端任务调度系统。应用层研究原型，本视频为真实本机界面操作，无旁白。',14)
        slide(1);caption('设计假设：办公事件集中到达时，固定设备可能排队。实际用户需求仍需访谈验证。',15)
        live();caption('两个真实执行进程都运行在本机。当前不是两台物理终端，也没有接入6G无线网络。',17)
        live('nodes');caption('节点展示实际连接、心跳往返和执行状态。带宽为配置值，可信是管理员设置。',13)
        page.get_by_role('button',name='暂停接收任务').first.click()
        page.get_by_role('button',name='恢复接收任务').wait_for()
        caption('暂停节点接收新任务；已有任务仍可完成。恢复后节点重新参与分配。',8)
        page.get_by_role('button',name='恢复接收任务').click()
        live();page.get_by_role('button',name='创建任务',exact=True).click()
        page.locator('[name="name"]').fill('演示巡检事件分类')
        page.locator('[name="events"]').fill('消防出口有纸箱堵塞\n配电箱出现浓烟和明火\n机房门锁损坏无法关闭')
        caption('输入三条合成巡检文本，设定时限与范围。轻量模型执行分类，所有结果需要人工复核。',15)
        page.get_by_role('button',name='提交任务').click()
        page.locator('#create-dialog').wait_for(state='hidden')
        page.wait_for_timeout(1500)
        live('tasks');page.locator('#task-search').fill('演示巡检事件分类')
        page.get_by_role('button',name='查看 ↗').first.click()
        page.locator('#detail-dialog').wait_for(state='visible')
        caption('这是执行节点真实返回的分类结果。模型训练仅有48条自编合成样本，分数未校准。',17)
        page.get_by_role('button',name='关闭',exact=True).last.click()
        slide(4);caption('截止时间优先；估计等待、通信与计算耗时。该启发式没有最优性保证。',15)
        live('experiments');caption('同样输入比较四种策略。此页是参数化仿真，全部网络和计算配置均是假设。',13)
        page.get_by_role('button',name='受限带宽',exact=True).click()
        caption('切换受限带宽条件。固定节点在部分配置下积压，差距不能外推到任意真实网络。',12)
        page.get_by_role('button',name='任务突发',exact=True).click()
        caption('30个种子、每次240项任务，共86400条仿真记录，完整输入哈希和CSV一并交付。',12)
        slide(7);caption('320项真实同机基准单独报告。全部策略按时完成，联合策略并非所有条件下都更快。',18)
        slide(8);caption('AI与6G的关联是通信与计算协同的应用设计，尚未验证6G空口、专网或标准协议栈。',15)
        live('guide');caption('在第二台真实电脑运行执行端，即可继续办公室多终端验证；需保存设备表、照片和原始数据。',15)
        slide(11);caption('已交付可运行系统、完整代码、原始数据与参赛材料。真实需求、物理多机收益和模型泛化仍待验证。',15)
        context.close();raw=Path(video.path());browser.close()
    dest=ROOT/'delivery/04-演示视频.mp4'
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-i',str(raw),'-c:v','libx264','-preset','fast','-crf','24','-pix_fmt','yuv420p','-movflags','+faststart','-an',str(dest)],check=True)
    probe=subprocess.run([ffmpeg,'-hide_banner','-i',str(dest)],capture_output=True,text=True,encoding='utf-8',errors='replace')
    match=re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)',probe.stderr)
    if not match:raise RuntimeError('视频时长核验失败')
    h,m,s=match.groups();duration=int(h)*3600+int(m)*60+float(s)
    if not 180<=duration<=300:raise RuntimeError('视频时长应在3至5分钟之间')
    result={'file':dest.name,'bytes':dest.stat().st_size,'duration_seconds':duration,'actual_media_duration_seconds':duration,'width':1280,'height':720,'video_codec':'H.264','processing_seconds':round(time.monotonic()-started,2),'captioned':True,'audio':False,'source':'actual browser screen recording of local process execution; synthetic input; simulation labelled','scenes':scenes}
    (ROOT/'results/video_record.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Demo MP4 created: {dest.stat().st_size} bytes; {len(scenes)} captioned scenes.')

if __name__=='__main__':main()
