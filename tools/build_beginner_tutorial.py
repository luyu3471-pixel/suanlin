"""Create a novice tutorial from genuine, isolated UI interactions.

App state is kept in tmp; existing user tasks and node settings are untouched.
PDF and standalone HTML share the same instructional content.
"""
import argparse
import base64
import csv
from datetime import datetime, timedelta, timezone
import html
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.tools'))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.tools' / 'browsers'))
ASSETS = ROOT / 'docs' / 'tutorial-assets'
OUT = ROOT / 'delivery'

def capture():
    from playwright.sync_api import sync_playwright
    from suanlin.server import create_server
    ASSETS.mkdir(parents=True, exist_ok=True)
    session = ROOT / 'tmp' / ('tutorial-' + uuid.uuid4().hex[:8])
    session.mkdir(parents=True)
    server = create_server(port=0, directory=session / 'data')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_address[1]}'
    children = []
    checks = []
    errors = []
    annotations = {}
    try:
        for i in range(2):
            children.append(subprocess.Popen([sys.executable, '-m', 'suanlin.worker', '--coordinator', url, '--token-file', str(session / 'data' / 'access_token'),
                                              '--name', f'本机执行节点 {i + 1}', '--id', f'local-{i + 1}'], cwd=ROOT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        limit = time.monotonic() + 10
        while time.monotonic() < limit:
            if len([n for n in server.store.snapshot()['nodes'] if n['online']]) == 2: break
            time.sleep(.1)
        else: raise RuntimeError('教程会话的两个执行端未就绪')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-proxy-server'])
            page = browser.new_page(viewport={'width':1280, 'height':1000}, device_scale_factor=1)
            page.on('pageerror', lambda error: errors.append(str(error)))

            def refresh():
                page.evaluate('async()=>{await refresh(true)}')
                page.wait_for_timeout(100)

            def go(name):
                page.goto(url + '/#' + name, wait_until='networkidle')
                page.locator('h1').wait_for()
                refresh()

            def shot(name, marks, clip=None, dialog=None):
                # Red numbered boxes are instructional annotations, not app UI.
                page.evaluate('''marks=>{
                    document.querySelector('#tutorial-annotations')?.remove();
                    const layer=document.createElement('div');layer.id='tutorial-annotations';layer.style.cssText='position:fixed;inset:0;pointer-events:none;z-index:2147483647;';(document.querySelector('dialog[open]')||document.body).append(layer);
                    for(const m of marks){const e=document.querySelector(m.selector);if(!e)throw Error('Missing tutorial target: '+m.selector);const r=e.getBoundingClientRect();const b=document.createElement('div');b.style.cssText=`position:fixed;left:${r.left-3}px;top:${r.top-3}px;width:${r.width+6}px;height:${r.height+6}px;border:3px solid #d84f3f;border-radius:6px;box-shadow:0 0 0 2px #ffffffc0;`;const n=document.createElement('span');n.textContent=m.number;n.style.cssText='position:absolute;left:-12px;top:-18px;width:28px;height:28px;display:grid;place-items:center;border-radius:50%;background:#d84f3f;color:white;font:bold 16px Arial;border:2px solid white;';b.append(n);layer.append(b);}
                }''', marks)
                if dialog:
                    box = page.locator(dialog).bounding_box()
                    clip = {'x':max(0,box['x']-20),'y':max(0,box['y']-24),'width':box['width']+40,'height':min(box['height']+44,1000-max(0,box['y']-24))}
                page.screenshot(path=str(ASSETS / name), clip=clip, animations='disabled')
                annotations[name] = marks
                page.evaluate('document.querySelector("#tutorial-annotations")?.remove()')

            def wait_name(name):
                limit = time.monotonic()+20
                while time.monotonic()<limit:
                    matches = [t for t in server.store.snapshot()['tasks'] if t['name']==name]
                    if matches and matches[0]['status']=='completed': return matches[0]
                    time.sleep(.1)
                raise RuntimeError('教程任务未完成：'+name)

            go('overview')
            shot('01-home.png', [{'selector':'.metric:first-child','number':1},{'selector':'a[data-page="tasks"]','number':2}], clip={'x':0,'y':80,'width':1280,'height':600})
            checks.append('isolated coordinator and two real subprocesses online')
            go('tasks')
            shot('02-demo-button.png', [{'selector':'.filters [data-action="demo"]','number':1}], clip={'x':235,'y':90,'width':1045,'height':470})
            page.locator('.filters [data-action="demo"]').click()
            limit = time.monotonic()+20
            while time.monotonic()<limit:
                snap=server.store.snapshot()
                if snap['metrics']['total']==12 and snap['counts']['completed']==12:break
                time.sleep(.1)
            else:raise RuntimeError('12项演示任务未全部完成')
            refresh()
            shot('03-task-list.png', [{'selector':'tbody tr:first-child td:nth-child(3)','number':1},{'selector':'tbody tr:first-child td:nth-child(4)','number':2},{'selector':'tbody tr:first-child [data-detail]','number':3}],clip={'x':235,'y':90,'width':1045,'height':670})
            checks.append('12 distinct demo tasks actually completed')
            page.get_by_role('button',name='创建任务',exact=True).click()
            page.locator('[name="name"]').fill('我的第一次测试')
            page.locator('[name="events"]').fill('消防通道被纸箱堵塞\n机房门锁损坏无法关闭\n办公室设备运行正常')
            page.locator('[name="policy"]').select_option('smart')
            page.locator('[name="privacy"]').select_option('local_only')
            page.locator('[name="deadline_ms"]').fill('5000')
            page.locator('[name="compute_loops"]').fill('0')
            shot('04-filled-form.png',[{'selector':'[name="name"]','number':1},{'selector':'[name="events"]','number':2},{'selector':'[name="policy"]','number':3},{'selector':'[name="privacy"]','number':4},{'selector':'[name="deadline_ms"]','number':5},{'selector':'[name="compute_loops"]','number':6},{'selector':'#task-form button[type="submit"]','number':7}],dialog='#create-dialog')
            page.get_by_role('button',name='提交任务',exact=False).click()
            page.locator('#create-dialog').wait_for(state='hidden')
            personal=wait_name('我的第一次测试')
            page.locator('#task-search').fill('我的第一次测试')
            refresh()
            page.locator('tbody tr').first.wait_for()
            assert page.locator('tbody tr').count()==1
            shot('05-find-task.png',[{'selector':'#task-search','number':1},{'selector':'tbody tr:first-child td:nth-child(4)','number':2},{'selector':'tbody tr:first-child [data-detail]','number':3}],clip={'x':235,'y':90,'width':1045,'height':470})
            page.locator('tbody tr:first-child [data-detail]').click()
            page.locator('#detail-dialog').wait_for(state='visible')
            for label in ('通道占用','门禁异常','正常状态'):page.get_by_text(label,exact=True).wait_for()
            shot('06-task-detail.png',[{'selector':'.detail-meta span:first-child','number':1},{'selector':'.detail-meta span:nth-child(2)','number':2},{'selector':'.prediction','number':3}],dialog='#detail-dialog')
            assert len(server.store.task(personal['id'])['result']['predictions'])==3
            checks.append('one named task contains three texts and returns three actual predictions')
            page.locator('#detail-dialog [data-action="close-detail"]').click()
            go('nodes')
            # Hide only host metadata in public tutorial screenshots.
            page.evaluate('document.querySelectorAll(".node-details div:nth-child(2) b").forEach(x=>x.textContent="主机信息已隐藏")')
            shot('07-node-buttons.png',[{'selector':'button[data-node="local-1"]','number':1},{'selector':'button[data-trust="local-1"]','number':2}],clip={'x':235,'y':365,'width':1045,'height':470})
            page.locator('button[data-node="local-1"]').click()
            page.get_by_role('button',name='恢复接收任务',exact=True).wait_for()
            page.evaluate('document.querySelectorAll(".node-details div:nth-child(2) b").forEach(x=>x.textContent="主机信息已隐藏")')
            shot('08-paused-node.png',[{'selector':'.node-card:first-child .badge','number':1},{'selector':'button[data-node="local-1"]','number':2}],clip={'x':235,'y':365,'width':1045,'height':470})
            page.get_by_role('button',name='创建任务',exact=True).click()
            page.locator('[name="name"]').fill('暂停节点后的测试')
            page.locator('[name="events"]').fill('办公室设备运行正常')
            page.get_by_role('button',name='提交任务',exact=False).click()
            routed=wait_name('暂停节点后的测试')
            assert routed['worker_id']=='local-2'
            go('tasks')
            page.locator('#task-search').fill('暂停节点后的测试')
            refresh()
            shot('09-rerouted-task.png',[{'selector':'tbody tr:first-child td:nth-child(3)','number':1}],clip={'x':235,'y':90,'width':1045,'height':440})
            go('nodes');page.get_by_role('button',name='恢复接收任务',exact=True).click();refresh()
            assert not server.store.workers['local-1']['draining']
            checks.append('paused local-1; next task actually routed to local-2; local-1 restored')
            go('tasks')
            shot('10-export.png',[{'selector':'button[data-action="export"]','number':1}],clip={'x':235,'y':90,'width':1045,'height':400})
            with page.expect_download() as download:
                page.get_by_role('button',name='导出记录',exact=False).click()
            downloaded=session/'tutorial-export.csv'
            download.value.save_as(str(downloaded))
            with downloaded.open(encoding='utf-8-sig',newline='') as f:exported=list(csv.DictReader(f))
            assert len(exported)==14
            checks.append('CSV export contains exactly the 14 tutorial tasks')
            go('experiments')
            shot('11-simulation.png',[{'selector':'.experiment-intro .badge','number':1},{'selector':'.scenario-tabs','number':2}],clip={'x':235,'y':90,'width':1045,'height':650})
            checks.append('simulation page explicitly identifies simulation evidence')
            browser.close()
        if errors:raise RuntimeError('浏览器错误：'+str(errors))
        (ROOT/'results/tutorial_capture_checks.json').write_text(json.dumps({'passed':True,'date':datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
            'scope':'isolated same-host tutorial UI capture; synthetic example texts; no physical multi-device or 6G evidence',
            'checks':checks,'browser_errors':errors,'screenshots':annotations,'existing_user_state_modified':False},ensure_ascii=False,indent=2),encoding='utf-8')
        print('Captured 11 annotated screenshots and verified the tutorial workflow.',flush=True)
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
        for child in children:
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        server.shutdown();server.server_close();thread.join(3);server.store.db.close()

def content():
    return [
      {'title':'打开文件夹并启动平台','part':'先完成这6步','blocks':[
        ('p','这份教程教你按按钮完成一次操作。先做第1至6步；暂停节点、导出和双机连接可以之后再学。'),
        ('p','网页版每一步先向下滚动看完，再点击“下一步”。截图可以点击放大；PDF版本按页阅读。'),
        ('steps',['同时按键盘上的 Win 和 E，打开文件资源管理器。Win键通常带有Windows图标。','点击文件资源管理器最上方的地址栏，输入下面的文件夹路径，然后按回车。']),
        ('code','D:\\6G'),
        ('p','在这个文件夹里找到 start.bat，双击它。如果没有显示文件后缀，可能只看到名为 start 的 Windows 批处理文件。不要双击 launch.py，也不要打开 web/index.html。'),
        ('table',[['看到什么','怎么判断'],['弹出一个命令窗口','这是后台服务，请保持开着。'],['浏览器自动打开多终端任务调度系统','启动成功，进入第2步。'],['浏览器没有自动打开','保持命令窗口开着，按第2步手动打开网址。']]),
        ('note','本机已经安装Python，可以直接练习。教程按项目放在D:\\6G编写；如果你另存了位置，就打开实际存放start.bat的那个文件夹。'),
        ('success','命令窗口保持运行，没有显示“启动失败”。') ]},
      {'title':'打开网页，找到任务中心','part':'先完成这6步','blocks':[
        ('steps',['打开常用浏览器，例如Edge或Chrome。点击最上方的网址栏。','输入下面的网址，按回车。这里输入网址，不是输入到网页搜索框。']),
        ('code','http://127.0.0.1:8765'),
        ('image',('01-home.png','实际首页截图，红框数字是教程标注。你看到的任务数量可能不同。',245)),
        ('table',[['图中标记','你要看什么或点击什么'],['1','“在线算力节点”通常为2，表示两个后台处理程序已连接。'],['2','点击左侧“任务中心”，进入下一步。']]),
        ('note','默认两个节点都在当前这台电脑上。节点可以先理解成“负责接任务的处理程序”。'),
        ('success','能看到“协同工作台”，右上角显示“协调服务在线”。') ]},
      {'title':'点击一次，运行现成任务','part':'先完成这6步','blocks':[
        ('p','在左侧点击“任务中心”。先用现成任务熟悉流程，不需要自己填写文字。'),
        ('image',('02-demo-button.png','在任务中心上方找到红框1中的“运行演示任务”。',170)),
        ('steps',['点击“运行演示任务”一次。它会提交12项独立的示例任务。','等待几秒，查看下方“任务记录”。状态会从待调度到执行中，再到已完成；很快完成时，可能直接看到已完成。']),
        ('image',('03-task-list.png','红框1是执行节点，2是状态，3是“查看”按钮。',215)),
        ('note','示例任务使用合成文字，并带有用于观察排队的额外CPU负载。它们是教学演示，不是真实办公室事件。不要连续反复点击。'),
        ('success','出现12项新任务，并能看到“已完成”的状态。') ]},
      {'title':'填写自己的第一项任务','part':'先完成这6步','blocks':[
        ('p','点击页面右上角“创建任务”。弹出表单后，按下面的数字填写。'),
        ('form',('04-filled-form.png',[
          ['编号','填写内容'],['1 任务名称','我的第一次测试'],['2 事件文本','复制本页下方三行文字'],['3 调度策略','联合调度'],['4 数据范围','保持“仅管理员设为可信的节点”'],['5 完成时限','5000，意思是5秒'],['6 额外CPU负载','0，先不增加额外计算'],['7 提交按钮','填好后点击“提交任务”']
        ])),
        ('p','把下面三行完整复制到“事件文本”框，每条占一行：'),
        ('code','消防通道被纸箱堵塞\n机房门锁损坏无法关闭\n办公室设备运行正常'),
        ('note','这些是练习用的示例句子。三行文字会作为1项任务提交，由1个节点一起处理，最终返回3条分类结果。'),
        ('success','点击“提交任务”后，表单关闭，页面提示任务已提交。') ]},
      {'title':'找到刚刚提交的任务','part':'先完成这6步','blocks':[
        ('steps',['回到左侧“任务中心”。','在上方搜索框中输入“我的第一次测试”。状态筛选保持“全部状态”。','找到对应任务，等待状态成为“已完成”。','点击这一行最右侧的“查看”。']),
        ('image',('05-find-task.png','红框1输入名称，红框2检查已完成，红框3点击查看。',300)),
        ('table',[['这一列','表示什么'],['执行节点','这项任务由哪个处理程序执行。'],['状态','是否仍在等待、正在运行或已经完成。'],['完成时延','从提交到收到结果花了多久，ms表示毫秒。']]),
        ('note','平台会保留旧任务。有旧记录很正常，不需要先清空数据库。若刚提交的任务没出现，先清空搜索条件并选择“全部状态”。'),
        ('success','打开名为“我的第一次测试”的任务详情弹窗。') ]},
      {'title':'读懂结果，完成第一次使用','part':'先完成这6步','blocks':[
        ('image',('06-task-detail.png','这是程序真实返回的结果。红框1为完成状态，2为总时延，3为第一条分类结果。',390)),
        ('table',[['你输入的文字','本次示例结果'],['消防通道被纸箱堵塞','通道占用'],['机房门锁损坏无法关闭','门禁异常'],['办公室设备运行正常','正常状态']]),
        ('p','看到这3条结果，说明你已经完成了提交、分配、执行和查看。点击弹窗右上角×关闭，再继续操作。'),
        ('note','先看分类名称和完成状态即可。模型分数未经校准，分数100%也不代表现实中一定判断正确。当前示例模型需要人工复核结果。'),
        ('success','能看到自己的任务名称、已完成状态，以及3条输入对应的分类结果。') ]},
      {'title':'进阶练习，暂停一个节点','part':'会基本操作后再学','blocks':[
        ('p','先等现有任务结束。左侧点击“算力节点”，找到“本机执行节点1”。'),
        ('image',('07-node-buttons.png','点击红框1“暂停接收任务”。红框2“撤销可信设置”先不要点。',190)),
        ('p','按钮变成“恢复接收任务”，说明暂停成功。节点仍然在线，只是不再接收新任务。另一个节点保持正常接收。'),
        ('steps',['创建一项新任务，名称填“暂停节点后的测试”，事件文本填“办公室设备运行正常”，其余保持第4步设置。','提交后在任务中心搜索这个名字，查看“执行节点”。']),
        ('image',('09-rerouted-task.png','暂停节点1后，本次新任务实际由节点2完成。',175)),
        ('p','最后回到节点页，点击节点1的“恢复接收任务”，让两个节点都能继续接活。'),
        ('note','暂停前已经领取的任务可以继续完成。不要同时暂停所有节点，否则新任务会一直待调度。'),
        ('success','新任务由节点2执行，随后你已恢复节点1接收任务。') ]},
      {'title':'导出记录，正确关闭平台','part':'会基本操作后再学','blocks':[
        ('p','在任务中心或工作台右上角，点击“导出记录”。浏览器会下载一个CSV文件。'),
        ('image',('10-export.png','红框1是“导出记录”。文件会进入浏览器的下载目录。',210)),
        ('steps',['在浏览器下载列表找到“任务调度记录.csv”。也可以按Ctrl+J打开下载列表。','用Excel、WPS或文本编辑器打开。主要查看id、name、worker_id、status、latency_ms等列。']),
        ('table',[['CSV字段','通俗解释'],['name','你填写的任务名称'],['worker_id','执行任务的节点编号'],['status','completed为已完成，queued为待调度'],['latency_ms','任务完成时延，单位毫秒'],['deadline_met','是否在设置时限内完成']]),
        ('p','结束使用时，切回启动start.bat时弹出的命令窗口，按住Ctrl再按C，停止后台服务。关闭浏览器只是关闭页面。下次使用重新双击start.bat。'),
        ('note','导出包含历史任务，不只包含刚才的一项。不要为了获得干净截图而删除原始数据库。'),
        ('success','CSV已下载；按Ctrl+C后后台服务停止。') ]},
      {'title':'对比实验页面怎么看','part':'会基本操作后再学','blocks':[
        ('p','点击左侧“对比实验”，可以切换正常网络、受限带宽和任务突发。'),
        ('image',('11-simulation.png','红框1明确标为仿真，红框2用于切换不同假设条件。',300)),
        ('table',[['页面或数据','属于哪种记录'],['任务中心和导出CSV','你通过这个平台实际提交的任务记录。'],['对比实验页','预先运行的参数化仿真；不是刚提交任务自动产生的对照。'],['平均时延','平均等待多久收到结果，通常越小越好。'],['按时完成率','在规定时限内完成的任务比例，通常越高越好。']]),
        ('note','切换此页不会给真实电脑限速，也不会改变任务中心的已完成结果。仿真里的网络和计算参数是假设，不是实际6G性能。'),
        ('success','能区分自己实际提交的记录与预先生成的仿真结果。') ]},
      {'title':'卡住时，按现象排查','part':'遇到问题直接看这页','blocks':[
        ('table',[['你看到的现象','先这样处理'],['找不到start.bat','确认进入D:\\6G。若文件后缀被隐藏，找名为start的Windows批处理文件。'],['网页无法访问','先确认start.bat的命令窗口还在运行，再输入完整网址http://127.0.0.1:8765。'],['提示端口已占用','先打开8765网址。如果已经是多终端任务调度系统，就使用现有页面；不要连续重复启动。'],['在线节点为0','等待几秒刷新。若仍为0，检查启动窗口是否报错，再正常停止并重启。'],['任务一直待调度','至少一个节点必须在线、未暂停接收、且设为可信。单独检查这些设置。'],['找不到刚提交的任务','清空搜索条件，将状态切回“全部状态”，再搜索你自己填写的任务名称。'],['同一任务出现3条结果','因为你在一个表单中输入了3行，它们属于同一项任务。这是正常行为。'],['看见接入令牌登录框','本机练习请用127.0.0.1网址。远程浏览器访问需使用当前协调端的接入令牌。'],['命令窗口提示找不到python','当前开发机已安装Python；若换到其他电脑，需要先安装Python 3.10或更高版本。']]),
        ('p','如果8765确实被其他程序使用，可以在项目文件夹地址栏输入powershell并按回车，再粘贴下面命令并按回车：'),
        ('code','python launch.py --port 8766'),
        ('p','随后浏览器改用 http://127.0.0.1:8766。仍卡住时，保留启动窗口的完整报错，并记录你停在教程哪一步。'),
        ('success','能根据现象找到下一项检查，不需要修改代码或删除数据。') ]},
      {'title':'可选，准备第二台真实电脑','part':'前面熟练后再做双机','blocks':[
        ('p','前面的截图使用1台电脑上的2个处理程序。下面才是两台物理电脑的连接方法。第一次练习可以暂时跳过。'),
        ('steps',['把当前电脑称为A，另一台称为B。两台连接同一个可信局域网。','A先用Ctrl+C关闭之前的多终端任务调度系统后台。','在A的D:\\6G文件夹地址栏输入powershell，按回车，打开当前目录的终端。','粘贴下面命令并按回车，保持窗口开着。']),
        ('code','python launch.py --host 0.0.0.0 --workers 1'),
        ('p','这会在A启动协调服务和1个本机执行程序。A仍通过http://127.0.0.1:8765使用网页。'),
        ('p','另开终端，运行以下命令，找到当前连接网卡的IPv4地址并记下来：'),
        ('code','ipconfig'),
        ('p','下面以192.168.1.100为示例，实际必须换成A的地址。把更新后的项目包解压到B，找到有launch.py的那层文件夹。B需要Python 3.10或以上。'),
        ('p','把A的D:\\6G\\.local\\access_token文件通过可信方式复制到B的项目目录，改名为token.txt。复制文件本身即可，不用把令牌写进命令。'),
        ('note','令牌只供连接设备使用，不放进公开文件、截图或网盘。此页使用默认.local数据目录；如果你自己另设了数据目录，以实际目录中的access_token为准。'),
        ('success','A的服务保持运行；你已记下A的真实IP；B有项目文件和token.txt。') ]},
      {'title':'可选，接入B并确认真实执行','part':'前面熟练后再做双机','blocks':[
        ('p','在B的项目文件夹地址栏输入powershell并按回车。将下面命令中的示例IP替换成A的真实IP，再整行粘贴运行：'),
        ('code','python -m suanlin.worker --coordinator http://192.168.1.100:8765 --token-file token.txt --name 办公室电脑B --id office-b'),
        ('steps',['保持B的执行端窗口开着，回到A的网页“算力节点”。','找到“办公室电脑B”，核对后点击“设为可信”。远程端默认不可信。','确认A和B都在线、未暂停接收。旧的离线记录可以保留。','回任务中心点击运行演示任务，再检查执行节点列是否出现办公室电脑B。']),
        ('table',[['检查项','怎样才算完成'],['设备真的不同','A、B是两台物理电脑，不能把两个程序当两台电脑。'],['连接成功','A的节点页显示B在线。'],['可以处理默认任务','B已设为可信，并接收任务。'],['确实承担工作','任务记录中出现B作为执行节点。只显示在线还不够。']]),
        ('p','如果本批任务没有分到B，可创建数项独立任务，将策略改为“轮询分配”，分别提交，再查看执行节点。不要把多行文字误当成多项独立任务。'),
        ('note','连接失败时检查IP、网络和A的专用网络防火墙许可。部分访客或校园Wi-Fi会隔离终端，可改用你们有权管理的可信实验网络；不需要关闭整个防火墙。'),
        ('p','需要正式收集双机、需求和模型证据时，再阅读docs/现场取证操作手册.md。日常使用先完成前6步即可。'),
        ('success','A显示B在线，而且你找到至少一项确实由B处理完成的任务。') ]},
    ]

def build_pdf(pages):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak, KeepTogether
    from reportlab.lib.utils import ImageReader
    pdfmetrics.registerFont(TTFont('TutorialCN', 'C:/Windows/Fonts/simhei.ttf'))
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='TutorialBody',fontName='TutorialCN',fontSize=11,leading=17,spaceAfter=9,wordWrap='CJK'))
    styles.add(ParagraphStyle(name='TutorialTitle',fontName='TutorialCN',fontSize=22,leading=30,spaceAfter=18,textColor=colors.black))
    styles.add(ParagraphStyle(name='TutorialCell',parent=styles['TutorialBody'],fontSize=9,leading=13,spaceAfter=0))
    styles.add(ParagraphStyle(name='TutorialNote',parent=styles['TutorialBody'],fontSize=9,leading=14,textColor=colors.HexColor('#596b5b')))
    styles.add(ParagraphStyle(name='TutorialCode',parent=styles['TutorialBody'],fontSize=11,leading=18,spaceBefore=3,spaceAfter=13))
    width=499
    def p(text,style='TutorialBody'):return Paragraph(html.escape(text).replace('\n','<br/>'),styles[style])
    def table(rows,small=False,widths=None):
        cols=len(rows[0]);w=widths or ([125,width-125] if cols==2 else [width/cols]*cols)
        t=Table([[p(c,'TutorialCell') for c in row] for row in rows],colWidths=w,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e9eee5')),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#d9d9d9')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8)]))
        return t
    def image(filename,max_height):
        path=ASSETS/filename;w,h=ImageReader(str(path)).getSize();factor=min(width/w,max_height/h)
        return Image(str(path),width=w*factor,height=h*factor,hAlign='LEFT')
    story=[]
    for index,page in enumerate(pages):
        if index:story.append(PageBreak())
        story += [p('多终端任务调度系统零基础图文使用教程','TutorialNote'),p(f'{index+1:02d}  {page["title"]}','TutorialTitle')]
        for kind,value in page['blocks']:
            if kind=='p':story.append(p(value))
            elif kind=='steps':story += [p(f'{i+1}. {text}') for i,text in enumerate(value)]
            elif kind=='code':story.append(p(value.replace('\\\\','\\'),'TutorialCode'))
            elif kind=='table':story += [table(value),Spacer(1,10)]
            elif kind=='image':
                filename,caption,max_height=value
                story += [KeepTogether([image(filename,max_height),Spacer(1,4),p(caption,'TutorialNote')])]
            elif kind=='form':
                filename,rows=value;path=ASSETS/filename;w,h=ImageReader(str(path)).getSize();factor=min(262/w,358/h)
                pic=Image(str(path),width=w*factor,height=h*factor,hAlign='LEFT')
                legend=table(rows,widths=[73,154])
                layout=Table([[pic,legend]],colWidths=[272,227],hAlign='LEFT')
                layout.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
                story += [layout,Spacer(1,9)]
            elif kind=='note':story.append(p('注意：'+value,'TutorialNote'))
            elif kind=='success':story += [Spacer(1,4),p('完成标志：'+value)]
    def header(canvas,doc):
        canvas.saveState();canvas.setFont('TutorialCN',8);canvas.setFillColor(colors.HexColor('#7e8b7b'))
        canvas.drawString(48,819,'多终端任务调度系统  平台操作教程');canvas.drawRightString(547,27,f'{doc.page} / {len(pages)}')
        canvas.bookmarkPage(f'step-{doc.page}')
        if doc.page<=len(pages):canvas.addOutlineEntry(pages[doc.page-1]['title'],f'step-{doc.page}',0,False)
        canvas.restoreState()
    OUT.mkdir(exist_ok=True)
    path=OUT/'06-零基础图文使用教程.pdf'
    SimpleDocTemplate(str(path),pagesize=(595.28,841.89),leftMargin=48,rightMargin=48,topMargin=51,bottomMargin=42,title='多终端任务调度系统零基础图文使用教程',author='多终端任务调度系统项目').build(story,onFirstPage=header,onLaterPages=header)
    from pypdf import PdfReader
    actual=len(PdfReader(path).pages)
    if actual!=len(pages):raise RuntimeError(f'教程出现溢出分页：预期{len(pages)}页，实际{actual}页，请调整版面')
    return path

def build_html(pages):
    esc=html.escape
    def embedded(filename):return 'data:image/png;base64,'+base64.b64encode((ASSETS/filename).read_bytes()).decode()
    def picture(filename,caption):return f'<figure><button class="zoom-image" title="点击放大截图"><img src="{embedded(filename)}" alt="{esc(caption)}"></button><figcaption>{esc(caption)} 点击图片可放大。</figcaption></figure>'
    sections=[];markdown=['# 多终端任务调度系统零基础图文使用教程','','先做第1至6步。截图来自独立同机教学会话；图中数字和任务记录可以与你的不同。','']
    for index,page in enumerate(pages):
        blocks=[];markdown += [f'## {index+1} {page["title"]}','']
        for kind,value in page['blocks']:
            if kind=='p':blocks.append('<p>'+esc(value)+'</p>');markdown += [value,'']
            elif kind=='steps':blocks.append('<ol>'+''.join('<li>'+esc(x)+'</li>' for x in value)+'</ol>');markdown += [f'{i+1}. {x}' for i,x in enumerate(value)]+['']
            elif kind=='code':
                value=value.replace('\\\\','\\')
                blocks.append(f'<div class="code-wrap"><pre>{esc(value)}</pre><button class="copy" data-copy="{esc(value,quote=True)}">复制</button></div>');markdown += ['```text',value,'```','']
            elif kind=='table':
                blocks.append('<div class="table-scroll"><table>'+''.join('<tr>'+''.join(f'<{"th" if i==0 else "td"}>{esc(c)}</{"th" if i==0 else "td"}>' for c in row)+'</tr>' for i,row in enumerate(value))+'</table></div>')
                markdown += ['| '+' | '.join(row)+' |' for row in value[:1]]+['| '+' | '.join(['---']*len(value[0]))+' |']+['| '+' | '.join(row)+' |' for row in value[1:]]+['']
            elif kind=='image':
                filename,caption,_=value;blocks.append(picture(filename,caption));markdown += [f'![{caption}](tutorial-assets/{filename})','']
            elif kind=='form':
                filename,rows=value;blocks.append('<div class="form-explainer">'+picture(filename,'填好的任务表单，按红框编号填写。')+'<table>'+''.join('<tr>'+''.join(f'<{"th" if i==0 else "td"}>{esc(c)}</{"th" if i==0 else "td"}>' for c in row)+'</tr>' for i,row in enumerate(rows))+'</table></div>')
                markdown += [f'![填好的任务表单](tutorial-assets/{filename})','']+[f'{row[0]}：{row[1]}' for row in rows[1:]]+['']
            elif kind=='note':blocks.append('<p class="note">注意：'+esc(value)+'</p>');markdown += ['注意：'+value,'']
            elif kind=='success':blocks.append('<p class="success">完成标志：'+esc(value)+'</p>');markdown += ['完成标志：'+value,'']
        sections.append(f'<section class="step" id="step-{index+1}"><div class="part">{esc(page["part"])}</div><h1>{index+1:02d}　{esc(page["title"])}</h1>'+''.join(blocks)+'</section>')
    css='''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f5f6f2;color:#263b30;font:17px/1.9 "Microsoft YaHei","Segoe UI",sans-serif}button,select{font:inherit}button{cursor:pointer}a{color:#285c45}aside{position:fixed;inset:0 auto 0 0;width:265px;background:#fff;border-right:1px solid #e0e6dc;padding:28px 20px;overflow:auto}.brand{font-size:22px;font-weight:600;line-height:1.6}.aside-note{font-size:13px;color:#71846b;margin:11px 0 20px}nav{display:grid;gap:6px}nav a{font-size:13px;padding:8px 11px;border-radius:7px;text-decoration:none;line-height:1.7;color:#71806b}nav a.active{background:#e8f0df;color:#27513c;font-weight:600}.open-platform{display:block;background:#28523f;color:#fff;text-decoration:none;text-align:center;padding:10px;border-radius:7px;margin:20px 0 10px;font-size:15px}.side-small{font-size:12px;color:#829078;line-height:1.8}main{margin-left:265px;padding:28px 42px 95px;max-width:1450px}.top-note{font-size:13px;background:#fff;border:1px solid #e0e6da;padding:12px 16px;border-radius:8px;margin-bottom:22px;color:#697e60}.step{display:none;background:white;border:1px solid #e0e6db;border-radius:12px;padding:30px 35px}.step.active{display:block}.part{font-size:12px;letter-spacing:1px;color:#80966e}h1{font-size:28px;line-height:1.6;font-weight:600;margin:8px 0 23px}p{margin:13px 0}ol{padding-left:27px}li{padding:5px 0}figure{margin:22px 0}.zoom-image{padding:0;border:1px solid #e1e5dc;background:#f8faf5;display:block;width:100%;border-radius:7px;overflow:hidden}.zoom-image img{display:block;max-width:100%;max-height:650px;width:auto;height:auto;margin:auto}figcaption{font-size:12px;color:#7c8e70;line-height:1.8;margin-top:8px}.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;margin:20px 0;font-size:15px}th,td{border:1px solid #dce3d6;padding:12px 14px;text-align:left;vertical-align:middle}th{background:#eef3e8;color:#42613a;font-weight:500}td:first-child{min-width:120px}.code-wrap{position:relative;background:#f0f4e9;border:1px solid #dce6cf;border-radius:7px;margin:20px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:16px/1.9 Consolas,"Microsoft YaHei",monospace;color:#294d38;padding:17px 75px 17px 18px;margin:0}.copy{position:absolute;right:8px;top:9px;border:1px solid #ccd9c1;background:white;color:#69825c;border-radius:5px;padding:5px 9px;font-size:12px}.note{font-size:14px;color:#758269;line-height:1.9;border-top:1px solid #e8ece3;padding-top:14px}.success{font-size:16px;color:#315e3d;font-weight:600;margin-top:23px}.form-explainer{display:grid;grid-template-columns:1.05fr 1fr;gap:22px;align-items:start}.form-explainer table{font-size:13px;margin-top:22px}.form-explainer td:first-child{min-width:80px}.pager{position:fixed;bottom:0;left:265px;right:0;background:#ffffffef;border-top:1px solid #dfe6d7;padding:14px 35px;display:flex;justify-content:space-between;align-items:center;backdrop-filter:blur(8px)}.pager button{border:1px solid #cbd9c1;background:#fff;color:#54714a;border-radius:7px;padding:9px 20px;font-size:15px}.pager button:last-child{background:#28523f;color:#fff;border-color:#28523f}.pager button:disabled{opacity:.4;cursor:default}.progress{font-size:13px;color:#7d8d74}.mobile-nav{display:none}dialog{padding:12px;border:1px solid #d7e1cc;border-radius:10px;max-width:96vw;max-height:94vh}dialog::backdrop{background:#153426bb}dialog img{display:block;max-width:91vw;max-height:82vh;width:auto;height:auto;margin:auto}dialog .close{display:block;margin:0 0 8px auto;border:1px solid #d9e4cf;background:#fff;padding:6px 15px;border-radius:6px;color:#315e3d}noscript .step{display:block}@media(max-width:900px){aside{display:none}main{margin:0;padding:16px 14px 100px}.mobile-nav{display:block;width:100%;padding:9px 10px;border:1px solid #d9e2d0;border-radius:7px;background:white;color:#4f6e40;margin-bottom:15px;font-size:14px}.step{padding:22px 18px}h1{font-size:23px}body{font-size:16px}.pager{left:0;padding:12px 15px}.form-explainer{grid-template-columns:1fr}.form-explainer .zoom-image img{max-height:650px}th,td{padding:10px;font-size:13px}.top-note{font-size:12px}}@media print{aside,.pager,.top-note,.mobile-nav,.copy{display:none}main{margin:0;padding:0}.step{display:block;border:0;break-before:page}.step:first-of-type{break-before:auto}.zoom-image img{max-height:400px}}'''
    nav=''.join(f'<a href="#step-{i+1}" data-step="{i+1}">{i+1:02d}　{esc(x["title"])}</a>' for i,x in enumerate(pages))
    options=''.join(f'<option value="{i+1}">{i+1:02d} {esc(x["title"])}</option>' for i,x in enumerate(pages))
    script=r'''const pages=[...document.querySelectorAll('.step')];let current=1;function show(n){current=Math.max(1,Math.min(pages.length,Number(n)||1));pages.forEach((p,i)=>p.classList.toggle('active',i===current-1));document.querySelectorAll('[data-step]').forEach(a=>a.classList.toggle('active',Number(a.dataset.step)===current));document.querySelector('.progress').textContent=`第 ${current} / ${pages.length} 步`;document.querySelector('#prev').disabled=current===1;document.querySelector('#next').disabled=current===pages.length;document.querySelector('.mobile-nav').value=String(current);history.replaceState(null,'','#step-'+current);window.scrollTo(0,0)}document.querySelectorAll('[data-step]').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();show(a.dataset.step)}));document.querySelector('#prev').onclick=()=>show(current-1);document.querySelector('#next').onclick=()=>show(current+1);document.querySelector('.mobile-nav').onchange=e=>show(e.target.value);document.querySelectorAll('.zoom-image').forEach(b=>b.onclick=()=>{document.querySelector('#zoom img').src=b.querySelector('img').src;document.querySelector('#zoom').showModal()});document.querySelector('#zoom .close').onclick=()=>document.querySelector('#zoom').close();document.querySelectorAll('.copy').forEach(b=>b.onclick=async()=>{const text=b.dataset.copy;try{await navigator.clipboard.writeText(text)}catch(e){const t=document.createElement('textarea');t.value=text;document.body.append(t);t.select();const ok=document.execCommand('copy');t.remove();if(!ok){b.textContent='请手动复制';return}}b.textContent='已复制';setTimeout(()=>b.textContent='复制',1500)});document.addEventListener('keydown',e=>{if(document.querySelector('dialog').open)return;if(e.key==='ArrowRight'){e.preventDefault();show(current+1)}if(e.key==='ArrowLeft'){e.preventDefault();show(current-1)}});show((location.hash.match(/step-(\d+)/)||[])[1]||1);'''
    document='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>多终端任务调度系统操作手册</title><style>'+css+'</style></head><body><aside><div class="brand">多终端任务调度系统<br>操作手册</div><p class="aside-note">先做前6步，完成一次提交与查看。<br>红框数字告诉你点哪里。</p><nav>'+nav+'</nav><a class="open-platform" href="http://127.0.0.1:8765" target="_blank" rel="noopener">打开实际平台 ↗</a><p class="side-small">先运行start.bat，再点上方链接。<br>本文件可以离线阅读。</p></aside><main><div class="top-note">这里是教程，截图里的按钮只能放大查看。请在另一个浏览器标签打开实际平台操作。图中数字不必与你的任务记录完全一致。</div><select class="mobile-nav" aria-label="跳转到步骤">'+options+'</select>'+''.join(sections)+'</main><div class="pager"><button id="prev">← 上一步</button><span class="progress"></span><button id="next">下一步 →</button></div><dialog id="zoom"><button class="close">关闭大图 ×</button><img alt="放大的操作截图"></dialog><noscript><style>.step{display:block}</style></noscript><script>'+script+'</script></body></html>'
    path=ROOT/'操作手册.html';path.write_text(document,encoding='utf-8')
    (ROOT/'docs/平台操作手册.md').write_text('\n'.join(markdown),encoding='utf-8')
    return path

def verify_html(path,pages):
    from playwright.sync_api import sync_playwright
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-proxy-server'])
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(path.as_uri(),wait_until='load')
        assert page.locator('.step.active').count()==1
        for i in range(1,len(pages)+1):
            page.locator(f'a[data-step="{i}"]').click()
            assert page.locator('.step.active').get_attribute('id')==f'step-{i}'
            assert page.evaluate('Array.from(document.querySelectorAll("img[src]")).every(x=>x.complete&&x.naturalWidth>0)')
        page.locator('a[data-step="4"]').click()
        page.locator('.step.active .zoom-image').first.click();assert page.locator('#zoom').is_visible()
        page.locator('#zoom .close').click();assert not page.locator('#zoom').is_visible()
        page.locator('a[data-step="1"]').click();page.locator('.step.active .copy').first.click()
        page.wait_for_timeout(100)
        assert page.locator('.step.active .copy').first.inner_text()=='已复制'
        page.set_viewport_size({'width':390,'height':844})
        page.locator('.mobile-nav').select_option('4')
        assert page.locator('.step.active').get_attribute('id')=='step-4'
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        browser.close()
    if errors:raise RuntimeError('教程HTML存在浏览器错误：'+str(errors))
    return {'chapter_navigation':True,'screenshots_loaded':True,'image_zoom':True,'copy_button':True,'mobile_navigation':True,'mobile_body_overflow':False,'browser_errors':errors}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--capture',action='store_true',help='在独立临时会话内重新采集实际操作截图');args=parser.parse_args()
    if args.capture:capture()
    def normalize(value):
        if isinstance(value,str):return value.replace('\\\\','\\')
        if isinstance(value,list):return [normalize(x) for x in value]
        if isinstance(value,tuple):return tuple(normalize(x) for x in value)
        if isinstance(value,dict):return {k:normalize(v) for k,v in value.items()}
        return value
    pages=normalize(content())
    pdf=build_pdf(pages)
    webpage=build_html(pages)
    checks=verify_html(webpage,pages)
    (ROOT/'results/tutorial_artifact_checks.json').write_text(json.dumps({'pages':len(pages),'pdf_bytes':pdf.stat().st_size,'html_bytes':webpage.stat().st_size,'checks':checks,'pdf_visual_review':'required separately after render'},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Created {len(pages)}-page PDF and standalone illustrated HTML; navigation, zoom, copying and mobile layout checked.')

if __name__=='__main__':main()
