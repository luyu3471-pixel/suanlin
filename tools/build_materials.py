"""Build editable HTML and submission PDFs from actual evidence and source docs."""
import html
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
if (ROOT/'.tools').exists():sys.path.insert(0,str(ROOT/'.tools'))
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER,TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,PageBreak,KeepTogether
from reportlab.lib.utils import ImageReader

OUT=ROOT/'delivery';OUT.mkdir(exist_ok=True)
DOCS=ROOT/'docs'
FONT_CANDIDATES=[Path('C:/Windows/Fonts/simhei.ttf'),Path('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc')]
font_path=next((p for p in FONT_CANDIDATES if p.exists()),None)
if not font_path:raise RuntimeError('请在FONT_CANDIDATES设置可嵌入的中文字体路径')
pdfmetrics.registerFont(TTFont('Chinese',str(font_path)))
styles=getSampleStyleSheet()
for name in ('Normal','Title','Heading1','Heading2','Heading3'):
    styles[name].fontName='Chinese';styles[name].textColor=colors.black
styles['Normal'].fontSize=9.5;styles['Normal'].leading=16;styles['Normal'].spaceAfter=7;styles['Normal'].wordWrap='CJK'
styles['Title'].fontSize=22;styles['Title'].leading=31;styles['Title'].alignment=TA_LEFT;styles['Title'].spaceAfter=21
styles['Heading1'].fontSize=14;styles['Heading1'].leading=22;styles['Heading1'].spaceBefore=17;styles['Heading1'].spaceAfter=9
styles['Heading2'].fontSize=11;styles['Heading2'].leading=18;styles['Heading2'].spaceBefore=11;styles['Heading2'].spaceAfter=7
for name in ('Title','Heading1','Heading2','Heading3'):
    styles[name].keepWithNext=True
styles.add(ParagraphStyle(name='CellCN',parent=styles['Normal'],fontSize=8,leading=12,spaceAfter=0))
styles.add(ParagraphStyle(name='CompactCellCN',parent=styles['Normal'],fontSize=7.5,leading=11,spaceAfter=0))
styles.add(ParagraphStyle(name='CaptionCN',parent=styles['Normal'],fontSize=8,leading=13,textColor=colors.HexColor('#7c8c79'),spaceBefore=5,spaceAfter=14))

def plain(text):
    return html.escape(text.replace('**','').replace('`',''))

def table(rows,widths=None):
    cols=len(rows[0]);total=487
    compact=rows[0][0]=='日期' and cols==4
    if compact:
        widths=[60,142,142,143]
    widths=widths or [total/cols]*cols
    content=[[Paragraph(plain(c),styles['CompactCellCN' if compact else 'CellCN']) for c in row] for row in rows]
    t=Table(content,colWidths=widths,repeatRows=1,hAlign='LEFT')
    rules=[('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dfe8dc')),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#d9d9d9')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]
    for i in range(1,len(rows)):
        if i%2==0:rules.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor('#f6f8f3')))
    if compact:
        rules += [('TOPPADDING',(0,0),(-1,-1),2.5),('BOTTOMPADDING',(0,0),(-1,-1),2.5),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6)]
    t.setStyle(TableStyle(rules));return t

def footer(canvas,doc):
    canvas.saveState();canvas.setFont('Chinese',8);canvas.setFillColor(colors.HexColor('#86937e'))
    canvas.drawString(54,818,'多终端任务调度系统');canvas.drawRightString(541,30,str(doc.page));canvas.restoreState()

def markdown_story(text):
    lines=text.splitlines();story=[];i=0
    while i<len(lines):
        line=lines[i].strip()
        if not line:i+=1;continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith('|'):
                cells=[c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[:\- ]+',c) for c in cells):rows.append(cells)
                i+=1
            if rows:
                story.extend([Spacer(1,7),table(rows),Spacer(1,12)])
            continue
        if line.startswith('```'):
            block=[];i+=1
            while i<len(lines) and not lines[i].startswith('```'):
                block.append(lines[i]);i+=1
            story.append(Paragraph('<br/>'.join(plain(x) for x in block),styles['CaptionCN']));i+=1;continue
        if line.startswith('# '):story.append(Paragraph(plain(line[2:]),styles['Title']))
        elif line.startswith(('## ','### ')):
            prefix=4 if line.startswith('### ') else 3
            heading=Paragraph(plain(line[prefix:]),styles['Heading2' if prefix==4 else 'Heading1'])
            story.append(heading)
        else:story.append(Paragraph(plain(line.removeprefix('- ')),styles['Normal']))
        i+=1
    return story

def pdf(filename,story):
    SimpleDocTemplate(str(OUT/filename),pagesize=(595.28,841.89),rightMargin=54,leftMargin=54,topMargin=58,bottomMargin=51,title=filename.removesuffix('.pdf'),author='多终端任务调度系统项目').build(story,onFirstPage=footer,onLaterPages=footer)

def screen(name,caption):
    p=ROOT/'results'/'screenshots'/name
    if not p.exists():return []
    w,h=ImageReader(str(p)).getSize();height=min(550,487*h/w);width=height*w/h
    return [KeepTogether([Image(str(p),width=width,height=height,hAlign='LEFT'),Paragraph(plain(caption),styles['CaptionCN'])])]

def build_report(sim,actual):
    # The reviewed Markdown is authoritative; rebuilding must not restore 2026-10 claims.
    text=(DOCS/'测试与验证报告.md').read_text(encoding='utf-8')
    story=markdown_story(text)
    for name,caption in [('nodes.png','2026-11-11既有双机实测节点截图，非本次新测试。'),('decision.png','既有实测任务决策截图；区分原样模型、保护与回退。')]:
        path=ROOT/'release-evidence'/name
        w,h=ImageReader(str(path)).getSize()
        height=min(600,487*h/w);width=height*w/h
        story += [PageBreak(), Image(str(path),width=width,height=height,hAlign='LEFT'), Paragraph(plain(caption),styles['CaptionCN'])]
    pdf('03-佐证材料.pdf',story)

def slide_html(sim,actual):
    sim_names={'normal':'正常网络','congested':'受限带宽','burst':'任务突发'}
    names={'fixed':'固定单节点','round_robin':'轮询分配','random':'随机分配','smart':'联合调度'}
    slides=[]
    def add(title,body,kicker='SCENARIO DESIGN',note='',cover=False):
        slides.append(f'<section class="slide {"cover" if cover else ""}"><div class="kicker">{kicker}</div><h1>{title}</h1><div class="body">{body}</div><footer><span>多终端任务调度系统</span><span>{note}</span><b>{len(slides)+1:02d}</b></footer></section>')
    add('多终端任务调度系统','<h2 class="cover-sub">局域网任务分配与执行</h2><p class="lead">办公事件与CPU业务的时限感知和资源协同</p><p class="cover-note">场景设计方案<br>2.4.1-slot-aware · 2026-11-12<br>真实双机实测与历史仿真分开报告</p>',kicker='MULTI-DEVICE TASK SCHEDULER',cover=True)
    add('办公场景与任务瓶颈','<div class="columns"><div><h2>突发提交</h2><p>任务集中到达<br>固定节点产生排队</p></div><div><h2>设备异构</h2><p>处理速度不同<br>其他设备可能空闲</p></div><div><h2>通信与数据约束</h2><p>远端计算有传输成本<br>部分任务限制处理范围</p></div></div><p class="statement">需求定位为待验证假设，尚无实际用户访谈证明</p>',note='目标对象：小型办公空间的管理与设备维护人员')
    add('系统架构与任务流程','<div class="flow"><div><b>01</b><h2>网页工作台</h2><p>任务类型与规格<br>时限与范围<br>记录与导出</p></div><div><b>02</b><h2>协调服务</h2><p>Qwen与排程保护<br>租约与重排<br>SQLite记录</p></div><div><b>03</b><h2>独立执行端</h2><p>注册与心跳<br>CPU业务算法<br>结果与耗时</p></div></div><p class="statement">A协调与Qwen调度，A/B执行CPU业务；本次A四槽/B四槽</p>',note='HTTP应用层原型，不拆分大模型，不自动发现设备')
    add('可运行的协同工作台','<div class="workbench"><img class="screenshot" src="../release-evidence/nodes.png" alt="既有双机实测节点截图"><div><h2>真实执行闭环</h2><p>节点注册与心跳<br>日志、检索、压缩<br>取消与超时重排<br>原始记录导出</p><p class="small-note">已有真实双机任务记录<br>压缩跨机差异仍待核查</p></div></div>',note='2026-11-11实测截图；截图不是本次新运行，需核实现场连接')
    add('Qwen方案与逐槽排程保护','<p class="lead">按截止时间排队，筛选在线、能力与范围合格的节点</p><div class="columns"><div><h2>模型建议</h2><p>逐槽可用时间<br>业务独立成本<br>最多8项一批</p></div><div><h2>排程保护</h2><p>整批重放校验<br>参考方案最多两轮重排<br>未开始任务可改派</p></div><div><h2>来源记录</h2><p>qwen：原样采纳<br>guarded：采用保护<br>fallback：异常回退</p></div></div><p class="statement">模型等待计入总耗时；保护贡献不能全部归因于Qwen</p>',note='重任务等待最多8秒，短任务4秒；单次模型调用3秒')
    add('执行可靠性与数据范围','<table><thead><tr><th>情况</th><th>处理方式</th><th>边界</th></tr></thead><tbody><tr><td>节点停止响应</td><td>25秒租约到期重排</td><td>最多3次执行尝试</td></tr><tr><td>旧结果迟到</td><td>校验当前租约</td><td>可能重复计算</td></tr><tr><td>暂停接收任务</td><td>停止新任务派发</td><td>已有任务继续完成</td></tr><tr><td>仅可信节点</td><td>管理员设定范围</td><td>没有硬件可信证明</td></tr></tbody></table><p class="statement">共享令牌与HTTP仅用于可信实验网络</p>',note='自动检查覆盖重试、取消、重启恢复及旧结果拒收')
    rows=''.join(f"<tr><td>{sim_names[g['scenario']]}</td><td>{names[g['policy']]}</td><td>{g['deadline_rate']['mean']*100:.1f}%</td><td>{g['mean_ms']['mean']:.1f}</td></tr>" for g in sim['groups'])
    add('历史参数化仿真结果','<p class="lead compact">30个种子 × 240项任务 × 4种策略 × 3类条件 = 86,400条记录</p><table class="dense"><thead><tr><th>假设条件</th><th>策略</th><th>按时完成率</th><th>平均时延ms</th></tr></thead><tbody>'+rows+'</tbody></table>',note='2026-10-26历史数据；不含Qwen，不是6G或双机实测')
    summary=json.loads((ROOT/'results/slot_aware_20261111_summary.json').read_text(encoding='utf-8'))
    reference=next(r for r in summary['reports'] if 'new-b4' in r['directory'] and r['profile']=='throughput')
    final=next(r for r in summary['reports'] if 'retest-b4' in r['directory'] and r['profile']=='throughput')
    rows=''.join(f"<tr><td>{label}</td><td>{g['mean_batch_seconds']:.3f}</td><td>{g['completed']}/{g['submitted']}</td><td>{period}</td></tr>" for label,g,period in [
        ('A单机',reference['policies']['fixed_A'],'前轮2.4.0'),('B单机',reference['policies']['fixed_B'],'前轮2.4.0'),('双机规则',reference['policies']['smart'],'前轮2.4.0'),('Qwen＋排程保护',final['policies']['ai'],'2.4.1专项')])
    add('真实双机：24项持续任务','<p class="lead compact">A4/B4；优化阶段跨版本450项正式任务＋54项预热一次完成</p><table><thead><tr><th>策略</th><th>整批秒</th><th>完成/提交</th><th>测量轮次</th></tr></thead><tbody>'+rows+'</tbody></table><p class="statement">最终6项原样Qwen、18项保护、0回退；规则参考仍更快</p>',note='各一轮，跨轮参考非同时测量；压缩差异未解，整体正确性未验收')
    add('AI与6G的关系','<div class="columns"><div><h2>当前AI</h2><p>A本地Qwen辅助调度<br>B不安装模型<br>轻量分类仅作演示</p></div><div><h2>当前通信</h2><p>HTTP应用层协同<br>网络与计算共同估计<br>普通网络可部署</p></div><div><h2>6G方向</h2><p>AI与通信融合<br>分布式算力协同<br>未来接入准确遥测</p></div></div><p class="statement">没有6G空口、专网或标准协议栈验证</p>',note='依据：ITU-R M.2160-0，https://www.itu.int/rec/R-REC-M.2160-0-202311-I/en')
    add('应用价值的验证路径','<div class="columns"><div><h2>确认需求</h2><p>至少3位实际使用者<br>记录现有流程与时限<br>保留反对意见</p></div><div><h2>验证收益</h2><p>已完成A4/B4实测<br>继续多轮负载对照<br>失败任务完整保留</p></div><div><h2>检验质量</h2><p>压缩跨机差异核查<br>独立真实业务质量<br>评估维护成本</p></div></div><p class="statement">低负载场景中，单机可能更合适</p>',note='尚无付费客户、用户试点或实际效率提升证明')
    add('团队分工与实施计划','<table><thead><tr><th>角色</th><th>主要责任</th><th>共同复核</th></tr></thead><tbody><tr><td>软件工程成员</td><td>协调器、执行端、界面与接口</td><td>运行行为与版本</td></tr><tr><td>电子信息工程成员</td><td>组网、设备配置、网络与断连实验</td><td>指标和实验口径</td></tr><tr><td>两人共同</td><td>访谈、模型误差、材料和答辩</td><td>真实性与AI披露</td></tr></tbody></table><p class="lead">11月11日已形成双机原始记录<br>11月12日统一2.4.1材料；访谈与业务质量待补</p>',note='旧计划不等于已完成；提交规则按报名系统核对')
    add('当前成果与下一步','<h2 class="end-title">可运行的系统<br>可追溯的记录<br>可重复的实验</h2><p class="lead">下一步：压缩差异、真实需求与业务质量验证</p><p class="sources">资料：aicomp.cn/tracks/tracks-4/5322.html<br>Qwen3.5-9B：官方Apache-2.0；llama.cpp：MIT<br>本地Q6_K下载来源、哈希与运行时构建号待补<br>ITU-R M.2160-0；Ray；KubeEdge；详见AI使用与第三方声明<br>代码、材料与合成数据由生成式AI辅助制作，参赛成员须审核并负责</p>',note='场景设计研究原型，未承诺奖项、商用效果或6G性能')
    css='''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#dfe6da;font-family:"Microsoft YaHei","Segoe UI",sans-serif;color:#244b3d}.slide{width:1280px;height:720px;padding:54px 72px 52px;background:#f8faf5;position:relative;overflow:hidden;break-after:page}.slide:nth-child(2n){background:#edf2e8}.kicker{font-size:13px;letter-spacing:3px;color:#8b9f7a;margin-bottom:27px}h1{font-size:39px;font-weight:500;margin:0 0 33px;letter-spacing:1px}h2{font-size:26px;font-weight:500;margin:0 0 20px}.lead{font-size:25px;line-height:1.8;margin:0 0 30px;color:#6e8762}.compact{font-size:21px;margin-bottom:20px}.columns{display:grid;grid-template-columns:1fr 1fr 1fr;gap:44px;margin-top:63px}.columns.two{grid-template-columns:1fr 1fr;margin-top:30px}.columns p,.flow p{font-size:23px;line-height:1.95;color:#829774;margin:0}.columns h2{padding-top:18px;border-top:1px solid #cfddc4}.statement{font-size:22px;line-height:1.7;color:#70865f;margin-top:52px}.flow{display:grid;grid-template-columns:repeat(3,1fr);gap:45px;margin-top:28px}.flow>div{border-top:2px solid #a7bd92;padding-top:18px}.flow b{font-size:16px;color:#a4b593}.flow h2{margin:17px 0 17px}.equation{font-size:30px;line-height:1.8;padding:13px 0;color:#305c40}.cover{background:#edf2e8!important}.cover h1{font-size:106px;line-height:1.2;margin:76px 0 20px}.cover-sub{font-size:42px;margin:0 0 24px}.cover .lead{font-size:24px}.cover-note{font-size:20px;line-height:1.8;color:#91a381;margin-top:42px}footer{position:absolute;bottom:27px;left:72px;right:72px;border-top:1px solid #dbe5d3;padding-top:14px;display:flex;gap:22px;justify-content:space-between;font-size:11px;color:#94a784}footer b{font-size:12px;font-weight:400}.screenshot{height:489px;max-width:1136px;object-fit:contain;object-position:left top;display:block}table{width:100%;border-collapse:collapse;font-size:21px;line-height:1.7}th{text-align:left;font-size:17px;font-weight:500;color:#799568;background:#e4eddc}th,td{padding:17px 15px;border-bottom:1px solid #d9e3d0}td{color:#547a48}.dense{font-size:17px;line-height:1.2}.dense th,.dense td{padding:7px 14px}.end-title{font-size:39px;line-height:1.6;margin-top:36px;margin-bottom:22px}.sources{font-size:15px;line-height:1.9;color:#9aaa8d;margin-top:27px}@page{size:338.667mm 190.5mm;margin:0}@media print{body{background:white}.slide{margin:0;break-inside:avoid}html{scroll-behavior:auto}}@media screen{.slide{margin:20px auto;box-shadow:0 6px 25px #1c422015}body{min-width:1280px}}'''
    css += '.screenshot{height:450px}th,td{padding:12px 15px}table{line-height:1.55}.dense{font-size:16px;line-height:1.2}.dense th,.dense td{padding:4px 14px}footer span:nth-child(2){max-width:800px;text-align:right;font-size:10px;overflow-wrap:anywhere}.columns p,.flow p{color:#607a52}.lead,.statement{color:#5b734f}.kicker,footer,.cover-note,.sources{color:#728765}.workbench{display:grid;grid-template-columns:800px 1fr;gap:40px;align-items:center}.workbench .screenshot{width:800px;height:450px;object-fit:contain}.workbench p{font-size:21px;line-height:2;color:#607a52}.workbench .small-note{font-size:16px;margin-top:35px}'
    content='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>多终端任务调度系统场景设计答辩</title><style>'+css+'</style></head><body>'+''.join(slides)+'<script>document.addEventListener("keydown",e=>{if(["ArrowRight","PageDown","ArrowLeft","PageUp"].includes(e.key)){e.preventDefault();const n=Math.round(window.scrollY/740)+(["ArrowRight","PageDown"].includes(e.key)?1:-1);document.querySelectorAll(".slide")[Math.max(0,Math.min(11,n))].scrollIntoView()}})</script></body></html>'
    (OUT/'02-答辩展示.html').write_text(content,encoding='utf-8')

def main():
    sim=json.loads((ROOT/'results/simulation_summary.json').read_text(encoding='utf-8'))
    actual=json.loads((ROOT/'results/local_benchmark_summary.json').read_text(encoding='utf-8'))
    pdf('01-场景设计方案.pdf',markdown_story((DOCS/'场景设计方案.md').read_text(encoding='utf-8')))
    build_report(sim,actual)
    manual=[]
    for i,name in enumerate(['交付与提交指南.md','现场验证计划.md','答辩讲稿与问答.md','分工与18天计划.md','AI使用与第三方声明.md']):
        if i:manual.append(PageBreak())
        manual+=markdown_story((DOCS/name).read_text(encoding='utf-8'))
    pdf('05-操作与提交手册.pdf',manual)
    slide_html(sim,actual)
    packages={x:importlib.metadata.version(x) for x in ['reportlab','pypdf','pillow']}
    (ROOT/'results/materials_build_environment.json').write_text(json.dumps({'packages':packages,'font':font_path.name,'source_date':'2026-11-12','measurement_date':'2026-11-11','version':'2.4.1-slot-aware','slide_editor':'Existing editable HTML design; Chromium PDF export via Node Playwright'},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Built proposal, evidence, operations manual and editable 12-slide HTML.')

if __name__=='__main__':main()
