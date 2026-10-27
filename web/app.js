'use strict';
const $ = (s, root = document) => root.querySelector(s);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',nodes:'M8 3h8v6H8z M2 15h8v6H2z M14 15h8v6h-8z M12 9v3 M6 15v-3h12v3',layers:'m12 3 10 5-10 5L2 8z M2 12l10 5 10-5 M2 16l10 5 10-5',chart:'M4 3v18h18 M8 16v-4 M13 16V8 M18 16V5',book:'M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3z M12 6v16',code:'m8 7-5 5 5 5 M16 7l5 5-5 5 M14 3l-4 18',plus:'M12 5v14 M5 12h14',arrow:'M5 19 19 5 M6 5h13v13',clock:'M12 8v5l4 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',check:'m5 12 4 4L19 6',activity:'M2 12h5l3-8 4 16 3-8h5',server:'M4 3h16v7H4z M4 14h16v7H4z M7 7h1 M7 18h1 M12 7h5 M12 18h5',monitor:'M3 3h18v13H3z M12 16v5 M8 21h8',shield:'m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6z m-4 10 3 3 5-6',download:'M12 3v12 m-5-5 5 5 5-5 M4 17v4h16v-4',info:'M12 10v7 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',play:'m8 4 12 8-12 8z'};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.grid}"/></svg>`;
document.querySelectorAll('[data-icon]').forEach(e => e.innerHTML = icon(e.dataset.icon));
const titles = {overview:'协同工作台',nodes:'算力节点',tasks:'任务中心',experiments:'对比实验',guide:'部署与验证'};
const labels = {completed:'已完成',running:'执行中',queued:'待调度',failed:'执行失败',cancelled:'已取消'};
const policies = {smart:'联合调度',fixed:'固定单节点',round_robin:'轮询分配',random:'随机分配'};
let state = null, current = 'overview', simulation = null, scenario = 'normal', taskFilter = 'all', taskSearch = '', lastError = false, fetching = false;
let toastTimer;
function toast(text) { $('#toast').textContent = text; $('#toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 3500); }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) { if(response.status===401 && !$('#login-dialog').open) $('#login-dialog').showModal(); throw new Error(result.error || '请求失败'); }
  return result;
}
const fmt = (x, digits=0) => x == null ? '—' : Number(x).toLocaleString('zh-CN', {maximumFractionDigits:digits});
const ms = x => x == null ? '—' : `${fmt(x,1)} ms`;
const percent = x => x == null ? '—' : `${(x*100).toFixed(1)}%`;
const time = x => new Date(x*1000).toLocaleTimeString('zh-CN',{hour12:false});
function badge(node) { return `<span class="badge ${!node.online?'offline':node.draining?'warn':''}"><i></i>${!node.online?'已离线':node.draining?'已暂停':node.busy_task?'执行中':'在线空闲'}</span>`; }
function heading(title,desc,eyebrow='COMPUTING, TOGETHER',actions=true) { return `<section class="page-heading"><div><span class="eyebrow">${eyebrow}</span><h1>${title}</h1><p>${desc}</p></div>${actions?`<div class="heading-actions"><button class="button secondary" data-action="export">${icon('download')} 导出记录</button><button class="button primary" data-action="create">${icon('plus')} 创建任务</button></div>`:''}</section>`; }
function metrics() {
 const active=state.nodes.filter(n=>n.online),m=state.metrics;
 const cards=[['nodes','在线算力节点',active.length,'个',`${state.nodes.filter(n=>n.online&&n.mode==='remote_host').length} 个远程终端`,'实时节点心跳'],['layers','累计完成任务',m.completed,'项',`${state.counts.running} 项正在执行`,`${state.counts.queued} 项等待调度`],['clock','平均完成时延',m.mean_ms==null?'—':fmt(m.mean_ms,1),'ms','服务端提交至结果接收','含人工附加 CPU 负载'],['shield','全量任务按时完成率',percent(m.deadline_rate),'',`${m.total} 项已提交任务`,'待完成与失败均计入分母']];
 return `<section class="metrics" aria-label="实时指标">${cards.map((c,i)=>`<article class="metric ${i===3?'highlight':''}"><div class="metric-top">${c[1]}<span class="metric-icon">${icon(c[0])}</span></div><div class="metric-value">${c[2]}<small>${c[3]}</small></div><div class="metric-foot"><b>${c[4]}</b><span>${c[5]}</span></div></article>`).join('')}</section>`;
}
function heroArt() { return `<div class="hero-art" aria-hidden="true"><svg viewBox="0 0 290 160"><g stroke="#bccfa8" fill="none" stroke-width="1"><ellipse class="orbit" cx="150" cy="76" rx="110" ry="55" transform="rotate(-18 150 76)"/><ellipse cx="150" cy="76" rx="78" ry="38" transform="rotate(24 150 76)"/><path d="M63 102l87-40 79 25M150 62V20m0 42 3 67" stroke-dasharray="4 5"/></g><g fill="#f4f8ec" stroke="#c5d7b1"><rect x="129" y="38" width="45" height="45" rx="11"/><rect x="39" y="83" width="38" height="35" rx="9"/><rect x="214" y="69" width="34" height="31" rx="8"/><rect x="138" y="118" width="28" height="26" rx="7"/></g><path d="M143 53h17v17h-17z M137 57h6m17 0h6m-29 8h6m17 0h6m-18-18v6m8-6v6m-8 17v6m8-6v6" fill="none" stroke="#7b9a60" stroke-width="1.6"/><path d="M49 93h18v12H49zm9 12v5m-6 0h12M223 79h16v4h-16zm0 8h16" fill="none" stroke="#9eb681" stroke-width="1.2"/><circle cx="150" cy="19" r="4" fill="#a1bd82"/><circle cx="247" cy="41" r="3" fill="#a1bd82"/><circle cx="96" cy="133" r="2" fill="#a1bd82"/></svg></div>`; }
function topology() {
 const online=state.nodes.filter(n=>n.online).slice(0,4),positions=[[18,25],[82,25],[18,77],[82,77]];
 return `<section class="panel"><div class="panel-head"><div><h2>算力协同网络 <small>COMPUTE NETWORK</small></h2><div class="panel-sub">节点能力、网络状态与任务时限协同决策</div></div><span class="badge"><i></i>${online.length} 个节点在线</span></div><div class="topology"><div class="network"><svg class="network-lines" viewBox="0 0 100 100" preserveAspectRatio="none">${positions.slice(0,online.length).map(([x,y])=>`<path d="M50 50 C50 ${y},${x} 50,${x} ${y}"/>`).join('')}</svg><div class="network-node center"><div class="node-orb">${icon('nodes')}</div>协调服务<small>联合调度 · 租约恢复</small></div>${online.map((n,i)=>`<div class="network-node ${n.busy_task?'busy':''}" style="left:${positions[i][0]}%;top:${positions[i][1]}%"><div class="node-orb">${icon(n.mode==='same_host'?'monitor':'server')}<span class="node-pulse"></span></div>${esc(n.name)}<small>${n.mode==='same_host'?'同机进程':'远程终端'} · ${ms(n.rtt_ms)}</small></div>`).join('')}</div></div><div class="topology-legend"><span>在线节点</span><span>任务执行中</span><span>HTTP 应用层连接</span></div></section>`;
}
function taskRows(tasks) {
 return tasks.map(t=>`<tr><td><div class="task-name"><span class="task-glyph">${icon('layers')}</span><div>${esc(t.name)}<div class="mono">${esc(t.id)}</div></div></div></td><td>${esc(policies[t.policy])}</td><td>${esc(state.nodes.find(n=>n.id===t.worker_id)?.name||'等待分配')}</td><td><span class="status ${t.status}">${labels[t.status]}</span></td><td>${ms(t.latency_ms)}</td><td><button class="row-button" data-detail="${esc(t.id)}">查看 ↗</button>${['queued','running'].includes(t.status)?` <button class="row-button inline-gap" data-cancel="${esc(t.id)}">取消</button>`:''}</td></tr>`).join('');
}
function taskTable(tasks, title='最近任务', all=false) {
 return `<section class="panel table-panel"><div class="panel-head"><h2>${title} <small>RECENT TASKS</small></h2><div class="table-head-actions"><span class="badge">${all?`显示 ${tasks.length} 项`:'实时更新'}</span>${!all?'<a class="text-link" href="#tasks">全部任务 ↗</a>':''}</div></div><div class="table-wrapper"><table><thead><tr><th>任务名称 / ID</th><th>调度策略</th><th>执行节点</th><th>状态</th><th>完成时延</th><th>操作</th></tr></thead><tbody>${taskRows(tasks)}</tbody></table>${tasks.length?'':`<div class="empty">还没有任务记录<br>创建任务或运行一组真实执行的合成事件。</div>`}</div></section>`;
}
function overview() {
 const online=state.nodes.filter(n=>n.online),busy=online.filter(n=>n.busy_task).length;
 return heading('协同工作台','把任务交给合适的设备，让每一份算力有序协作。')+`<section class="hero"><div><span class="eyebrow">NEARBY DEVICES. SHARED INTELLIGENCE.</span><h2>多端协同，算力就近。</h2><p>感知节点状态，协调 AI 任务，保留每一次执行的依据。</p><div class="hero-tags"><span>时限感知</span><span>通信与计算协同</span><span>可信范围约束</span></div></div>${heroArt()}</section>`+metrics()+`<div class="dashboard-grid"><div>${topology()}${taskTable(state.tasks.slice(0,5))}</div><div class="dashboard-right"><section class="panel strategy"><span class="eyebrow">SCHEDULING POLICY</span><h3>联合调度</h3><p>综合预测等待、通信和计算耗时，优先处理临近截止的任务。</p><div class="strategy-list"><div class="strategy-row"><span>执行时间预测</span><b>在线 EWMA 更新</b></div><div class="strategy-row"><span>数据范围</span><b>按任务约束筛选</b></div><div class="strategy-row"><span>故障处理</span><b>租约到期重新调度</b></div></div><div class="strategy-footer"><span>另有 3 种基线策略</span><a href="#experiments">查看对比 ↗</a></div></section><section class="panel secondary-panel"><h3>工作空间动态</h3><div class="progress-row"><span>节点执行槽占用</span><span>${busy} / ${online.length}</span></div><div class="progress-track"><div class="progress-fill" style="--progress:${online.length?busy/online.length*100:0}%"></div></div><div class="event-list">${state.events.slice(0,4).map(e=>`<div class="event-item"><span>${esc(e.message)}</span><time>${time(e.at)}</time></div>`).join('')||'<p class="fine-label">等待节点接入与任务提交。</p>'}</div><button class="button secondary mini" data-action="demo">${icon('play')} 运行 12 项演示任务</button><p class="fine-label">合成事件，真实进程执行；含附加 CPU 负载。</p></section></div></div>`+notice();
}
function notice() { return `<div class="notice">${icon('info')}<span>${state.measurement==='application_loopback'?'当前为同机多进程演示。':'当前含远程连接，请核实物理终端与网络配置。'}系统使用合成巡检文本与轻量分类模型，尚未验证图像识别、真实用户收益或 6G 无线性能。</span></div>`; }
function nodesPage() {
 return heading('算力节点','连接身边的设备，观察每个执行节点的可用状态。','YOUR COMPUTING NEIGHBORS')+metrics()+`<section class="node-grid">${state.nodes.map(n=>`<article class="panel node-card"><div class="node-header"><span class="metric-icon">${icon(n.mode==='same_host'?'monitor':'server')}</span><div><h3>${esc(n.name)}</h3><p>${esc(n.id)} · ${n.mode==='same_host'?'同机进程':'远程接入'}</p></div></div><div class="node-details"><div><span>当前状态</span>${badge(n)}</div><div><span>主机 / 来源</span><b>${esc(n.host)} / ${esc(n.peer)}</b></div><div><span>HTTP 往返耗时</span><b>${ms(n.rtt_ms)}</b></div><div><span>单位耗时估计</span><b>${ms(n.ms_per_unit)}</b></div><div><span>配置带宽（非测量）</span><b>${fmt(n.bandwidth_mbps)} Mbps</b></div><div><span>累计完成</span><b>${fmt(n.completed)} 项</b></div><div><span>管理员可信设置</span><b>${n.trusted?'可信':'未设为可信'}</b></div></div><div class="node-buttons"><button class="button secondary mini" data-node="${esc(n.id)}" data-drain="${!n.draining}">${n.draining?'恢复接收任务':'暂停接收任务'}</button><button class="button secondary mini" data-trust="${esc(n.id)}" data-value="${!n.trusted}">${n.trusted?'撤销可信设置':'设为可信'}</button></div></article>`).join('')||'<div class="panel empty">暂无节点。使用一键启动脚本，或按部署说明连接执行端。</div>'}</section>`+notice()+`<div class="notice">${icon('shield')}<span>“可信”是管理员对节点的部署设置，不代表硬件证明、加密存储或可信执行环境。远程节点默认不可信。</span></div>`;
}
function tasksPage() {
 const tasks=state.tasks.filter(t=>(taskFilter==='all'||t.status===taskFilter)&&(!taskSearch||`${t.name} ${t.id}`.toLowerCase().includes(taskSearch.toLowerCase())));
 return heading('任务中心','每一次提交、调度与完成，都有可以回溯的执行记录。','TASK ORCHESTRATION')+`<div class="filters"><input id="task-search" aria-label="搜索任务" placeholder="搜索任务名称或 ID" value="${esc(taskSearch)}"><select id="task-filter" aria-label="筛选任务状态"><option value="all">全部状态</option>${Object.entries(labels).map(([s,l])=>`<option value="${s}" ${s===taskFilter?'selected':''}>${l}</option>`).join('')}</select><button class="button secondary" data-action="demo">运行演示任务</button></div>`+taskTable(tasks,'任务记录',true)+`<p class="fine-label">页面显示最近 200 条任务；导出记录包含全部任务。时延使用协调端时钟，避免跨机时钟偏差。</p>`+notice();
}
function experimentPage() {
 if(!simulation?.available) return heading('对比实验','在相同输入与约束下，对比调度策略的适用范围。','REPRODUCIBLE EXPERIMENTS',false)+`<section class="panel empty">尚未加载实验结果。<br>在项目目录运行 <code>python experiments.py</code><br><button class="button secondary" data-action="experiments">加载结果</button></section>`;
 const groups=simulation.groups.filter(g=>g.scenario===scenario), names={normal:'正常网络',congested:'受限带宽',burst:'任务突发'};
 return heading('对比实验','复现输入，保留原始记录，清楚呈现调度策略的适用范围。','REPRODUCIBLE EXPERIMENTS',false)+`<section class="panel experiment-intro"><div><h2>四种策略，同一份任务序列</h2><p>${simulation.seeds} 个随机种子 × ${simulation.tasks_per_seed} 项任务 × 4 种策略 × 3 类条件</p></div><span class="badge warn">离散事件仿真 · 非实测</span></section><div class="scenario-tabs">${Object.entries(names).map(([k,v])=>`<button class="${k===scenario?'active':''}" data-scenario="${k}">${v}</button>`).join('')}</div><section class="panel chart-panel"><h2>按时完成率</h2><p class="muted">当前条件：${names[scenario]}。各随机种子完成率的平均值，全部任务计入分母。</p>${groups.map(g=>`<div class="bar-row ${g.policy==='smart'?'smart':''}"><span>${esc(policies[g.policy] || g.label)}</span><div class="bar-track"><div class="bar-fill" style="--bar:${g.deadline_rate.mean*100}%"></div></div><span class="bar-value">${percent(g.deadline_rate.mean)}</span></div>`).join('')}</section><section class="panel"><div class="panel-head"><h2>指标与不确定性</h2><span class="badge">${simulation.seeds} 次独立任务序列</span></div><div class="table-wrapper"><table><thead><tr><th>策略</th><th>按时率</th><th>约 95% 区间</th><th>平均时延</th><th>各次 P95 均值</th></tr></thead><tbody>${groups.map(g=>`<tr><td>${esc(policies[g.policy] || g.label)}</td><td>${percent(g.deadline_rate.mean)}</td><td>${g.deadline_rate.ci95.map(percent).join(' ～ ')}</td><td>${ms(g.mean_ms.mean)}</td><td>${ms(g.p95_ms.mean)}</td></tr>`).join('')}</tbody></table></div></section><div class="notice">${icon('info')}<span>网络 RTT、带宽和计算时间为假设参数。调度器获知名义服务时间与队列，结果可能优于实际部署。原始 CSV、随机种子与输入哈希保存在 results 目录；不据此推断实际 6G 性能。</span></div>`;
}
function guidePage() {
 return heading('部署与验证','从单机演示，到真实办公室多终端验证。','BUILD YOUR LOCAL COMPUTE NETWORK',false)+`<div class="guides-grid"><section class="panel guide-section"><h2>三步开始协同</h2><div class="step"><span class="step-number">01</span><div><h3>启动协调端</h3><p>安装 Python 3.10 或更高版本，双击 start.bat。默认启动两个同机执行进程，无需安装第三方库。</p><pre>python launch.py</pre></div></div><div class="step"><span class="step-number">02</span><div><h3>提交巡检文本</h3><p>点击“创建任务”，每行输入一个巡检事件。选择策略、数据范围与完成时限，查看执行记录与分类结果。</p><p>“运行演示任务”提交合成文本，并加入单独计时的 CPU 负载。</p></div></div><div class="step"><span class="step-number">03</span><div><h3>接入第二台真实电脑</h3><p>协调端运行局域网模式；将项目和接入令牌以可信方式交给第二台电脑，在执行端启动 worker。</p><pre>python launch.py --host 0.0.0.0<br><br>python -m suanlin.worker --coordinator http://协调端IP:8765 --token-file token.txt --name 办公室节点B</pre><p>远程节点接入后，在节点页核实设备并设为可信，才可接收默认范围的任务。</p></div></div></section><section class="panel guide-section prose"><h2>实验与成果边界</h2><h3>已实现</h3><p>HTTP 节点注册与心跳、真实工作进程执行、任务时限感知、四种分配策略、取消与故障重试、SQLite 持久化、原始 CSV 导出。</p><h3>AI 能力</h3><p>内置字符与双字特征的朴素贝叶斯文本分类器，使用 48 条人工编写的合成样本。四类输出均须人工复核，尚未验证实际巡检准确率。</p><h3>通信指标口径</h3><p>HTTP 往返耗时包含软件处理，不等同无线 RTT。带宽由配置提供，不是自动测量。任务字节统计为 JSON 载荷乘以派发次数，不含 HTTP 头、心跳与 TCP 重传。</p><h3>6G 关联</h3><p>对应 AI 与通信融合、分布式计算和算力协同方向。当前验证层次为 HTTP 应用层原型与参数化仿真，尚未接入 6G 网络。</p><h3>完整材料位置</h3><p>delivery 目录提供场景方案、答辩展示、佐证材料和演示视频；docs 目录保存可修改的正文、讲稿、测试和提交指南。</p><pre>python -m unittest discover -s tests -v<br>python experiments.py</pre></section></div>`;
}
function render() {
 if (!state) return;
 const focused=document.activeElement?.id, cursor=document.activeElement?.selectionStart;
 $('#content').innerHTML=({overview,nodes:nodesPage,tasks:tasksPage,experiments:experimentPage,guide:guidePage})[current]();
 if(focused==='task-search') { $('#task-search')?.focus(); $('#task-search')?.setSelectionRange(cursor,cursor); }
 $('#breadcrumb').textContent=titles[current];
 $('#node-count').textContent=state.nodes.filter(n=>n.online).length;
 document.querySelectorAll('[data-page]').forEach(a=>a.classList.toggle('active',a.dataset.page===current));
}
async function refresh(force=false) {
 if(fetching||document.hidden&&!force) return;
 fetching=true;
 try { state=await api('/api/status'); $('#connection').innerHTML='<i></i>协调服务在线'; $('#connection').classList.remove('error'); lastError=false; if(!simulation) simulation=await api('/api/experiments'); render(); }
 catch(error) { $('#connection').innerHTML='<i></i>连接中断'; $('#connection').classList.add('error'); if(!lastError&&state) toast(error.message); lastError=true; }
 finally { fetching=false; }
}
async function detail(id) {
 const t=await api('/api/tasks/'+id);
 $('#detail-content').innerHTML=`<p class="muted">${esc(t.name)} <span class="mono">${esc(t.id)}</span></p><div class="detail-meta"><span>执行状态<b>${labels[t.status]}</b></span><span>完成时延<b>${ms(t.latency_ms)}</b></span><span>调度策略<b>${esc(policies[t.policy])}</b></span><span>派发尝试<b>${t.attempts} 次</b></span><span>计算耗时<b>${ms(t.result?.compute_ms)}</b></span><span>其中附加负载<b>${ms(t.result?.load_ms)}</b></span></div>${t.result?.predictions?.map(p=>`<div class="prediction"><div class="prediction-title"><b>${esc(p.label)}</b><span>模型分数 ${percent(p.confidence)}</span></div><p>${esc(p.text)}</p></div>`).join('')||`<p class="muted">${esc(t.error||'等待执行结果。')}</p>`}<p class="form-note">合成文本训练的轻量模型。分数未经概率校准，所有结果均需人工复核。完整结果保存在本地 SQLite 数据库。</p>`;
 $('#detail-dialog').showModal();
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('button');
 if(!button) return;
 try {
  const action=button.dataset.action;
  if(action==='create') $('#create-dialog').showModal();
  if(action==='close-dialog') $('#create-dialog').close();
  if(action==='close-detail') $('#detail-dialog').close();
  if(action==='export') { const response=await fetch('/api/tasks/export.csv'); if(!response.ok) throw new Error('记录导出失败，请检查登录状态'); const url=URL.createObjectURL(await response.blob()); const a=document.createElement('a');a.href=url;a.download='任务调度记录.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);toast('任务记录已导出'); }
  if(action==='demo') { button.disabled=true; await api('/api/demo',{count:12,policy:'smart'});toast('12 项合成事件已提交，正在真实执行');await refresh(true); }
  if(action==='experiments') {simulation=await api('/api/experiments');render();}
  if(button.dataset.detail) await detail(button.dataset.detail);
  if(button.dataset.cancel) {await api('/api/tasks/'+button.dataset.cancel+'/cancel',{});toast('任务已取消');await refresh(true);}
  if(button.dataset.node) {await api('/api/nodes/'+button.dataset.node,{draining:button.dataset.drain==='true'});await refresh(true);toast('节点接收状态已更新');}
  if(button.dataset.trust) {await api('/api/nodes/'+button.dataset.trust,{trusted:button.dataset.value==='true'});await refresh(true);toast('节点可信范围已更新');}
  if(button.dataset.scenario) {scenario=button.dataset.scenario;render();}
 } catch(error) {toast(error.message);} finally {button.disabled=false;}
});
$('#task-form').addEventListener('submit',async event=>{
 event.preventDefault(); const f=new FormData(event.target), button=$('button[type="submit"]',event.target);button.disabled=true;
 try { await api('/api/tasks',{name:f.get('name'),events:f.get('events').split('\n').map(x=>x.trim()).filter(Boolean),policy:f.get('policy'),privacy:f.get('privacy'),deadline_ms:Number(f.get('deadline_ms')),compute_loops:Number(f.get('compute_loops'))});$('#create-dialog').close();toast('任务已提交');await refresh(true); }
 catch(error) {toast(error.message);}finally{button.disabled=false;}
});
$('#login-form').addEventListener('submit',async event=>{event.preventDefault();try{await api('/api/login',{token:new FormData(event.target).get('token')});$('#login-dialog').close();event.target.reset();await refresh(true);}catch(error){toast(error.message);}});
document.addEventListener('input',event=>{if(event.target.id==='task-search'){taskSearch=event.target.value;render();}});
document.addEventListener('change',event=>{if(event.target.id==='task-filter'){taskFilter=event.target.value;render();}});
function navigate() { current=location.hash.slice(1) in titles?location.hash.slice(1):'overview';render();window.scrollTo({top:0}); }
window.addEventListener('hashchange',navigate);
setInterval(()=>$('#clock').textContent=new Date().toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'})+' · '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}),1000);
navigate();refresh(true);setInterval(refresh,1500);
