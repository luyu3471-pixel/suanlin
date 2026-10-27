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
            j=i+1
            while j<len(lines) and not lines[j].strip():j+=1
            if j<len(lines) and not lines[j].strip().startswith(('#','|','```')):
                story.append(KeepTogether([heading,Paragraph(plain(lines[j].strip().removeprefix('- ')),styles['Normal'])]))
                i=j+1
                continue
            story.append(heading)
        else:story.append(KeepTogether([Paragraph(plain(line.removeprefix('- ')),styles['Normal'])]))
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
    lines=['# 多终端任务调度系统原型测试与验证记录','本报告汇总2026年10月26日在开发机完成的软件检查、真实同机任务执行和离散事件仿真。三类证据分开报告；未包含办公室用户试点、物理多机或6G无线测试。','## 一 自动检查与界面验收','14项自动检查通过，完整原始输出保存于results/unittest.txt。覆盖真实HTTP执行、权限和来源校验、租约续期与到期重排、取消保护、旧结果拒收、无效结果保护、重启恢复、CSV安全和仿真可复现。','浏览器验收包含7项场景，结果保存在results/browser_qa.json。检查工作台加载、创建任务与分类输出、节点暂停恢复、仿真条件切换、部署说明、CSV下载和390像素手机布局。软件检查通过不等于真实场景安全性认证。','## 二 真实同机请求基准',f"平台为{actual['platform']}，Python {actual['python']}。两个同机工作进程，每策略{actual['rounds']}轮，每轮{actual['tasks_per_trial']}项，共{actual['total']}项正式任务；预热任务不进入正式统计。各轮使用相同输入，策略顺序轮换。","所有任务包含10万、20万或30万次SHA256人工负载循环，单独记录其耗时。下表为各轮指标的平均值；P95为逐轮最近秩P95再取平均，不是合并样本P95。完成时延含HTTP、排队、分类和附加负载。均值与P95只针对完成任务，失败数另报。",'| 策略 | 完成数 | 按时率 | 平均时延ms | 各轮P95均值ms |','|---|---|---|---|---|']
    names={'fixed':'固定单节点','round_robin':'轮询分配','random':'随机分配','smart':'联合调度'}
    for g in actual['groups']:lines.append(f"| {names[g['policy']]} | {g['completed']} | {g['deadline_rate']*100:.1f}% | {g['mean_ms']:.1f} | {g['p95_ms']:.1f} |")
    best=min(actual['groups'],key=lambda g:g['mean_ms'])
    lines+= [f"本次平均时延最低的是{names[best['policy']]}。所有策略均有适用条件，不能用仿真中的优势替换真实记录。本机测试不证明多物理终端收益，也不证明真实业务需要人工放大的负载。",'原始数据：results/local_benchmark_raw.csv。每轮统计与环境：results/local_benchmark_summary.json。', '## 三 参数化离散事件仿真','30个固定随机种子，每次240项任务，4种策略和3类条件，共86400条逐任务记录。输入哈希、参数、区间算法和限制保存在summary文件中。CI为以随机种子为单位的均值加减1.96倍标准误的近似区间。','| 条件 | 策略 | 按时率 | 近似95%区间 | 均值ms |','|---|---|---|---|---|']
    sn={'normal':'正常网络','congested':'受限带宽','burst':'任务突发'}
    for g in sim['groups']:
        low,high=g['deadline_rate']['ci95'];lines.append(f"| {sn[g['scenario']]} | {names[g['policy']]} | {g['deadline_rate']['mean']*100:.1f}% | {low*100:.1f}%至{high*100:.1f}% | {g['mean_ms']['mean']:.1f} |")
    lines+= ['所有网络与计算参数均是假设。仿真调度器获知名义处理时间与队列，未模拟物理信道、丢包、故障、GPU显存或模型冷启动。受限带宽与突发条件可能造成固定节点明显拥塞，从而放大差异。原型的小文本载荷不同于仿真的大载荷档，仿真只作为敏感性分析，不是实际业务或6G性能。','仿真不包含真实系统轮询的全部执行细节，两者时序不同。与固定、轮询、随机的比较也不等于已经与Ray或KubeEdge等成熟框架比较。','## 四 模型与数据','内置字符/双字多项式朴素贝叶斯模型使用48条自编合成文本。当前没有独立真实标注集，未报告准确率或F1。展示中的模型分数未经概率校准，所有输出均需人工复核。人工CPU负载用于观察排队，不能冒充重模型推理时间。','## 五 尚待取得的现场证据','未取得实际管理人员需求访谈、两台物理终端对比、真实巡检分类质量、外部试点、专利或软著证明。现场验证方案见操作手册。不能把本报告当作外部认证或应用证明。','## 六 系统实际截图']
    text='\n\n'.join(lines)
    # Restore adjacency of Markdown table rows after paragraph joining.
    text=re.sub(r'(\|)\n\n(?=\|)',r'\1\n',text)
    (DOCS/'测试与验证报告.md').write_text(text,encoding='utf-8')
    story=markdown_story(text)
    for name,caption in [('overview.png','真实本机工作台，所有节点为同机工作进程。'),('task-detail.png','浏览器验收中实际提交文本并取得分类结果，事件为合成文本。'),('nodes.png','节点暂停与恢复通过实际接口完成，可信范围是管理员配置。'),('experiments.png','实验页展示参数化仿真，非物理网络实测。')]:
        story.extend([PageBreak()]+screen(name,caption))
    pdf('03-佐证材料.pdf',story)

def slide_html(sim,actual):
    sim_names={'normal':'正常网络','congested':'受限带宽','burst':'任务突发'}
    names={'fixed':'固定单节点','round_robin':'轮询分配','random':'随机分配','smart':'联合调度'}
    slides=[]
    def add(title,body,kicker='SCENARIO DESIGN',note='',cover=False):
        slides.append(f'<section class="slide {"cover" if cover else ""}"><div class="kicker">{kicker}</div><h1>{title}</h1><div class="body">{body}</div><footer><span>多终端任务调度系统</span><span>{note}</span><b>{len(slides)+1:02d}</b></footer></section>')
    add('多终端任务调度系统','<h2 class="cover-sub">局域网任务分配与执行</h2><p class="lead">办公巡检事件的任务时限感知与资源协同</p><p class="cover-note">场景设计方案<br>应用层原型与参数化仿真验证</p>',kicker='MULTI-DEVICE TASK SCHEDULER',cover=True)
    add('办公场景与任务瓶颈','<div class="columns"><div><h2>突发提交</h2><p>任务集中到达<br>固定节点产生排队</p></div><div><h2>设备异构</h2><p>处理速度不同<br>其他设备可能空闲</p></div><div><h2>通信与数据约束</h2><p>远端计算有传输成本<br>部分任务限制处理范围</p></div></div><p class="statement">需求定位为待验证假设，尚无实际用户访谈证明</p>',note='目标对象：小型办公空间的管理与设备维护人员')
    add('系统架构与任务流程','<div class="flow"><div><b>01</b><h2>网页工作台</h2><p>事件文本<br>时限与范围<br>记录与导出</p></div><div><b>02</b><h2>协调服务</h2><p>筛选与调度<br>租约与重排<br>SQLite记录</p></div><div><b>03</b><h2>独立执行端</h2><p>注册与心跳<br>本地分类推理<br>结果与耗时</p></div></div><p class="statement">显式接入设备，任务整体分配，每节点一个执行槽</p>',note='HTTP应用层原型，不拆分大模型，不自动发现设备')
    add('可运行的协同工作台','<div class="workbench"><img class="screenshot" src="../results/screenshots/workbench-wide.png" alt="真实工作台截图"><div><h2>真实执行闭环</h2><p>节点注册与心跳<br>任务提交和分类<br>取消与超时重排<br>原始记录导出</p><p class="small-note">两个工作进程同在本机<br>尚未验证物理多机收益</p></div></div>',note='真实截图；同机多进程演示，非两台物理终端')
    add('时限与资源约束下的调度','<p class="lead">先按截止时间排序，再筛选允许处理的在线节点</p><div class="equation">预测完成耗时 = 剩余等待 + HTTP往返<br>+ 载荷传输 + 计算预测 × 1.15</div><div class="columns two"><div><h2>运行反馈</h2><p>单位耗时采用 EWMA 更新<br>0.65旧值 + 0.35新样本</p></div><div><h2>当前限制</h2><p>启发式，无最优性保证<br>单调超时惩罚不改变节点排序</p></div></div>',note='带宽是配置值；1.15为固定余量，不是统计置信界')
    add('执行可靠性与数据范围','<table><thead><tr><th>情况</th><th>处理方式</th><th>边界</th></tr></thead><tbody><tr><td>节点停止响应</td><td>25秒租约到期重排</td><td>最多3次执行尝试</td></tr><tr><td>旧结果迟到</td><td>校验当前租约</td><td>可能重复计算</td></tr><tr><td>暂停接收任务</td><td>停止新任务派发</td><td>已有任务继续完成</td></tr><tr><td>仅可信节点</td><td>管理员设定范围</td><td>没有硬件可信证明</td></tr></tbody></table><p class="statement">共享令牌与HTTP仅用于可信实验网络</p>',note='自动检查覆盖重试、取消、重启恢复及旧结果拒收')
    rows=''.join(f"<tr><td>{sim_names[g['scenario']]}</td><td>{names[g['policy']]}</td><td>{g['deadline_rate']['mean']*100:.1f}%</td><td>{g['mean_ms']['mean']:.1f}</td></tr>" for g in sim['groups'])
    add('参数化仿真结果','<p class="lead compact">30个种子 × 240项任务 × 4种策略 × 3类条件 = 86,400条记录</p><table class="dense"><thead><tr><th>假设条件</th><th>策略</th><th>按时完成率</th><th>平均时延ms</th></tr></thead><tbody>'+rows+'</tbody></table>',note='配置与原始记录完整保留；不是6G网络实测')
    rows=''.join(f"<tr><td>{names[g['policy']]}</td><td>{g['deadline_rate']*100:.1f}%</td><td>{g['mean_ms']:.1f}</td><td>{g['p95_ms']:.1f}</td></tr>" for g in actual['groups'])
    add('真实同机任务执行','<p class="lead compact">两个同机进程，四种策略各5轮，每轮16项，共320项正式任务</p><table><thead><tr><th>策略</th><th>按时完成率</th><th>平均时延ms</th><th>各轮P95均值ms</th></tr></thead><tbody>'+rows+'</tbody></table><p class="statement">所有策略均按时完成，当前联合策略没有普遍优势</p>',note='含单独计时的SHA256人工负载；不是多物理终端结果')
    add('AI与6G的关系','<div class="columns"><div><h2>当前AI</h2><p>真实文本分类推理<br>48条合成训练样本<br>结果须人工复核</p></div><div><h2>当前通信</h2><p>HTTP应用层协同<br>网络与计算共同估计<br>普通网络可部署</p></div><div><h2>6G方向</h2><p>AI与通信融合<br>分布式算力协同<br>未来接入准确遥测</p></div></div><p class="statement">没有6G空口、专网或标准协议栈验证</p>',note='依据：ITU-R M.2160-0，https://www.itu.int/rec/R-REC-M.2160-0-202311-I/en')
    add('应用价值的验证路径','<div class="columns"><div><h2>确认需求</h2><p>至少3位实际使用者<br>记录现有流程与时限<br>保留反对意见</p></div><div><h2>验证收益</h2><p>两至三台物理电脑<br>低负载与突发对照<br>失败任务完整保留</p></div><div><h2>检验质量</h2><p>独立标注事件集<br>宏平均F1与漏报率<br>评估维护成本</p></div></div><p class="statement">低负载场景中，单机可能更合适</p>',note='尚无付费客户、用户试点或实际效率提升证明')
    add('团队分工与实施计划','<table><thead><tr><th>角色</th><th>主要责任</th><th>共同复核</th></tr></thead><tbody><tr><td>软件工程成员</td><td>协调器、执行端、界面与接口</td><td>运行行为与版本</td></tr><tr><td>电子信息工程成员</td><td>组网、设备配置、网络与断连实验</td><td>指标和实验口径</td></tr><tr><td>两人共同</td><td>访谈、模型误差、材料和答辩</td><td>真实性与AI披露</td></tr></tbody></table><p class="lead">10月底接入真实终端，11月上旬补现场证据<br>11月12日前完成最终核查与上传</p>',note='官方报名及作品提交截止2026年11月13日20时，以后续通知为准')
    add('当前成果与下一步','<h2 class="end-title">可运行的系统<br>可追溯的记录<br>可重复的实验</h2><p class="lead">下一步：真实需求、物理多机与实际巡检数据验证</p><p class="sources">资料：aicomp.cn/tracks/tracks-4/5322.html<br>ITU-R M.2160-0；Ray Scheduling；KubeEdge Why KubeEdge<br>代码、材料与合成数据由生成式AI辅助制作，参赛成员须审核并负责</p>',note='场景设计研究原型，未承诺奖项、商用效果或6G性能')
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
    packages={x:importlib.metadata.version(x) for x in ['reportlab','pypdf','pillow','playwright','imageio-ffmpeg']}
    (ROOT/'results/build_environment.json').write_text(json.dumps({'packages':packages,'font':font_path.name,'source_date':'2026-10-26','slide_editor':'Artifact Session unavailable; local editable HTML + PDF fallback'},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Built proposal, evidence, operations manual and editable 12-slide HTML.')

if __name__=='__main__':main()
