'use strict';

/* Keep live UI elements stable, so polling does not restart motion or interrupt input. */
const InterfaceMotion = (() => {
  const root = document.documentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)');
  const finite = new Set();
  const numbers = new Map();
  const seen = new WeakSet();
  const statuses = new WeakMap();
  let enabled = true, route = null, pointerFrame = 0, pointer = null;

  function animate(element, frames, options) {
    if (!enabled || document.hidden || !element.animate) return null;
    const animation = element.animate(frames, options);
    finite.add(animation);
    animation.finished.then(() => finite.delete(animation), () => finite.delete(animation));
    return animation;
  }

  function numberText(value, kind) {
    if (!Number.isFinite(value)) return '—';
    if (kind === 'percent') return value.toFixed(1) + '%';
    return value.toLocaleString('zh-CN', {maximumFractionDigits: kind === 'integer' ? 0 : 1});
  }

  function finishNumbers() {
    for (const [element, item] of numbers) {
      cancelAnimationFrame(item.frame);
      element.textContent = numberText(item.target, item.kind);
      item.value = item.target;
      item.frame = 0;
      if (!element.isConnected) numbers.delete(element);
    }
  }

  function syncPreference() {
    enabled = !reduced.matches;
    root.dataset.motion = enabled ? 'on' : 'off';
    root.dataset.motionPaused = document.hidden ? 'true' : 'false';
    if (!enabled || document.hidden) {
      for (const animation of finite) animation.cancel();
      finite.clear();
      finishNumbers();
      document.querySelectorAll('.fx-ripple').forEach(element => element.remove());
    }
  }

  reduced.addEventListener('change', syncPreference);
  document.addEventListener('visibilitychange', syncPreference);
  syncPreference();

  const key = node => node.nodeType === 1 ? (node.getAttribute('data-key') || node.id || '') : '';
  const sameType = (a, b) => a.nodeType === b.nodeType && (a.nodeType !== 1 || a.tagName === b.tagName);

  function patchAttributes(element, template) {
    for (const attr of [...element.attributes]) {
      if (element.tagName === 'DETAILS' && attr.name === 'open') continue;
      if (attr.name !== 'style' && !template.hasAttribute(attr.name)) element.removeAttribute(attr.name);
    }
    for (const attr of template.attributes) {
      if (attr.name !== 'style' && element.getAttribute(attr.name) !== attr.value) element.setAttribute(attr.name, attr.value);
    }
    // Pointer lighting uses local properties; preserve them during a data refresh.
    for (const name of [...element.style]) {
      if (!name.startsWith('--fx-') && !template.style.getPropertyValue(name)) element.style.removeProperty(name);
    }
    for (const name of [...template.style]) {
      const value = template.style.getPropertyValue(name);
      if (element.style.getPropertyValue(name) !== value) element.style.setProperty(name, value, template.style.getPropertyPriority(name));
    }
  }

  function patchNode(element, template) {
    if (element.nodeType === 3 || element.nodeType === 8) {
      if (element.nodeValue !== template.nodeValue) element.nodeValue = template.nodeValue;
      return;
    }
    patchAttributes(element, template);
    // Animated numbers own their text; their exact target remains in the accessible label.
    if (!element.classList.contains('metric-number')) patchChildren(element, template);
    if (element !== document.activeElement && (element.tagName === 'INPUT' || element.tagName === 'SELECT')) {
      if (element.value !== template.value) element.value = template.value;
    }
  }

  function patchChildren(parent, template) {
    const keyed = new Map([...parent.childNodes].filter(node => key(node)).map(node => [key(node), node]));
    let cursor = parent.firstChild;
    for (const next of [...template.childNodes]) {
      const id = key(next);
      let element = id ? keyed.get(id) : (cursor && !key(cursor) && sameType(cursor, next) ? cursor : null);
      if (element && !sameType(element, next)) element = null;
      if (!element) {
        element = next.cloneNode(true);
        parent.insertBefore(element, cursor);
      } else {
        if (element !== cursor) parent.insertBefore(element, cursor);
        patchNode(element, next);
      }
      cursor = element.nextSibling;
    }
    while (cursor) {
      const extra = cursor;
      cursor = cursor.nextSibling;
      extra.remove();
    }
  }

  function updateNumbers(container) {
    for (const [element, item] of numbers) {
      if (!element.isConnected) { cancelAnimationFrame(item.frame); numbers.delete(element); }
    }
    container.querySelectorAll('.metric-number').forEach(element => {
      const target = element.dataset.value === '' ? NaN : Number(element.dataset.value);
      const kind = element.dataset.format;
      const previous = numbers.get(element);
      if (previous && Object.is(previous.target, target) && previous.kind === kind) return;
      if (previous) cancelAnimationFrame(previous.frame);
      const start = previous && Number.isFinite(previous.value) ? previous.value : 0;
      const item = {target, kind, value: start, frame: 0};
      numbers.set(element, item);
      if (!enabled || document.hidden || !Number.isFinite(target) || start === target) {
        item.value = target;
        element.textContent = numberText(target, kind);
        return;
      }
      const began = performance.now();
      const step = now => {
        if (!element.isConnected) { numbers.delete(element); return; }
        const progress = Math.min(1, (now - began) / 680);
        item.value = start + (target - start) * (1 - Math.pow(1 - progress, 3));
        element.textContent = numberText(item.value, kind);
        item.frame = progress < 1 ? requestAnimationFrame(step) : 0;
        if (progress === 1) { item.value = target; element.textContent = numberText(target, kind); }
      };
      element.textContent = numberText(start, kind);
      item.frame = requestAnimationFrame(step);
    });
  }

  function reveal(element, delay = 0) {
    seen.add(element);
    if (element.classList.contains('bar-fill')) {
      animate(element, [{transform:'scaleX(0)'}, {transform:'scaleX(1)'}], {duration:850,delay,easing:'cubic-bezier(.2,.7,.2,1)',fill:'backwards'});
    } else {
      animate(element, [{opacity:0,transform:'translateY(18px)'}, {opacity:1,transform:'translateY(0)'}], {duration:560,delay,easing:'cubic-bezier(.2,.7,.2,1)',fill:'backwards'});
    }
  }

  const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    let index = 0;
    for (const entry of entries) {
      if (entry.isIntersecting) {
        observer.unobserve(entry.target);
        if (!seen.has(entry.target)) reveal(entry.target, Math.min(index++ * 65, 195));
      }
    }
  }, {threshold:.06}) : null;

  let flowSurface = null;
  const flowVisibility = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    for (const entry of entries) entry.target.style.setProperty('--fx-topology-play', entry.isIntersecting ? 'running' : 'paused');
  }) : null;

  function decorate(container) {
    const nextSurface = container.querySelector('.business-topology');
    if (nextSurface !== flowSurface) {
      if (flowSurface) flowVisibility?.unobserve(flowSurface);
      flowSurface = nextSurface;
      if (flowSurface) {
        if (flowVisibility) flowVisibility.observe(flowSurface);
        else flowSurface.style.setProperty('--fx-topology-play', 'running');
      }
    }
    observer?.disconnect();
    let index = 0;
    container.querySelectorAll('.page-heading,.hero,.metric,.panel,.notice,.bar-fill').forEach(element => {
      if (seen.has(element)) return;
      const rect = element.getBoundingClientRect();
      if (rect.top < innerHeight && rect.bottom > 0 || !observer || !enabled) {
        reveal(element, Math.min(index++ * 55, 220));
      } else {
        observer.observe(element);
      }
    });
    container.querySelectorAll('.status').forEach(element => {
      const value = element.textContent;
      const previous = statuses.get(element);
      if (previous && previous !== value) animate(element, [{boxShadow:'0 0 0 5px #7ba5ff44'}, {boxShadow:'0 0 0 0 #7ba5ff00'}], {duration:700,easing:'ease-out'});
      statuses.set(element, value);
    });
  }

  function render(container, html, page) {
    const template = document.createElement('template');
    template.innerHTML = html;
    if (route !== page) {
      observer?.disconnect();
      for (const animation of finite) animation.cancel();
      finite.clear();
      container.replaceChildren(template.content);
      route = page;
    } else {
      patchChildren(container, template.content);
    }
    updateNumbers(container);
    decorate(container);
  }

  document.addEventListener('pointermove', event => {
    if (!enabled || !finePointer.matches || document.hidden) return;
    const card = event.target.closest('.hero,.metric,.node-card,.strategy');
    if (!card) return;
    pointer = {card, x:event.clientX, y:event.clientY};
    if (pointerFrame) return;
    pointerFrame = requestAnimationFrame(() => {
      pointerFrame = 0;
      if (!enabled || !pointer.card.isConnected) return;
      const {card,x,y} = pointer, box = card.getBoundingClientRect();
      card.style.setProperty('--fx-x', (x - box.left) + 'px');
      card.style.setProperty('--fx-y', (y - box.top) + 'px');
      if (card.classList.contains('hero')) {
        card.style.setProperty('--fx-shift-x', ((x - box.left) / box.width - .5) * 10 + 'px');
        card.style.setProperty('--fx-shift-y', ((y - box.top) / box.height - .5) * 8 + 'px');
      }
    });
  }, {passive:true});

  document.addEventListener('click', event => {
    const button = event.target.closest('.button,.hero-action,.scenario-tabs button');
    if (!enabled || !button || button.disabled) return;
    const box = button.getBoundingClientRect(), size = Math.max(box.width, box.height) * 2;
    const ripple = document.createElement('span');
    ripple.className = 'fx-ripple';
    ripple.setAttribute('aria-hidden','true');
    const x = event.detail ? event.clientX - box.left : box.width / 2;
    const y = event.detail ? event.clientY - box.top : box.height / 2;
    ripple.style.cssText = 'width:'+size+'px;height:'+size+'px;left:'+(x-size/2)+'px;top:'+(y-size/2)+'px';
    button.append(ripple);
    const animation = animate(ripple, [{transform:'scale(0)',opacity:.22}, {transform:'scale(1)',opacity:0}], {duration:620,easing:'ease-out'});
    if (animation) animation.finished.then(() => ripple.remove(), () => ripple.remove());
    else ripple.remove();
  });

  return {render, update(container, html) {
    const template = document.createElement('template');
    template.innerHTML = html;
    patchChildren(container, template.content);
  }};
})();

const $ = (s, root = document) => root.querySelector(s);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',nodes:'M8 3h8v6H8z M2 15h8v6H2z M14 15h8v6h-8z M12 9v3 M6 15v-3h12v3',layers:'m12 3 10 5-10 5L2 8z M2 12l10 5 10-5 M2 16l10 5 10-5',chart:'M4 3v18h18 M8 16v-4 M13 16V8 M18 16V5',book:'M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3z M12 6v16',code:'m8 7-5 5 5 5 M16 7l5 5-5 5 M14 3l-4 18',plus:'M12 5v14 M5 12h14',arrow:'M5 19 19 5 M6 5h13v13',clock:'M12 8v5l4 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',check:'m5 12 4 4L19 6',activity:'M2 12h5l3-8 4 16 3-8h5',server:'M4 3h16v7H4z M4 14h16v7H4z M7 7h1 M7 18h1 M12 7h5 M12 18h5',monitor:'M3 3h18v13H3z M12 16v5 M8 21h8',shield:'m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6z m-4 10 3 3 5-6',download:'M12 3v12 m-5-5 5 5 5-5 M4 17v4h16v-4',info:'M12 10v7 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',play:'m8 4 12 8-12 8z'};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.grid}"/></svg>`;
document.querySelectorAll('[data-icon]').forEach(e => e.innerHTML = icon(e.dataset.icon));
const titles = {overview:'协同工作台',nodes:'算力节点',tasks:'任务中心',experiments:'对比实验',guide:'部署与验证'};
const labels = {completed:'已完成',running:'执行中',queued:'待调度',failed:'执行失败',cancelled:'已取消'};
const policies = {ai:'Qwen AI 调度',smart:'联合调度',fixed:'固定单节点',round_robin:'轮询分配',random:'随机分配'};
let state = null, current = 'overview', simulation = null, scenario = 'normal', taskFilter = 'all', taskSearch = '', lastError = false, fetching = false;
let toastTimer;
let taskSort = 'newest', lastSyncAt = null, connectionError = '', authRequired = false, experimentsError = '', experimentsLoading = false;
let activeTask = null, detailVersion = 0, detailLoading = false, detailError = '';
let lastExampleId = null;
let selectedTopologyNode = null, guideStep = 1;
const taskKinds = {log_analysis:'业务日志分析',document_search:'文档检索与排序',archive:'文件脱敏打包'};
const pending = new Set();
function toast(text) { $('#toast').textContent = text; $('#toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 3500); }
async function request(path, data, raw = false) {
 const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 12000);
 try {
  const response = await fetch(path, {signal:controller.signal, ...(data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})});
  if (response.status === 401) {
   if (!$('#login-dialog').open) $('#login-dialog').showModal();
   const body = await response.json().catch(()=>({}));
   const error = new Error(path==='/api/login'?(body.error||'接入令牌不正确。'):'登录已失效，请重新输入接入令牌。'); error.status = 401; throw error;
  }
  if (raw && response.ok) return await response.blob();
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求失败，请稍后重试。');
  return result;
 } catch (error) {
  if (error.name === 'AbortError' || error instanceof TypeError) {
   throw new Error(data === undefined ? '暂时无法连接协调服务，请检查服务是否运行。' : '未能确认操作结果，请恢复连接后先查看记录，再决定是否重试。');
  }
  throw error;
 } finally { clearTimeout(timer); }
}
const api = (path, data) => request(path, data);

const fmt = (x, digits=0) => x == null ? '—' : Number(x).toLocaleString('zh-CN', {maximumFractionDigits:digits});
const ms = x => x == null ? '—' : `${fmt(x,1)} ms`;
const percent = x => x == null ? '—' : `${(x*100).toFixed(1)}%`;
const time = x => new Date(x*1000).toLocaleTimeString('zh-CN',{hour12:false});
function badge(node) { return `<span class="badge ${!node.online?'offline':node.draining?'warn':node.busy_task?'busy':''}"><i></i>${!node.online?'已离线':node.draining?'已暂停':node.busy_task?'执行中':'在线空闲'}</span>`; }
function heading(title,desc='',actions=true) { return `<section class="page-heading"><div><h1>${title}</h1>${desc?`<p>${desc}</p>`:''}</div>${actions?`<div class="heading-actions"><button class="button secondary" data-action="export">${icon('download')} 导出记录</button><button class="button primary" data-action="create">${icon('plus')} 创建任务</button></div>`:''}</section>`; }
function metrics() {
 const active=state.nodes.filter(n=>n.online),m=state.metrics;
 const values=[active.length,m.completed,m.mean_ms,m.deadline_rate==null?null:m.deadline_rate*100],formats=['integer','integer','decimal','percent'];
 const cards=[['nodes','在线节点',active.length,'个',`${state.nodes.filter(n=>n.online&&n.mode==='remote_host').length} 个远程终端`,'实时节点心跳'],['layers','已完成任务',m.completed,'项',`${state.counts.running} 项正在执行`,`${state.counts.queued} 项等待调度`],['clock','平均耗时',m.mean_ms==null?'—':fmt(m.mean_ms,1),'ms','服务端提交至结果接收','应用层指标 · 非无线时延'],['shield','按时完成率',percent(m.deadline_rate),'',`${m.total} 项已提交任务`,'待完成与失败均计入分母']];
 return `<section class="metrics" aria-label="实时指标">${cards.map((c,i)=>`<article data-key="metric-${i}" data-tone="${['blue','violet','amber','rose'][i]}" class="metric ${i===3?'highlight':''}"><div class="metric-top">${c[1]}<span class="metric-icon">${icon(c[0])}</span></div><div class="metric-value" aria-label="${esc(c[2]+' '+c[3])}"><span class="metric-number" aria-hidden="true" data-value="${values[i]??''}" data-format="${formats[i]}">${c[2]}</span><small aria-hidden="true">${c[3]}</small></div><div class="metric-foot"><b>${c[4]}</b></div></article>`).join('')}</section>`;
}
function heroArt() {
 return `<div class="hero-art" aria-hidden="true"><svg viewBox="0 0 340 180" fill="none"><defs><pattern id="hero-grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" stroke="#7b9bce" stroke-opacity=".1" stroke-width=".6"/></pattern><linearGradient id="chip-fill" x1="143" y1="46" x2="204" y2="112" gradientUnits="userSpaceOnUse"><stop stop-color="#365d9c"/><stop offset="1" stop-color="#1d355a"/></linearGradient></defs><rect x="10" y="4" width="320" height="166" fill="url(#hero-grid)"/><path d="M60 54H111L156 87M279 47H241L193 87M278 136H239L192 103M61 134H110L156 103" stroke="#759bdb" stroke-opacity=".5" stroke-width="1.2"/><path class="hero-flow" d="M60 54H111L156 87M279 47H241L193 87M278 136H239L192 103" stroke="#8eb8ff" stroke-opacity=".7" stroke-width="1.2" stroke-dasharray="3 18"/><path d="m143 63 31-18 32 18v45l-32 19-31-19z" fill="#6f9fe0" fill-opacity=".08" stroke="#6494dd" stroke-opacity=".3"/><circle class="hero-chip-ring" cx="174" cy="87" r="39" stroke="#8eb5fa" stroke-width=".7"/><rect class="hero-chip" x="148" y="61" width="52" height="52" rx="12" fill="url(#chip-fill)" stroke="#91b7f1" stroke-opacity=".7"/><path d="M165 77h18v19h-18zM160 82h5m18 0h5m-28 9h5m18 0h5m-18-19v5m8-5v5m-8 19v5m8-5v5" stroke="#b5d2ff" stroke-width="1.6"/><g fill="#1a2a44" stroke="#5b79a8" stroke-opacity=".7"><rect x="38" y="34" width="42" height="36" rx="8"/><rect x="258" y="27" width="42" height="36" rx="8"/><rect x="258" y="118" width="42" height="36" rx="8"/><rect x="38" y="117" width="42" height="36" rx="8"/></g><g stroke="#a2c0ef" stroke-width="1.2"><path d="M49 44h20v13H49zm10 13v4m-5 0h10M269 37h20v5h-20zm0 9h20v5h-20zM269 128h20v13h-20zm10 13v4m-5 0h10M49 127h20v5H49zm0 9h20v5H49z"/></g><g class="hero-sparks" fill="#8fb5f7"><circle cx="110" cy="54" r="2.5"/><circle cx="240" cy="136" r="2.5"/><circle cx="300" cy="45" r="2"/></g><g fill="#849fc7" font-family="Consolas,monospace" font-size="7" letter-spacing="1.2"><text x="136" y="145">COORDINATOR</text><text x="36" y="21">EXECUTION NODES</text></g></svg></div>`;
}

function topologyTasks(node) {
 const reported=state.topology?.node_tasks?.[node.id];
 if(reported) return reported;
 const tasks=state.tasks.filter(t=>t.worker_id===node.id);
 return {running:tasks.filter(t=>t.status==='running'),recent:tasks.filter(t=>t.status==='completed').sort((a,b)=>(b.finished_at||0)-(a.finished_at||0)).slice(0,3)};
}
function taskStage(task,node) {
 const p=task.progress;
 if(!p||!Number.isFinite(p.done)||!Number.isFinite(p.total)||p.total<=0) return {label:'处理中 · 等待进度上报',value:null,stale:!node.online};
 const value=Math.max(0,Math.min(100,p.done/p.total*100));
 const stages={compute:'附加 CPU 负载',log_analysis:'日志分析',build_index:'建立索引',search:'本轮检索',archive:'脱敏压缩',complete:'计算结束，等待结果接收'};
 const age=Math.max(0,Math.floor(state.server_time-(p.at||0))),stale=!node.online||age>15;
 return {value,stale,label:`${stages[p.stage]||'执行阶段'} · ${fmt(value,1)}%${stale?' · 最近上报已过期':''}`,detail:`${fmt(p.done)} / ${fmt(p.total)} · ${age} 秒前上报`};
}
function topologyTaskPanel(node) {
 const {running,recent}=topologyTasks(node),active=Object.keys(node.active_tasks||{}).length;
 return `<div class="topology-task-panel" id="topology-task-panel" data-key="topology-panel-${esc(node.id)}"><div class="topology-selection"><div><h3>${esc(node.name)}</h3><p>${node.mode==='same_host'?'同机进程':'远程终端'} · 槽位 ${active} / ${node.capacity||1}</p></div>${badge(node)}</div>${!node.online?'<p class="topology-warning">节点离线，执行状态等待确认；租约到期后由系统重排。</p>':''}<h4>正在处理 · ${running.length} 项</h4>${running.length?`<ul class="topology-task-list">${running.map(t=>{const progress=taskStage(t,node);return `<li data-key="live-task-${esc(t.id)}"><div class="topology-task-title"><button class="text-button" data-detail="${esc(t.id)}">${esc(t.name)}</button><span class="mono">${esc(t.id)}</span></div><p>${esc(progress.label)}</p>${progress.value==null?'':`<progress max="100" value="${progress.value}" aria-label="${esc(t.name)}当前阶段进度" class="${progress.stale?'is-stale':''}"></progress><small>${esc(progress.detail)}</small>`}</li>`;}).join('')}</ul>`:`<p class="topology-empty">${active?'执行槽仍被占用，正在等待清理或状态回报。':node.online?'当前没有执行中的任务。':'当前没有可确认的运行任务。'}</p>`}<h4>最近完成</h4>${recent.length?`<ul class="topology-results">${recent.map(t=>`<li data-key="node-result-${esc(t.id)}"><div><b>${esc(t.name)}</b><small>${fullTime(t.finished_at)} · ${ms(t.latency_ms)}</small></div><button class="row-button" data-detail="${esc(t.id)}">查看结果</button></li>`).join('')}</ul>`:'<p class="topology-empty">完成任务后可查看结果。</p>'}</div>`;
}
function topology() {
 const nodes=[...state.nodes].sort((a,b)=>Number(b.online)-Number(a.online)||a.id.localeCompare(b.id)),allOnline=nodes.filter(n=>n.online);
 const selected=nodes.find(n=>n.id===selectedTopologyNode)||nodes[0],visible=nodes.slice(0,4),positions=visible.length===1?[[82,50]]:visible.length===2?[[14,50],[86,50]]:visible.length===3?[[14,20],[86,20],[50,76]]:[[18,18],[82,18],[18,68],[82,68]];
 if(selected&&!visible.some(n=>n.id===selected.id)) visible[visible.length-1]=selected;
 const linkState=n=>{const data=topologyTasks(n);if(!n.online)return 'is-offline';if(data.running.length)return 'is-running';const at=data.recent[0]?.finished_at;return at!=null&&state.server_time-at>=0&&state.server_time-at<8?'is-returning':'';};
 return `<section class="panel business-topology" aria-labelledby="topology-title"><div class="panel-head"><div><h2 id="topology-title">算力协同网络</h2><div class="panel-sub">点击节点，查看任务和结果。</div></div><span class="badge"><i></i>${allOnline.length} 个节点在线</span></div><ol class="topology-workflow" aria-label="真实任务处理流程"><li><b>接收任务</b><span>待调度 ${state.counts.queued} 项</span></li><li><b>选择节点</b><span>${state.ai_scheduler?.enabled?'Qwen 辅助 / 规则':'规则联合调度'}</span></li><li><b>执行处理</b><span>执行中 ${state.counts.running} 项</span></li><li><b>汇总结果</b><span>已完成 ${state.counts.completed} 项</span></li></ol>${nodes.length>4?`<div class="topology-more">图中展示 ${visible.length} / ${nodes.length} 个已登记节点，可在下方切换或<a href="#nodes">查看全部节点 ↗</a></div>`:''}<div class="topology-body"><div class="topology-map-area"><div class="topology"><div class="network"><svg class="network-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${visible.map((n,i)=>{const [x,y]=positions[i];return `<path data-key="link-${esc(n.id)}" class="topology-link ${linkState(n)}" d="M50 50 C50 ${y},${x} 50,${x} ${y}"/><path class="topology-stream" data-flow="${linkState(n)}" pathLength="100" d="M50 50 C50 ${y},${x} 50,${x} ${y}"/>`;}).join('')}</svg><div class="network-node center"><div class="node-orb">${icon('nodes')}</div><strong>协调端</strong><small>接收 · 调度 · 汇总</small></div>${visible.map((n,i)=>{const data=topologyTasks(n),first=data.running[0],last=data.recent[0],stage=first?taskStage(first,n):null;return `<div data-key="network-${esc(n.id)}" class="network-node ${data.running.length&&n.online?'busy':''} ${!n.online?'is-offline':''} ${n.id===selected?.id?'is-selected':''}" style="left:${positions[i][0]}%;top:${positions[i][1]}%"><button type="button" class="node-orb" data-topology-node="${esc(n.id)}" aria-label="查看${esc(n.name)}的任务" aria-pressed="${n.id===selected?.id}" aria-controls="topology-task-panel">${icon(n.mode==='same_host'?'monitor':'server')}<span class="node-pulse"></span></button><strong class="topology-node-name" title="${esc(n.name)}">${esc(n.name)}</strong><small>${!n.online?'已离线':n.draining?'已暂停接收':data.running.length?'执行中 '+data.running.length+' 项':'在线空闲'} · ${n.mode==='same_host'?'同机进程':'远程终端'}</small>${first?`<span class="topology-node-task" title="${esc(first.name)}">${esc(first.name)}</span><small class="topology-node-progress">${esc(stage.value==null?'等待进度上报':(stage.stale?'最近阶段 ':'当前阶段 ')+fmt(stage.value,1)+'%')}</small>`:last?`<button type="button" class="topology-result-link" data-detail="${esc(last.id)}">查看最近结果 ↗</button>`:''}</div>`;}).join('')}</div>${nodes.length?'':'<p class="topology-no-nodes">尚无执行节点。<a href="#guide">查看启动步骤 ↗</a></p>'}</div><div class="topology-legend"><span>在线连接</span><span>任务执行中</span><span>近期结果接收</span></div></div><aside class="topology-inspector">${selected?`<div class="topology-node-picker"><label for="topology-node-select">查看执行节点</label><select id="topology-node-select">${nodes.map(n=>`<option value="${esc(n.id)}" ${n.id===selected.id?'selected':''}>${esc(n.name)}${n.online?'':'（离线）'}</option>`).join('')}</select></div>${topologyTaskPanel(selected)}`:''}</aside></div><details class="visual-notes topology-notes"><summary>进度与动效口径</summary><p>进度来自心跳，仅表示当前阶段；短任务可能直接完成。连线表示 HTTP 应用关联，动效表示执行中或近 8 秒收到结果，不是实时流量测量。</p></details></section>`;
}
function statusLabel(status) {
 const symbol={completed:'check',running:'play',queued:'clock',failed:'info',cancelled:'layers'}[status]||'info';
 return icon(symbol)+labels[status];
}
function taskRows(tasks) {
 return tasks.map(t=>`<tr role="row" data-key="task-${esc(t.id)}" data-status="${esc(t.status)}">
 <td role="cell" class="task-cell-name" data-label="任务名称 / ID"><div class="task-name"><span class="task-glyph">${icon('layers')}</span><div>${esc(t.name)}<div class="mono">${esc(t.id)}</div></div></div></td>
 <td role="cell" data-label="调度策略">${esc(policies[t.policy])}</td>
 <td role="cell" data-label="执行节点">${esc(state.nodes.find(n=>n.id===t.worker_id)?.name||t.worker_id||(['queued','running'].includes(t.status)?'等待分配':'未分配'))}</td>
 <td role="cell" data-label="状态"><span class="status ${t.status}">${statusLabel(t.status)}</span></td>
 <td role="cell" data-label="完成时延">${ms(t.latency_ms)}</td>
 <td role="cell" class="task-cell-actions" data-label="操作"><button class="row-button" data-detail="${esc(t.id)}">查看详情</button>${['queued','running'].includes(t.status)?` <button class="row-button cancel-button" data-cancel="${esc(t.id)}">取消任务</button>`:''}</td></tr>`).join('');
}
function taskTable(tasks, title='最近任务', all=false) {
 return `<section class="panel table-panel task-table-panel"><div class="panel-head"><h2>${title}</h2><div class="table-head-actions"><span class="badge">${all?`显示 ${tasks.length} 项`:'实时更新'}</span>${!all?'<a class="text-link" href="#tasks">全部任务 ↗</a>':''}</div></div><div class="table-wrapper task-table-wrapper"><table class="task-table" role="table" aria-label="${title}"><thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">任务名称 / ID</th><th scope="col" role="columnheader">调度策略</th><th scope="col" role="columnheader">执行节点</th><th scope="col" role="columnheader">状态</th><th scope="col" role="columnheader">完成时延</th><th scope="col" role="columnheader">操作</th></tr></thead><tbody role="rowgroup">${taskRows(tasks)}</tbody></table>${tasks.length?'':emptyTasks(all)}</div></section>`;
}
function exampleNodes() {
 return state?.nodes.filter(n=>n.online&&!n.draining&&n.trusted)||[];
}
function overview() {
 const online=state.nodes.filter(node=>node.online);
 const busy=online.reduce((sum,node)=>sum+Object.keys(node.active_tasks||{}).length,0);
 const slots=online.reduce((sum,node)=>sum+(node.capacity||1),0);
 const ready=exampleNodes().length>0;
 const example=state.tasks.find(task=>task.id===lastExampleId);
 const recent=state.events.slice(0,3).map(event=>`<div class="event-item" data-key="event-${esc(event.at+'-'+event.message)}"><span>${esc(event.message)}</span><time>${time(event.at)}</time></div>`).join('');
 const uses=[['inspection','整理巡检记录','把现场记录变成待办清单','check'],
  ['log_analysis','从日志找故障','定位出错服务和异常原行','activity'],
  ['document_search','从文档找答案','用关键词找到原文和行号','book'],
  ['archive','分享文件前先脱敏','遮盖敏感内容，下载处理后的副本','shield']];
 const templates=uses.map(([kind,label,detail,symbol])=>`<button type="button" data-template="${kind}">
  <span class="work-use-icon">${icon(symbol)}</span><span><b>${label}</b><small>${detail}</small></span>${icon('arrow')}
 </button>`).join('');
 return heading('协同工作台')+
 `<section class="hero"><div class="hero-grid-motion" aria-hidden="true"></div><div class="hero-scan" aria-hidden="true"></div><div class="hero-particles" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div>
  <div class="hero-copy"><h2>把任务交给合适的电脑</h2><p>试着分类一句话，看看是哪台电脑处理的。</p>
   <div class="hero-bottom">
    <button id="tour-example" class="button primary" type="button" data-action="try-example" ${ready?'':'disabled'}>试跑一条示例</button>
    <button class="hero-action" type="button" data-action="demo">批量演示 ${icon('arrow')}</button>
    ${example?`<button class="hero-action" type="button" data-detail="${esc(example.id)}">查看上次示例</button>`:''}
   </div>
   ${ready?'':'<a class="hero-readiness" href="#nodes">暂无可接收任务的电脑 · 查看节点 ↗</a>'}
  </div>${heroArt()}
 </section>`+`<section class="workload-choices" aria-labelledby="other-tasks-title"><h2 id="other-tasks-title">用自己的数据做什么</h2><div>${templates}</div></section>`+metrics()+topology()+
 `<div class="dashboard-grid">
  <div>${taskTable(state.tasks.slice(0,5))}</div>
  <section class="panel secondary-panel dashboard-activity"><h3>最近动态</h3><div class="progress-row"><span>执行槽占用</span><span>${busy} / ${slots}</span></div><div class="progress-track"><div class="progress-fill" style="--progress:${slots?busy/slots*100:0}%"></div></div><div class="event-list">${recent||'<p class="fine-label">暂无活动</p>'}</div></section>
 </div>`+notice();
}
function notice() {
 return `<details class="scope-notes"><summary>${icon('info')}环境与指标口径</summary><p>${state.measurement==='application_loopback'?'当前为同机进程演示。':'当前包含远程节点，请核实物理设备。'}业务演示采用合成数据，当前未接入 6G。任务完成不等于结果正确性验收。</p><p>HTTP 往返包含软件处理；配置带宽未经测量。完成时延按协调端提交至接收结果计算；按时完成率以全部提交任务为分母，包含未完成和失败任务。</p></details>`;
}
function networkRoadmap() {
 return `<section class="panel network-roadmap"><h2>本项目如何面向 6G 演进</h2><p>研究任务在哪里处理、何时完成，以及通信开销是否抵消远端计算收益。当前属于应用层协同；局域网和 5G 也可承载这一机制。</p><div class="table-wrapper"><table><thead><tr><th>结合点</th><th>当前实现与证据边界</th><th>未来能力与验证工作</th></tr></thead><tbody><tr><td>通信与计算联合调度</td><td>考虑逐槽等待、HTTP 往返、配置带宽与计算成本；带宽未经实测。</td><td>接入链路遥测，补完整业务文件传输与网络条件变化实验。</td></tr><tr><td>AI 辅助资源编排</td><td>Qwen 在 A 建议执行节点，经过排程保护；现有结果不支持优于规则。</td><td>验证动态条件下的决策收益，并将模型等待计入端到端耗时。</td></tr><tr><td>终端与边缘协同</td><td>已有真实双机 CPU 任务记录；当前在线节点以节点页为准。</td><td>扩展异构边缘节点，评估资源发现与跨接入协同。</td></tr><tr><td>任务时限保障</td><td>截止时间排序、租约重排；未取得网络服务质量保障。</td><td>对接可提供服务质量保障的试验网络，检验按时完成率与尾部时延。</td></tr></tbody></table></div><p class="fine-label">上述未来能力是演进需求，并非全部由 6G 首次提供。当前不涉及 6G 空口、基站控制或通感一体化。</p><a class="text-link" href="https://www.itu.int/rec/R-REC-M.2160-0-202311-I/en" target="_blank" rel="noopener">依据：ITU-R M.2160 · AI 与通信融合方向 ↗</a></section>`;
}
const offloadFields = [
 ['payload','上传数据量（MB）',2,0,1024], ['result','返回结果量（KB）',64,0,1048576],
 ['bandwidth','上下行可用带宽（Mbps）',100,.1,10000], ['rtt','额外往返开销（ms）',20,0,10000],
 ['local','本机计算（ms）',800,0,300000], ['remote','远端计算（ms）',200,0,300000],
 ['queue','远端排队（ms）',40,0,300000]
];
const offload = Object.fromEntries(offloadFields.map(([key,,value])=>[key,String(value)]));
function offloadResult() {
 const invalid=offloadFields.some(([key,,initial,min,max])=>offload[key].trim()===''||!Number.isFinite(Number(offload[key]))||Number(offload[key])<min||Number(offload[key])>max);
 if(invalid) return '<p class="inline-error">请填写范围内的有效数值，带宽至少为 0.1 Mbps。</p>';
 const upload=Number(offload.payload)*8000/Number(offload.bandwidth),download=Number(offload.result)*8/Number(offload.bandwidth);
 const remote=upload+download+Number(offload.rtt)+Number(offload.queue)+Number(offload.remote), local=Number(offload.local);
 const tied=Math.abs(remote-local)<.05;

 const max=Math.max(local,remote,1),network=upload+download+Number(offload.rtt),queue=Number(offload.queue),compute=Number(offload.remote);
 const lane=(label,total,parts)=>`<div class="duration-row"><div class="duration-label"><b>${label}</b><span>${ms(total)}</span></div><div class="duration-track" role="img" aria-label="${label}预计 ${ms(total)}"><div class="duration-fill" style="transform:scaleX(${total/max})">${parts.map(([kind,value])=>`<span class="duration-${kind}" style="width:${total?value/total*100:0}%"></span>`).join('')}</div></div></div>`;
 return `<div class="offload-verdict"><h3>${tied?'预计耗时相当':remote<local?'远端预计更快':'本机预计更快'}</h3><p>${tied?'可按数据范围选择。':remote<local?'计算收益覆盖通信与排队开销。':'通信与排队抵消远端计算收益。'}</p></div><div class="offload-timeline">${lane('本机',local,[['compute',local]])}${lane('远端',remote,[['network',network],['queue',queue],['compute',compute]])}</div><div class="duration-legend"><span class="compute">计算</span><span class="network">传输与往返</span><span class="queue">排队</span></div><details class="visual-notes"><summary>查看分项耗时</summary><p>上传 ${ms(upload)} + 往返 ${ms(Number(offload.rtt))} + 排队 ${ms(queue)} + 计算 ${ms(compute)} + 回传 ${ms(download)}</p></details>`;

}
function offloadPanel() {
 const field=([key,label,initial,min,max])=>`<label>${label}<input type="number" data-offload="${key}" min="${min}" max="${max}" step="any" inputmode="decimal" value="${esc(offload[key])}"></label>`;
 return `<section class="panel offload-panel" aria-labelledby="offload-title"><div class="offload-intro"><div><h2 id="offload-title">本机，还是远端？</h2><p>拖动带宽，观察通信成本如何改变选择。</p></div><span class="badge warn">参数推演 · 非实测</span></div><div class="offload-presets"><button class="button secondary mini" data-offload-preset="fast">通畅链路示例</button><button class="button secondary mini" data-offload-preset="slow">受限链路示例</button></div><div class="offload-layout"><div class="offload-controls"><div class="offload-inputs">${offloadFields.filter(([key])=>['payload','bandwidth','rtt'].includes(key)).map(field).join('')}</div><input class="bandwidth-range" type="range" min="0" max="1000" step="1" value="${Math.log10(Math.max(.1,Number(offload.bandwidth)||.1)/.1)*200}" data-offload-range aria-label="调整可用带宽" aria-valuetext="${esc(offload.bandwidth)} Mbps"><div class="range-endpoints"><span>0.1 Mbps</span><span>10,000 Mbps · 对数刻度</span></div><details class="visual-notes"><summary>计算、排队与回传参数</summary><div class="offload-inputs">${offloadFields.filter(([key])=>!['payload','bandwidth','rtt'].includes(key)).map(field).join('')}</div></details></div><div id="offload-result" class="offload-result" role="status" aria-live="polite">${offloadResult()}</div></div><details class="visual-notes"><summary>推演假设与验证边界</summary><p>所有数值是假设，不对应任何网络代际。MB、KB 为十进制；上下行带宽相同，本机排队为零，往返不含文件序列化传输。未计模型决策、协议头和重传。</p><p>此模型加入回传，不复刻线上调度器，也不提交任务或更改节点。当前业务测试仅传规格、执行端生成数据，不能据此证明真实文件传输或 6G 性能。</p></details></section>`;
}
function decisionEvidence(t) {
 return `<section class="decision-evidence"><h3>通信与计算决策依据</h3><p>先按在线状态与数据范围筛选节点，再由所选策略分配。联合调度估算逐槽等待、HTTP 往返、任务传输与计算成本；Qwen 建议另经整批排程保护。</p><dl><div><dt>任务 JSON 载荷</dt><dd>${fmt(t.payload_bytes)} 字节</dd></div><div><dt>派发时服务耗时估计</dt><dd>${ms(t.predicted_ms)}</dd></div><div><dt>最近开始距提交</dt><dd>${t.started_at==null?'—':ms((t.started_at-t.created_at)*1000)}</dd></div></dl><p class="fine-label">服务估计不含排队与模型等待；最近开始距提交可能包含重试等待。未保存逐节点通信与计算分项快照，不能从当前节点读数还原历史决策。上传与回传分段实测仍待补充。</p></section>`;
}
function resourceMeter(label,value,text,fresh=true) {
 const known=Number.isFinite(value),bounded=known?Math.max(0,Math.min(100,value)):0;
 return `<div class="resource-meter ${fresh?'':'is-stale'} ${bounded>=85?'is-high':''}"><div><span>${label}</span><b>${esc(text)}</b></div>${known?`<div class="resource-track" role="progressbar" aria-label="${label}${fresh?'':'（上报已过期）'}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${bounded}"><span style="transform:scaleX(${bounded/100})"></span></div>`:'<div class="resource-track unavailable"></div>'}</div>`;
}
function nodesPage() {
 return heading('算力节点','查看哪些电脑在线、还能接多少任务。')+`<section class="node-grid">${state.nodes.map(n=>{
 const r=n.resources||{},fresh=n.online&&n.resources_at&&state.server_time-n.resources_at<=15,active=Object.keys(n.active_tasks||{}).length,used=r.memory_total_gib&&r.memory_available_gib!=null?Math.max(0,Math.min(100,(1-r.memory_available_gib/r.memory_total_gib)*100)):null;
 return `<article class="panel node-card resource-node" data-key="node-${esc(n.id)}"><div class="node-header"><span class="metric-icon">${icon(n.mode==='same_host'?'monitor':'server')}</span><div><h3>${esc(n.name)}</h3><p>${n.mode==='same_host'?'同机进程':'远程终端'} · ${esc(n.id)}</p></div>${badge(n)}</div><div class="node-quick-stats"><span>${icon('activity')}HTTP 往返 <b>${ms(n.rtt_ms)}</b></span><span>${icon('shield')}${n.trusted?'管理员已设可信':'未设为可信'}</span></div>${resourceMeter('CPU 使用率',r.cpu_percent,r.cpu_percent==null?'未上报':fmt(r.cpu_percent,1)+'%',fresh)}${resourceMeter('内存占用',used,used==null?'未上报':fmt(used,1)+'%',fresh)}${resourceMeter('执行槽占用',active/(n.capacity||1)*100,active+' / '+(n.capacity||1),n.online)}${fresh?'':'<p class="resource-age">资源上报缺失或已过期</p>'}<details class="node-specs"><summary>设备配置与测量口径</summary><div class="node-details"><div><span>主机 / 来源</span><b>${esc(n.host)} / ${esc(n.peer)}</b></div><div><span>CPU 型号</span><b>${esc(r.cpu_name||'未上报')}</b></div><div><span>物理 / 逻辑核心</span><b>${r.physical_cores??'—'} / ${r.logical_cores??'—'}</b></div><div><span>可用 / 总内存</span><b>${r.memory_available_gib??'—'} / ${r.memory_total_gib??'—'} GiB</b></div><div><span>GPU（任务使用 CPU）</span><b>${esc(r.gpus?.map(g=>g.name+' '+g.memory_total_mib+' MiB').join('；')||'未检测到或未上报')}</b></div><div><span>配置带宽（非实测）</span><b>${fmt(n.bandwidth_mbps)} Mbps</b></div><div><span>累计完成</span><b>${fmt(n.completed)} 项</b></div><div><span>资源上报</span><b>${n.resources_at?Math.max(0,Math.floor(state.server_time-n.resources_at))+' 秒前':'未上报'}</b></div></div><p>HTTP 往返包含软件处理，不是无线时延；可信设置由管理员指定，不是硬件证明。</p></details><div class="node-buttons"><button class="button secondary mini" data-node="${esc(n.id)}" data-drain="${!n.draining}">${n.draining?'恢复接收任务':'暂停接收任务'}</button><button class="button secondary mini" data-trust="${esc(n.id)}" data-value="${!n.trusted}">${n.trusted?'撤销可信设置':'设为可信'}</button></div></article>`;
 }).join('')||'<div class="panel empty">暂无执行节点。<a href="#guide">查看启动步骤 ↗</a></div>'}</section>`+notice();
}
function emptyTasks(all) {
 const filtered = all && (taskSearch.trim() || taskFilter !== 'all');
 return `<div class="empty task-empty"><span class="empty-symbol">${icon('layers')}</span><h3>${filtered?'没有符合条件的任务':'还没有处理记录'}</h3><p>${filtered?'试试其他名称、ID 或任务状态。':'运行示例或提交自己的文本后，这里会显示进度和结果入口。'}</p><button class="button secondary" data-action="${filtered?'reset-filters':'create'}">${filtered?'清除筛选':'填写自己的文本'}</button></div>`;
}
function tasksPage() {
 const query=taskSearch.trim().toLowerCase();
 const tasks=state.tasks.filter(t=>(taskFilter==='all'||t.status===taskFilter)&&(!query||`${t.name} ${t.id}`.toLowerCase().includes(query)));
 tasks.sort((a,b)=>taskSort==='oldest'?a.created_at-b.created_at:taskSort==='latency'?(b.latency_ms??-1)-(a.latency_ms??-1)||b.created_at-a.created_at:b.created_at-a.created_at);
 const chips=[['all','全部'],['running','执行中'],['queued','待调度'],['completed','已完成'],['failed','执行失败'],['cancelled','已取消']];
 return heading('任务中心')+
 `<section class="task-tools" aria-label="任务筛选"><div class="filter-chips" aria-label="按状态快速筛选">${chips.map(([value,label])=>`<button type="button" class="filter-chip ${taskFilter===value?'active':''}" data-filter="${value}" aria-pressed="${taskFilter===value}">${label}<b>${value==='all'?state.tasks.length:state.tasks.filter(t=>t.status===value).length}</b></button>`).join('')}</div><div class="filters"><input id="task-search" type="search" aria-label="搜索任务" placeholder="搜索任务名称或 ID" value="${esc(taskSearch)}"><select id="task-filter" aria-label="筛选任务状态"><option value="all">全部状态</option>${Object.entries(labels).map(([s,l])=>`<option value="${s}" ${s===taskFilter?'selected':''}>${l}</option>`).join('')}</select><select id="task-sort" aria-label="任务排序">${[['newest','最新提交优先'],['oldest','最早提交优先'],['latency','完成时延从高到低']].map(([v,l])=>`<option value="${v}" ${taskSort===v?'selected':''}>${l}</option>`).join('')}</select><button class="button secondary" data-action="demo">运行演示任务</button></div><div class="filter-summary"><span>最近 ${state.tasks.length} 条记录中，符合条件 ${tasks.length} 条</span>${taskSearch.trim()||taskFilter!=='all'?'<button class="text-button" data-action="reset-filters">清除筛选</button>':''}</div></section>`+
 taskTable(tasks,'任务记录',true)+`<p class="fine-label">最近 200 条记录 · 导出包含全部任务</p>`+notice();
}
function experimentPage() {
 if(!simulation?.available) return heading('对比实验','',false)+offloadPanel()+`<section class="panel empty">${experimentsLoading?'正在加载实验结果…':experimentsError?esc(experimentsError):'尚未生成实验结果。<br>在项目目录运行 <code>python experiments.py</code>'}<br><button class="button secondary" data-action="experiments" ${experimentsLoading?'disabled':''}>${experimentsError?'重新加载':'加载结果'}</button></section>`;
 const groups=simulation.groups.filter(g=>g.scenario===scenario), names={normal:'正常网络',congested:'受限带宽',burst:'任务突发'};
 return heading('对比实验','',false)+offloadPanel()+`<section class="panel experiment-intro"><div><h2>四种策略，同一份任务序列</h2><p>${simulation.seeds} 个随机种子 × ${simulation.tasks_per_seed} 项任务 × 4 种策略 × 3 类条件</p></div><span class="badge warn">离散事件仿真 · 非实测</span></section><div class="scenario-tabs">${Object.entries(names).map(([k,v])=>`<button class="${k===scenario?'active':''}" data-scenario="${k}">${v}</button>`).join('')}</div><section class="panel chart-panel"><h2>按时完成率</h2><p class="muted">当前条件：${names[scenario]}。全部任务计入分母。</p>${groups.map(g=>`<div data-policy="${esc(g.policy)}" class="bar-row ${g.policy==='smart'?'smart':''}"><span>${esc(policies[g.policy] || g.label)}</span><div class="bar-track"><div class="bar-fill" style="--bar:${g.deadline_rate.mean*100}%"></div></div><span class="bar-value">${percent(g.deadline_rate.mean)}</span></div>`).join('')}</section><details class="panel experiment-details"><summary>完整指标与不确定性</summary><div class="panel-head"><h2 class="sr-only">指标与不确定性</h2><span class="badge">${simulation.seeds} 次独立任务序列</span></div><div class="table-wrapper"><table><thead><tr><th>策略</th><th>按时率</th><th>约 95% 区间</th><th>平均时延</th><th>各次 P95 均值</th></tr></thead><tbody>${groups.map(g=>`<tr><td>${esc(policies[g.policy] || g.label)}</td><td>${percent(g.deadline_rate.mean)}</td><td>${g.deadline_rate.ci95.map(percent).join(' ～ ')}</td><td>${ms(g.mean_ms.mean)}</td><td>${ms(g.p95_ms.mean)}</td></tr>`).join('')}</tbody></table></div></details><details class="scope-notes"><summary>仿真实验口径</summary><p>网络 RTT、带宽和计算时间为假设参数。调度器获知名义服务时间与队列，结果可能优于实际部署。原始 CSV、随机种子与输入哈希保存在 results 目录；不据此推断实际 6G 性能。</p></details>`;
}

function guidePage() {
 const steps=[
  ['启动协调端','安装 Python 3.10+，先用规则模式体验。默认启动两个同机执行进程。','python launch.py --scheduler rules'],
  ['接入另一台电脑','两机加入可信局域网，安全传递令牌。远程节点接入后，核实设备并设为可信。','python launch.py --host 0.0.0.0\n\npython -m suanlin.worker --coordinator http://协调端IP:8765 --token-file token.txt --name 办公室节点B'],
  ['提交并查看结果','回到工作台，点“试跑一条示例”查看结果。','']
 ],step=steps[guideStep-1];
 return heading('部署与验证','',false)+`<section class="panel deployment-panel"><div class="deployment-layout"><div><div class="deployment-scene" aria-label="协调端与执行端部署关系示意"><div class="deployment-device ${guideStep===1?'is-active':''}">${icon('server')}<b>协调端 A</b><small>接收 · 分配 · 汇总</small></div><div class="deployment-bridge ${guideStep>1?'is-active':''}" aria-hidden="true"><span></span></div><div class="deployment-device ${guideStep>1?'is-active':''}">${icon('monitor')}<b>执行端 B</b><small>处理 · 回传</small></div></div><p class="deployment-caption">部署关系示意 · 真实连接状态见工作台</p><div class="deployment-tabs" aria-label="部署步骤">${steps.map((st,i)=>`<button data-guide-step="${i+1}" aria-pressed="${guideStep===i+1}" class="${guideStep===i+1?'active':''}"><span>${i+1}</span>${st[0]}</button>`).join('')}</div></div><div class="deployment-instruction" data-key="guide-step-${guideStep}"><h2>${step[0]}</h2><p>${step[1]}</p>${step[2]?`<pre>${esc(step[2])}</pre>`:'<a class="button primary" href="#overview">返回工作台 ↗</a>'}</div></div></section><section class="guide-reference"><h2>按需了解</h2><details class="visual-notes"><summary>AI 与业务任务</summary><p>Qwen 只在 A 辅助分配节点，B 无需安装模型。日志分析、检索和压缩运行 CPU 算法；巡检分类器使用 48 条合成训练样本，结果需人工复核。业务演示输入为合成数据。</p></details><details class="visual-notes"><summary>通信指标与安全</summary><p>HTTP 往返含软件处理；带宽为配置值。载荷统计不含协议头、心跳与重传。可信范围由管理员设置。当前 HTTP 与共享令牌仅适用于可信实验网；跨网络需配置 TLS 和独立身份体系。</p></details><details class="visual-notes"><summary>材料位置与实验复现</summary><p>docs 保存可编辑说明，delivery 保存交付材料，reports 保存新版实测。软件功能验证与真实业务效果分别评价。</p><pre>python -m unittest discover -s tests -v\npython experiments.py</pre></details><details class="visual-notes roadmap-disclosure"><summary>AI 与 6G：当前实现与演进方向</summary>${networkRoadmap()}</details></section>`;
}
function render() {
 if (!state) return;
 InterfaceMotion.render($('#content'),({overview,nodes:nodesPage,tasks:tasksPage,experiments:experimentPage,guide:guidePage})[current](),current);
 syncPending();
 if ($('#create-dialog').open) updateFormHelp();
 $('#breadcrumb').textContent=titles[current];
 $('#node-count').textContent=state.nodes.filter(n=>n.online).length;
 document.querySelectorAll('[data-page]').forEach(a=>{ const active=a.dataset.page===current; a.classList.toggle('active',active); if(active) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current'); });
 ButtonHints.place();
}
function setConnection(online) {
 const el=$('#connection'),label=online?'协调服务在线':'连接中断';
 if(el.textContent.trim()!==label) el.innerHTML='<i></i>'+label;
 el.classList.toggle('error',!online);
 const banner=$('#connection-notice');
 banner.hidden=online;
 if (!online) {
  const message=`${connectionError}${lastSyncAt?' 当前显示 '+new Date(lastSyncAt).toLocaleTimeString('zh-CN',{hour12:false})+' 更新的数据。':authRequired?' 请完成登录后继续。':' 请先启动项目中的 start.bat。'}`;
  $('#connection-message').textContent=message;
 }
}
async function loadExperiments() {
 if(experimentsLoading) return;
 experimentsLoading=true;experimentsError='';render();
 try {simulation=await api('/api/experiments');}
 catch(error){experimentsError=error.message;}
 finally{experimentsLoading=false;render();}
}
async function refresh(force=false) {
 if(fetching||document.hidden&&!force) return;
 fetching=true;
 try {
  state=await api('/api/status');lastSyncAt=Date.now();connectionError='';authRequired=false;setConnection(true);lastError=false;render();
  if(current==='experiments'&&!simulation&&!experimentsError&&!experimentsLoading) void loadExperiments();
  if($('#detail-dialog').open&&!detailLoading) void refreshDetail();
 } catch(error) {
  connectionError=error.message;authRequired=error.status===401;setConnection(false);
  if(!state) $('#content').innerHTML=`<section class="panel connection-empty"><span class="empty-symbol">${icon('server')}</span><h1>${authRequired?'请登录工作空间':'暂时无法加载工作空间'}</h1><p>${authRequired?'输入协调端生成的接入令牌，登录后继续操作。':'确认协调服务已经启动，连接恢复后页面会自动更新。'}</p><button class="button primary" data-action="refresh">重新连接</button><a class="text-link" href="/guide" target="_blank" rel="noopener">查看启动说明 ↗</a></section>`;
  lastError=true;
 } finally {fetching=false;syncPending();FirstRun.sync();}
}
function pendingKey(button) {
 if(button.dataset.node) return 'node:'+button.dataset.node;
 if(button.dataset.trust) return 'trust:'+button.dataset.trust;
 if(button.dataset.cancel) return 'cancel:'+button.dataset.cancel;
 if(button.dataset.detail) return 'detail:'+button.dataset.detail;
 return ['try-example','demo','export','experiments','refresh','detail-refresh'].includes(button.dataset.action)?button.dataset.action:null;
}
function syncPending() {
 document.querySelectorAll('button').forEach(button=>{
  const key=pendingKey(button);
  if(!key) return;
  const busy=pending.has(key)||(key==='experiments'&&experimentsLoading);
  // Detail requests keep the trigger focusable so closing a dialog restores keyboard focus.
  const preserveFocus=key.startsWith('detail:');
  button.disabled=(busy&&!preserveFocus)||(key==='try-example'&&(!exampleNodes().length||lastError));
  if(key==='try-example') button.textContent=busy?'正在提交示例…':lastExampleId?'再跑一条示例':'试跑一条示例';
  if(busy&&preserveFocus) button.setAttribute('aria-disabled','true'); else button.removeAttribute('aria-disabled');
  if(busy) button.setAttribute('aria-busy','true'); else button.removeAttribute('aria-busy');
 });
}
const dispatchMessages = {
 offline:'没有在线的执行电脑。启动执行端后，这条任务会继续等待分配。',
 paused:'可用电脑已暂停接收任务。恢复接收后可以继续分配。',
 untrusted:'这项任务需要可信电脑。可在算力节点里核实并设置可信范围。',
 unsupported:'在线电脑不支持这类任务。请在执行电脑上启动支持这类输入的新版执行端。',
 memory:'支持这类任务的电脑可用内存不足，等待资源释放。',
 busy:'符合条件的电脑都在忙。空出执行槽后会继续分配。',
 planning:'系统正在比较可用电脑，拟定执行安排。',
 dispatch:'已有可接任务的电脑，正在等执行端领取。'
};
function queueAdvice(task) {
 const known = dispatchMessages[task.dispatch?.reason];
 if (known) return known;
 const online=state?.nodes.filter(node=>node.online)||[];
 if (!online.length) return dispatchMessages.offline;
 const active=online.filter(node=>!node.draining);
 if (!active.length) return dispatchMessages.paused;
 const trusted=active.filter(node=>task.privacy!=='local_only'||node.trusted);
 if (!trusted.length) return dispatchMessages.untrusted;
 const spec=task.payload?.workload;
 if (spec && !trusted.some(node=>(node.workload_kinds||[]).includes(spec.kind)&&(!spec.input_meta||node.file_workloads))) {
  return dispatchMessages.unsupported;
 }
 return trusted.some(node=>Object.keys(node.active_tasks||{}).length<(node.capacity||1))
  ? dispatchMessages.dispatch : dispatchMessages.busy;
}
const fullTime=value=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'—';
const inspectionActions = {
 '烟雾风险':['优先复核','交给现场安全负责人核查'],
 '通道占用':['安排处理','联系场地负责人清理通道'],
 '门禁异常':['安排处理','联系门禁管理人员核查'],
 '正常状态':['记录留档','复核后归档']
};
function inspectionRows(task) {
 return (task.result?.predictions || []).map((row,index)=>({row,index:index+1,action:inspectionActions[row.label] || ['待复核','核查后决定下一步']}))
  .sort((a,b)=>Object.keys(inspectionActions).indexOf(a.row.label)-Object.keys(inspectionActions).indexOf(b.row.label));
}
function statsList(rows) {
 return `<dl class="result-summary">${rows.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
}
function countPills(counts) {
 return `<div class="result-pills">${Object.entries(counts||{}).map(([key,count])=>`<span>${esc(key)} <b>${fmt(count)}</b></span>`).join('')}</div>`;
}
function resultDownload(label) {
 return `<button class="button secondary mini" type="button" data-action="download-output">${icon('download')}${label}</button>`;
}
function taskOutput(t) {
 const spec=t.payload?.workload, result=t.result?.summary;
 if (!spec) {
  const rows=inspectionRows(t);
  if (!rows.length) return `<section class="task-result" aria-label="分类结果"><h3>巡检记录</h3><ol class="source-list">${(t.payload?.events||[]).map(text=>`<li>${esc(text)}</li>`).join('')}</ol></section>`;
  return `<section class="task-result" aria-label="分类结果"><div class="result-heading"><h3>巡检待办清单</h3>${resultDownload('导出待办清单')}</div><p class="form-note">共 ${rows.length} 条，按建议处理顺序排列。请人工复核分类与建议。</p><ol class="source-list">${rows.map(({row,index,action})=>`<li><strong>${esc(row.label)} · ${action[0]}</strong><span class="source-locator">原记录第 ${index} 条</span><p>${esc(row.text)}</p><span class="form-note">建议：${action[1]}</span></li>`).join('')}</ol></section>`;
 }
 const kind=spec.kind, source=spec.input_meta;
 const caption=source ? `<p class="form-note">${source.example?'来源：示例内容':'来源：你提供的内容'} · ${esc(source.files.map(file=>file.name).join('、'))}</p>`
  : '<p class="form-note">来源：历史合成数据演示</p>';
 let content='';
 if (!result) {
  content=source ? '<p>处理结果会在这里自动出现。</p>' : `<p>提交规模：${fmt(spec.units)}</p>`;
 } else if (source && kind==='log_analysis') {
  const rows=[['分析记录',fmt(result.records)+' 条'],['检测到异常',fmt(result.abnormal)+' 条']];
  if(result.latency_samples) rows.push(['95% 的请求不超过（'+fmt(result.latency_samples)+' 个样本）',ms(result.p95_latency_ms)]);
  content=statsList(rows)+
   (Object.keys(result.status_counts||{}).length ? '<h4>状态码分布</h4>'+countPills(result.status_counts) : '')+
   (Object.keys(result.alerts||{}).length ? '<h4>常见异常信号</h4>'+countPills(result.alerts) : '')+
   (result.affected?.length ? '<h4>异常最多的服务</h4>'+countPills(Object.fromEntries(result.affected.map(item=>[item.name,item.count]))) : '')+
   `<div class="result-heading"><h4>异常原行</h4>${result.examples?.length?resultDownload('导出异常清单'):''}</div>
    ${result.examples?.length?`<ol class="source-list">${result.examples.map(item=>`<li><strong>第 ${item.line} 行${item.status?' · HTTP '+esc(item.status):''}${item.service?' · '+esc(item.service):''}</strong><p>${esc(item.excerpt||'此行未提供消息字段。')}</p></li>`).join('')}</ol><p class="form-note">最多列出前 20 条异常，统计覆盖全部输入。</p>`:'<p>当前规则没有检测到异常。</p>'}`;
 } else if (source && kind==='document_search') {
  content=statsList([['检索内容',result.query],['检查的文件',fmt(result.documents)+' 个']])+
   `<div class="result-heading"><h4>原文片段</h4>${result.matches?.length?resultDownload('导出命中片段'):''}</div>
    ${result.matches?.length?`<ol class="source-list">${result.matches.map(hit=>`<li><strong>${esc(hit.file)} · 第 ${hit.line} 行</strong><p class="source-quote">${esc(hit.excerpt)}</p><span class="form-note">命中：${esc(hit.matched.join('、'))}</span></li>`).join('')}</ol>`:'<p>没有找到相关片段。可以换一个更具体的关键词。</p>'}`;
 } else if (source && kind==='archive') {
  content=statsList([['遮盖内容',fmt(result.redacted_count)+' 处'],['处理记录',fmt(result.records)],['压缩包大小',fmt(result.compressed_bytes/1024,1)+' KB']])+
   countPills(result.redactions)+
   (result.unmatched_fields?.length?`<p class="inline-error">未找到字段：${esc(result.unmatched_fields.join('、'))}。请核对列名或字段名。</p>`:'')+
   `<p class="form-note">下载后请检查副本，确认未识别的敏感内容也已处理。</p>
    ${t.result.download_available?`<a class="button primary mini" href="/api/tasks/${encodeURIComponent(t.id)}/result.zip" download data-tip="下载脱敏后的文件压缩包">${icon('download')}下载脱敏 ZIP</a>`:''}`;
 } else if(kind==='log_analysis') {
  content=statsList([['已分析记录',fmt(result.records)+' 条'],['状态码分布',Object.entries(result.status_counts||{}).map(([key,value])=>key+'：'+fmt(value)).join('；')]]);
 } else if(kind==='document_search') {
  content=statsList([['已检索文档',fmt(result.documents)+' 篇'],['查询词',fmt(result.queries)+' 组']]);
 } else {
  content=statsList([['处理记录',fmt(result.records)+' 条'],['压缩前 / 后',fmt(result.uncompressed_bytes)+' / '+fmt(result.compressed_bytes)+' 字节']]);
 }
 return `<section class="task-result"><h3>${esc(taskKinds[kind]||kind)}${result?'结果':''}</h3>${caption}${content}</section>`;
}
function downloadTaskOutput(task) {
 let rows, name;
 const spec=task.payload?.workload, summary=task.result?.summary;
 if (!spec) {
  rows=[['原记录序号','记录','分类','处理优先级','建议'],
        ...inspectionRows(task).map(({row,index,action})=>[index,row.text,row.label,...action])];
  name='巡检待办清单.csv';
 } else if (spec.kind==='log_analysis' && summary?.examples?.length) {
  rows=[['行号','状态码','服务','原文'],...summary.examples.map(item=>[item.line,item.status,item.service,item.excerpt])];
  name='日志异常清单.csv';
 } else if (spec.kind==='document_search' && summary?.matches?.length) {
  rows=[['文件','行号','命中词','原文'],...summary.matches.map(hit=>[hit.file,hit.line,hit.matched.join('、'),hit.excerpt])];
  name='文档命中片段.csv';
 } else return;
 const csv=rows.map(row=>row.map(value=>{
  let text=String(value??'');
  if (/^[=+@-]/.test(text.trimStart()) || /^[\t\r\n]/.test(text)) text="'"+text;
  return '"'+text.replace(/"/g,'""')+'"';
 }).join(',')).join('\r\n');
 const url=URL.createObjectURL(new Blob(['\ufeff',csv],{type:'text/csv;charset=utf-8'}));
 const link=document.createElement('a');link.href=url;link.download=name;link.click();
 setTimeout(()=>URL.revokeObjectURL(url),5000);
}

const progressStages = {
 classify:'整理巡检记录', compute:'执行附加计算', read_logs:'读取并统计日志',
 search_files:'查找相关原文', redact:'识别并遮盖敏感内容', pack_archive:'打包下载文件',
 log_analysis:'统计日志', build_index:'建立文档索引', search:'匹配检索词', archive:'脱敏与压缩', complete:'处理完成'
};
function schedulingProgress(t) {
 const status=t.status, terminal=['completed','failed','cancelled'].includes(status);
 const worker=state?.nodes.find(node=>node.id===t.worker_id)?.name || t.worker_id;
 const assigned=Boolean(t.started_at && t.worker_id && status!=='queued');
 const running=status==='running';
 const steps=[
  ['提交','done',time(t.created_at)],
  ['分配电脑',assigned?'done':terminal?'waiting':'active',assigned?worker:'等候接单'],
  ['处理内容',status==='completed'?'done':terminal&&assigned?'stopped':running?'active':'waiting',assigned?time(t.started_at):'尚未开始'],
  ['返回结果',status==='completed'?'done':terminal?'stopped':'waiting',status==='completed'?time(t.finished_at):terminal?labels[status]:'等处理完成']
 ];
 const progress=t.progress;
 const hasProgress=running && Number.isFinite(progress?.total) && progress.total>0 && Number.isFinite(progress?.done);
 const stage=hasProgress?(progressStages[progress.stage]||'正在处理'):'正在处理';
 const width=hasProgress?Math.min(100,Math.max(0,progress.done/progress.total*100)):0;
 const bar=hasProgress?`<div class="work-progress"><div><strong>${esc(stage)}</strong><span>${fmt(progress.done)} / ${fmt(progress.total)}</span></div>
 <progress max="100" value="${width}" aria-label="${esc(stage)}"></progress></div>`:'';
 const late=status==='queued'&&state?.server_time>t.deadline_at;
 const note=status==='queued'?queueAdvice(t)+(late?' 已超过目标时限。':''):running?(hasProgress?'':'执行电脑已接单，等待它回报处理进度。'):
  status==='cancelled'?'任务已取消。':status==='failed'?(t.error||'任务执行失败。'):'';
 const counts=status==='queued'&&t.dispatch?`<p class="schedule-counts">在线 ${t.dispatch.online} 台 · 符合条件 ${t.dispatch.eligible} 台 · 有空位 ${t.dispatch.free} 台</p>`:'';
 const waited=(status==='queued'&&Number.isFinite(state?.server_time))?Math.max(0,(state.server_time-t.created_at)*1000):null;
 return `<section class="schedule-window" aria-label="调度进度">
 <div class="result-heading"><h3>调度进度</h3>${waited!==null?`<span class="schedule-counts">已等待 ${fmt(waited/1000,1)} 秒</span>`:''}</div>
 <ol class="dispatch-steps">${steps.map(([label,phase,detail],index)=>`<li data-phase="${phase}" ${phase==='active'?'aria-current="step"':''}>
  <span class="dispatch-dot">${phase==='done'?icon('check'):index+1}</span><div><strong>${label}</strong><small>${esc(detail)}</small></div>
 </li>`).join('')}</ol>
 ${note?`<p class="schedule-note" role="status">${esc(note)}</p>`:''}${counts}${bar}
 </section>`;
}

function taskDetailHTML(t) {
 const terminal=['completed','failed','cancelled'].includes(t.status);
 return `<div class="detail-title"><div><h3>${esc(t.name)}</h3><span class="mono">${esc(t.id)}</span></div><span class="status ${t.status}" aria-live="polite">${statusLabel(t.status)}</span></div>
 ${schedulingProgress(t)}
 ${detailError?`<p class="inline-error" role="status">详情更新中断：${esc(detailError)}</p>`:''}
 <div class="task-outcome">${taskOutput(t)}
 <dl class="result-meta"><div><dt>执行电脑</dt><dd>${esc(state?.nodes.find(n=>n.id===t.worker_id)?.name||t.worker_id||'等待分配')}</dd></div><div><dt>从提交到完成</dt><dd>${ms(t.latency_ms)}</dd></div></dl></div>
 ${t.status==='completed'?`<div class="result-next"><button class="button primary" data-action="next-create">${t.payload?.workload?'创建另一个任务':'填写自己的文本'}</button><a class="text-link" href="#tasks" data-detail-close>查看全部任务 ↗</a></div>`:''}
 <details class="task-technical"><summary>查看调度与执行细节</summary>
   ${t.scheduler_decision?.plan_revision?`<div class="notice">计划版本 ${esc(t.scheduler_decision.plan_revision)} · 执行序号 ${esc(t.scheduler_decision.order+1)} · 已重规划 ${esc(t.replan_count||0)} 次${t.status==='queued'?` · 预计开始 ${time(t.scheduler_decision.predicted_start_at)} · 预计完成 ${time(t.scheduler_decision.predicted_finish_at)}`:''}</div>`:''}
 ${t.policy==='ai'?`<div class="notice"><span>调度决策：${esc(({qwen:'Qwen 方案',qwen_guarded:'Qwen 方案经排程保护调整',rules_fallback:'规则回退'})[t.scheduler_decision?.source] || '等待 A 上的 Qwen')} · 模型调用 ${ms(t.scheduler_decision?.model_ms)} · ${esc(t.scheduler_decision?.reason || '')}${t.scheduler_decision?.model_worker_id?` · 模型建议 ${esc(t.scheduler_decision.model_worker_id)} → 最终节点 ${esc(t.scheduler_decision.selected_worker || t.scheduler_decision.worker_id || '待定')}`:''}</span></div>`:''}
 <div class="detail-meta"><span>执行节点<b>${esc(state?.nodes.find(n=>n.id===t.worker_id)?.name||t.worker_id||'尚未分配')}</b></span><span>完成时延<b>${ms(t.latency_ms)}</b></span><span>调度策略<b>${esc(policies[t.policy])}</b></span><span>派发尝试<b>${t.attempts} 次</b></span><span>完成时限<b>${ms(t.deadline_ms)}${t.status==='completed'?(t.deadline_met?' · 按时完成':' · 已超时'):''}</b></span><span>计算耗时 / 附加负载<b>${ms(t.result?.compute_ms)} / ${ms(t.result?.load_ms)}</b></span></div>
 ${decisionEvidence(t)}
 ${t.payload?.workload?`<h3>完整业务结果</h3><pre class="raw-result">${esc(JSON.stringify(t.result?.summary||{status:t.status},null,2))}</pre>`:''}
 </details><div class="detail-toolbar"><span>${terminal?'执行已结束':'状态每 1.5 秒自动更新'}</span><button class="button secondary mini" data-action="detail-refresh">刷新详情</button></div>`;
}
async function refreshDetail(force=false) {
 if(!activeTask||detailLoading||!$('#detail-dialog').open) return;
 if(!force&&!detailError&&['completed','failed','cancelled'].includes(activeTask.status)) return;
 const version=detailVersion,id=activeTask.id;
 detailLoading=true;
 try {
  const task=await api('/api/tasks/'+encodeURIComponent(id));
  if(version!==detailVersion||!$('#detail-dialog').open) return;
  activeTask=task;detailError='';
  InterfaceMotion.update($('#detail-content'),taskDetailHTML(task));syncPending();FirstRun.sync();
 } catch(error) {
  if(version!==detailVersion||!$('#detail-dialog').open) return;
  detailError=error.message;
  if(activeTask.status) InterfaceMotion.update($('#detail-content'),taskDetailHTML(activeTask));
  else $('#detail-content').innerHTML=`<div class="empty"><p>${esc(detailError)}</p><button class="button secondary" data-action="detail-refresh">重试加载详情</button></div>`;
 } finally {if(version===detailVersion) detailLoading=false;}
}
async function detail(id) {
 detailVersion++;detailLoading=false;detailError='';activeTask={id};
 $('#detail-content').innerHTML='<div class="loading">正在读取任务详情…</div>';
 if(!$('#detail-dialog').open) $('#detail-dialog').showModal();
 await refreshDetail(true);
}
const MAX_FILE_BYTES = 256 * 1024;
const workloadHelp = {
 log_analysis:'用日志定位失败和超时，按服务找出问题最多的地方。',
 document_search:'在多份资料中找相关原文，并定位到文件和行号。',
 archive:'遮盖邮箱、手机号、身份证号和 IPv4 地址，下载压缩后的副本。'
};
const businessExamples = {
 log_analysis:{format:'jsonl',text:'{"service":"订单接口","status":200,"latency_ms":72,"message":"订单查询成功"}\n{"service":"支付接口","status":504,"latency_ms":980,"message":"请求超时"}\n{"service":"支付接口","status":502,"latency_ms":410,"message":"上游连接失败"}\n{"service":"订单接口","status":200,"latency_ms":60,"message":"订单查询成功"}'},
 document_search:{format:'txt',text:'差旅报销说明\n报销材料包括电子发票、行程单和出差审批单。\n在出差结束后十个工作日内提交。\n财务每周三核对报销材料。',query:'报销 发票'},
 archive:{format:'csv',text:'姓名,邮箱,手机号,客户编号,备注\n示例客户,sample@example.com,13800138000,C-002,已回访',fields:'姓名，客户编号'}
};

function availableTaskNodes(privacy, kind) {
 return (state?.nodes || []).filter(node=>node.online && !node.draining &&
  (privacy === 'any' || node.trusted) &&
  (!kind || (node.file_workloads && (node.workload_kinds || []).includes(kind))));
}
function updateFormHelp() {
 const form=$('#task-form'), f=form.elements;
 const business=f.workload_kind.value;
 const files=f.workload_files.files;
 const hasFiles=Boolean(business && files.length);
 $('#text-fields').hidden=Boolean(business);
 $('#business-fields').hidden=!business;
 $('#workload-help').textContent=workloadHelp[business] || '把巡检记录整理成可导出的待办清单。';
 f.events.required=!business;
 f.events.disabled=Boolean(business);
 f.events_file.disabled=Boolean(business);
 f.workload_files.disabled=!business;
 f.workload_files.multiple=business==='document_search';
 f.workload_files.accept=business==='log_analysis'?'.log,.txt,.csv,.jsonl,.ndjson':
  business==='archive'?'.txt,.log,.csv,.json,.jsonl,.ndjson':'.txt,.md,.log,.csv,.json,.jsonl,.ndjson';
 $('#input-file-label').textContent=business==='document_search'?'上传资料（最多 12 个 UTF-8 文本文件）':'上传文件（UTF-8）';
 $('#file-selection').hidden=!hasFiles;
 $('#file-summary').textContent=hasFiles ? '已选 '+files.length+' 个文件 · '+fmt([...files].reduce((n,file)=>n+file.size,0)/1024,1)+' KB' : '';
 $('#business-paste').hidden=hasFiles;
 $('#paste-format-label').hidden=business==='document_search';
 f.workload_format.disabled=!business || hasFiles || business==='document_search';
 const jsonOption = f.workload_format.querySelector('[value=json]');
 jsonOption.disabled=business==='log_analysis';
 if (jsonOption.disabled && f.workload_format.value==='json') f.workload_format.value='jsonl';
 f.workload_text.disabled=!business || hasFiles;
 f.workload_text.required=Boolean(business && !hasFiles);
 f.workload_text.placeholder=business==='log_analysis'?'粘贴日志原文，或选择上方的日志文件':business==='document_search'?'粘贴会议纪要、制度或手册内容':'粘贴需要脱敏的文本或表格内容';
 $('#query-label').hidden=business!=='document_search';
 f.workload_query.disabled=business!=='document_search';
 f.workload_query.required=business==='document_search';
 $('#redact-fields-label').hidden=business!=='archive';
 f.workload_fields.disabled=business!=='archive';
 f.compute_loops.disabled=Boolean(business);
 f.privacy.disabled=Boolean(business);
 const lines=f.events.value.split('\n').map(line=>line.trim()).filter(Boolean);
 const tooLong=lines.some(line=>Array.from(line).length>2000);
 const error=lines.length>128?'最多支持 128 条，请减少后提交。':tooLong?'有一条文本超过 2000 字，请缩短后提交。':!lines.length?'请输入至少一条非空文本。':'';
 f.events.setCustomValidity(business?'':error);
 $('#event-count').textContent='已输入 '+lines.length+' / 128 条 · 每条最多 2000 字';
 $('#event-count').classList.toggle('is-error',lines.length>128||tooLong);
 $('#fill-example').disabled=Boolean(f.events.value.trim());
 f.name.setCustomValidity(f.name.value.trim()?'':'请填写任务名称。');
 const hint=$('#task-node-hint');
 const privacy=business?'local_only':f.privacy.value;
 const eligible=availableTaskNodes(privacy,business);
 const canUpload=!business || state?.capabilities?.file_workloads;
 hint.textContent=!canUpload?'当前后台版本还不支持文件任务。请重新启动新版服务后再试。':
  eligible.length?eligible.length+' 台电脑可以接这类任务。':queueAdvice({privacy,payload:business?{workload:{kind:business,input_meta:{}}}:{}});
 hint.classList.toggle('is-warning',!canUpload||!eligible.length);
 return lines;
}
async function readUtf8(file) {
 try {
  return new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());
 } catch (_) {
  throw new Error('无法读取「'+file.name+'」，请保存为 UTF-8 文本后重试。');
 }
}
async function collectWorkload(form, kind) {
 const f=form.elements;
 const selected=[...f.workload_files.files];
 if (selected.length > (kind==='document_search'?12:1)) {
  throw new Error(kind==='document_search'?'最多选择 12 个文件。':'请只选择一个文件。');
 }
 if (selected.reduce((sum,file)=>sum+file.size,0)>MAX_FILE_BYTES) throw new Error('输入合计最多 256 KB，请先截取要处理的范围。');
 const ext=kind==='document_search'?'txt':f.workload_format.value;
 const pasted=f.workload_text.value;
 const files=selected.length?await Promise.all(selected.map(async file=>({name:file.name,text:await readUtf8(file)}))):
  [{name:'粘贴的内容.'+ext,text:pasted}];
 if (files.some(file=>!file.text.trim())) throw new Error('请提供有内容的文件或粘贴文字。');
 if (files.reduce((sum,file)=>sum+new TextEncoder().encode(file.text).length,0)>MAX_FILE_BYTES) throw new Error('输入合计最多 256 KB，请先截取要处理的范围。');
 const input={files};
 if (kind==='document_search') input.query=f.workload_query.value.trim();
 if (kind==='archive') input.fields=f.workload_fields.value.split(/[,，;；\n]/).map(value=>value.trim()).filter(Boolean);
 const example=businessExamples[kind];
 if (!selected.length && pasted===example.text && ext===example.format &&
     (!example.query || input.query===example.query) && (!example.fields || input.fields.join('，')===example.fields)) input.example=true;
 return {kind,input};
}
function openCreate(kind) {
 const f=$('#task-form').elements;
 const nextKind=taskKinds[kind]?kind:'';
 if (f.workload_kind.value!==nextKind) {
  f.workload_files.value='';
  f.workload_text.value='';
  f.workload_query.value='';
  f.workload_fields.value='';
  f.workload_format.value='txt';
 }
 f.workload_kind.value=nextKind;
 if (nextKind) {
  f.name.value=taskKinds[nextKind];
  f.deadline_ms.value=300000;
  f.compute_loops.value=0;
 } else {
  if (Object.values(taskKinds).includes(f.name.value)) f.name.value='办公巡检事件分类';
  f.deadline_ms.value=30000;
 }
 $('#form-error').hidden=true;
 updateFormHelp();
 $('#create-dialog').showModal();
}

document.addEventListener('click',async event=>{
 const nodeLink=event.target.closest('[data-detail-nodes]');
 if(nodeLink||event.target.closest('[data-detail-close]')) $('#detail-dialog').close();
 const button=event.target.closest('button');
 if(!button||button.disabled) return;
 const key=pendingKey(button);
 if(key&&pending.has(key)) return;
 if(key) {pending.add(key);syncPending();}
 try {
  const action=button.dataset.action;
  if(action==='start-tour') FirstRun.start();
  if(button.dataset.topologyNode) {selectedTopologyNode=button.dataset.topologyNode;render();}
  if(button.dataset.guideStep) {guideStep=Number(button.dataset.guideStep);render();}
  if(action==='create') {if(FirstRun.is('tasks')) FirstRun.stop(false);openCreate();}
  if(button.dataset.template) openCreate(button.dataset.template);
  if(action==='next-create') {$('#detail-dialog').close();$('#task-form').reset();$('#task-form details').open=false;openCreate();}
  if(action==='try-example') {
   const guided=FirstRun.is('sample');
   await refresh(true);
   if(lastError) throw new Error('协调服务暂时无法连接，请恢复连接后再试。');
   if(!exampleNodes().length) throw new Error('还没有可接收任务的可信节点，请先检查执行节点。');
   const task=await api('/api/tasks',{name:'首次体验：巡检文字分类',events:['消防出口被纸箱堵塞'],policy:'smart',privacy:'local_only',deadline_ms:30000,compute_loops:0});
   lastExampleId=task.id;await refresh(true);
   if(!guided||FirstRun.is('sample')) {
    await detail(task.id);
    if(guided&&FirstRun.is('sample')&&$('#detail-dialog').open) FirstRun.show('result');
   }
  }
  if(action==='close-dialog') $('#create-dialog').close();
  if(action==='close-detail') $('#detail-dialog').close();
  if(action==='refresh') await refresh(true);
  if(action==='detail-refresh') await refreshDetail(true);
  if(action==='download-output' && activeTask?.status==='completed') downloadTaskOutput(activeTask);
  if(action==='reset-filters') {taskSearch='';taskFilter='all';render();$('#task-search')?.focus();}
  if(action==='clear-files') {$('#task-form').elements.workload_files.value='';updateFormHelp();}
  if(action==='business-example') {
   const f=$('#task-form').elements, sample=businessExamples[f.workload_kind.value];
   if(sample) {
    f.workload_files.value='';f.workload_text.value=sample.text;f.workload_format.value=sample.format;
    f.workload_query.value=sample.query||'';f.workload_fields.value=sample.fields||'';
    updateFormHelp();f.workload_text.focus();
   }
  }
  if(action==='example') {
   const field=$('#task-form').elements.events;
   if(!field.value.trim()) field.value='消防出口被纸箱堵塞\n配电箱发现浓烟和明火\n办公室设备运行正常';
   updateFormHelp();field.focus();
  }
  if(action==='export') {
   const blob=await request('/api/tasks/export.csv',undefined,true),url=URL.createObjectURL(blob),a=document.createElement('a');
   a.href=url;a.download='任务调度记录.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);toast('全部任务记录已导出');
  }
  if(action==='demo') {await api('/api/demo',{count:12,policy:state.ai_scheduler?.enabled?'ai':'smart'});toast('12 项合成事件已提交，正在真实执行');await refresh(true);}
  if(action==='experiments') await loadExperiments();
  if(button.dataset.detail) await detail(button.dataset.detail);
  if(button.dataset.cancel) {await api('/api/tasks/'+button.dataset.cancel+'/cancel',{});toast('任务已取消');await refresh(true);}
  if(button.dataset.node) {await api('/api/nodes/'+button.dataset.node,{draining:button.dataset.drain==='true'});await refresh(true);toast('节点接收状态已更新');}
  if(button.dataset.trust) {await api('/api/nodes/'+button.dataset.trust,{trusted:button.dataset.value==='true'});await refresh(true);toast('节点可信范围已更新');}
  if(button.dataset.offloadPreset) {
   Object.assign(offload,button.dataset.offloadPreset==='slow'?{bandwidth:'10',rtt:'80'}:{bandwidth:'100',rtt:'20'});render();
  }
  if(button.dataset.scenario) {scenario=button.dataset.scenario;render();}
  if(button.dataset.filter) {taskFilter=button.dataset.filter;render();}
 } catch(error) {
  toast(error.message);
  if(button.dataset.action==='try-example') FirstRun.exampleError(error.message);
 }
 finally {if(key) {pending.delete(key);syncPending();}}
});
$('#detail-dialog').addEventListener('close',()=>{detailVersion++;activeTask=null;detailLoading=false;if(FirstRun.is('result')) FirstRun.show('nodes');});
$('#task-form').addEventListener('input',updateFormHelp);
$('#task-form').addEventListener('invalid',event=>{const section=event.target.closest('details');if(section)section.open=true;},true);
$('#task-form').addEventListener('change',async event=>{
 const f=event.currentTarget.elements;
 if(event.target===f.workload_kind) {
  f.deadline_ms.value=f.workload_kind.value?300000:30000;
  if(['办公巡检事件分类',...Object.values(taskKinds)].includes(f.name.value)) f.name.value=taskKinds[f.workload_kind.value]||'办公巡检事件分类';
 }
 if(event.target===f.events_file && f.events_file.files[0]) {
  const file=f.events_file.files[0];
  try {
   if(file.size>MAX_FILE_BYTES) throw new Error('巡检文件最多 256 KB，请先截取要整理的内容。');
   const text=await readUtf8(file);
   if(f.events_file.files[0]===file) {f.events.value=text;$('#form-error').hidden=true;}
  } catch(error) {$('#form-error').textContent=error.message;$('#form-error').hidden=false;}
 }
 updateFormHelp();
});
$('#task-form').addEventListener('submit',async event=>{
 event.preventDefault();
 if(pending.has('submit')) return;
 const events=updateFormHelp();if(!event.target.reportValidity()) return;
 const f=new FormData(event.target),button=$('button[type="submit"]',event.target);
 pending.add('submit');button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='正在提交…';$('#form-error').hidden=true;
 try {
  const body={name:f.get('name').trim(),policy:f.get('policy'),privacy:f.get('privacy'),deadline_ms:Number(f.get('deadline_ms')),compute_loops:Number(f.get('compute_loops'))};
  if(f.get('workload_kind')) {
   if(!state?.capabilities?.file_workloads) throw new Error('请重新启动新版后台服务，再提交文件任务。');
   body.privacy='local_only';
   body.workload=await collectWorkload(event.target,f.get('workload_kind'));
  }
  else body.events=events;
  const task=await api('/api/tasks',body);
  $('#create-dialog').close();event.target.reset();$('#task-form details').open=false;updateFormHelp();await refresh(true);await detail(task.id);
 } catch(error) {$('#form-error').textContent=error.message;$('#form-error').hidden=false;}
 finally {pending.delete('submit');button.disabled=false;button.removeAttribute('aria-busy');button.textContent='提交任务';}
});
$('#login-form').addEventListener('submit',async event=>{
 event.preventDefault();if(pending.has('login')) return;
 const button=$('button[type="submit"]',event.target);pending.add('login');button.disabled=true;
 try {await api('/api/login',{token:new FormData(event.target).get('token')});$('#login-dialog').close();event.target.reset();await refresh(true);}
 catch(error){toast(error.message);}
 finally{pending.delete('login');button.disabled=false;}
});
document.addEventListener('input',event=>{if(event.target.id==='task-search'&&!event.isComposing){taskSearch=event.target.value;render();}});
document.addEventListener('input',event=>{
 const field=event.target.dataset.offload,range=event.target.hasAttribute('data-offload-range');
 if(!field&&!range) return;
 if(range) {offload.bandwidth=String(Math.round(.1*Math.pow(10,Number(event.target.value)/200)*10)/10);const input=$('[data-offload="bandwidth"]');if(input) input.value=offload.bandwidth;}
 else offload[field]=event.target.value;
 const slider=$('[data-offload-range]');if(slider){if(!range)slider.value=Math.log10(Math.max(.1,Number(offload.bandwidth)||.1)/.1)*200;slider.setAttribute('aria-valuetext',offload.bandwidth+' Mbps');}
 const result=$('#offload-result');if(result) InterfaceMotion.update(result,offloadResult());
});
document.addEventListener('compositionend',event=>{if(event.target.id==='task-search'){taskSearch=event.target.value;render();}});
document.addEventListener('change',event=>{
 if(event.target.id==='topology-node-select') {selectedTopologyNode=event.target.value;render();}
 if(event.target.id==='task-filter'){taskFilter=event.target.value;render();}
 if(event.target.id==='task-sort'){taskSort=event.target.value;render();}
});
function navigate() {
 const page=Object.hasOwn(titles,location.hash.slice(1))?location.hash.slice(1):'overview';
 const changed=page!==current;
 current=page;
 render();
 if(changed) window.scrollTo({top:0});
 FirstRun.navigated();
 if(current==='experiments'&&state&&!simulation&&!experimentsLoading) void loadExperiments();
}
window.addEventListener('hashchange',navigate);
window.addEventListener('online',()=>void refresh(true));
document.addEventListener('visibilitychange',()=>{if(!document.hidden) void refresh(true);});
setInterval(()=>$('#clock').textContent=new Date().toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'})+' · '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}),1000);
const clickControls = 'button, a[href], summary, [role="button"]';
const AudioFeedback = (() => {
 let context;
 function unlock() {
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (!Audio) return;
  try {
   context ||= new Audio();
   return context.state === 'suspended' ? context.resume() : Promise.resolve();
  } catch (_) { /* Sound is optional on devices without an audio output. */ }
 }
 function tone(kind, syllable = 0) {
  if (!context || context.state !== 'running' || document.hidden) return;
  const voice = kind === 'voice';
  const oscillator = context.createOscillator();
  const volume = context.createGain();
  const at = context.currentTime;
  const duration = voice ? .065 : .055;
  const pitch = voice ? [560, 670, 620, 730][syllable % 4] : 740;
  oscillator.type = voice ? 'triangle' : 'sine';
  oscillator.frequency.setValueAtTime(pitch, at);
  oscillator.frequency.exponentialRampToValueAtTime(pitch * (voice ? .85 : .58), at + duration);
  volume.gain.setValueAtTime(.0001, at);
  volume.gain.exponentialRampToValueAtTime(voice ? .024 : .042, at + .007);
  volume.gain.exponentialRampToValueAtTime(.0001, at + duration);
  oscillator.connect(volume).connect(context.destination);
  oscillator.start(at);
  oscillator.stop(at + duration);
  oscillator.onended = () => { oscillator.disconnect(); volume.disconnect(); };
 }
 document.addEventListener('click', event => {
  const control = event.target.closest(clickControls);
  if (!event.isTrusted || !control || control.disabled || control.getAttribute('aria-disabled') === 'true') return;
  const ready = unlock();
  if (ready) void ready.then(() => tone('click')).catch(() => {});
 }, true);
 return {syllable: index => tone('voice', index)};
})();

function showFloating(element, parent) {
 const canUseTopLayer = typeof element.showPopover === 'function';
 if (element.parentElement !== parent) {
  hideFloating(element);
  parent.append(element);
 }
 element.hidden = false;
 if (canUseTopLayer && !element.matches(':popover-open')) {
  element.setAttribute('popover', 'manual');
  element.showPopover();
 }
}
function hideFloating(element) {
 if (typeof element.hidePopover === 'function' && element.matches(':popover-open')) element.hidePopover();
 element.hidden = true;
}

const ButtonHints = (() => {
 const tip = $('#control-tooltip');
 const actions = {
  'start-tour':'跟着小邻试一次任务', 'try-example':'用一句示例文字试一次',
  create:'填写你想处理的任务', demo:'一次运行 12 条演示任务',
  export:'下载任务记录表', example:'把示例文字填入表单',
  'next-create':'用自己的内容再试一次', 'close-dialog':'关闭当前窗口',
  'close-detail':'回到任务列表', refresh:'重试连接协调服务',
  'detail-refresh':'获取任务的最新状态', experiments:'读取已有的实验结果',
  'reset-filters':'显示所有任务', 'download-output':'下载这项任务的处理结果',
  'clear-files':'清除当前选择的文件', 'business-example':'用少量示例内容体验这项任务'
 };
 const pages = {
  overview:'查看正在运行的任务和电脑', nodes:'查看在线电脑与剩余处理能力',
  tasks:'查找任务的进度和结果', experiments:'比较不同的分配方式',
  guide:'查看如何连接另一台电脑'
 };
 const templates = {
  inspection:'把巡检内容整理成待办清单', log_analysis:'在你的日志中找出异常',
  document_search:'从你提供的文档中找到相关原文', archive:'遮盖敏感内容后下载副本'
 };
 function description(element) {
  const data = element.dataset;
  if (data.tip) return data.tip;
  if (data.action && actions[data.action]) return actions[data.action];
  if (data.page) return pages[data.page];
  if (data.template) return templates[data.template];
  if (data.detail) return '看哪台电脑处理了它，以及处理结果';
  if (data.cancel) return '停止等待或请求停止正在处理的任务';
  if (data.node) return data.drain === 'true' ? '暂时不让这台电脑接新任务' : '让这台电脑继续接任务';
  if (data.trust) return data.value === 'true' ? '允许这台电脑接收仅限可信设备的任务' : '取消这台电脑的可信权限';
  if (data.topologyNode) return '查看这台电脑负责的任务';
  if (data.filter) return data.filter === 'all' ? '查看所有状态的任务' : '只显示这种状态的任务';
  if (data.scenario) return '查看这种条件下的策略对比';
  if (data.offloadPreset) return '用这组网络参数试算耗时';
  if (data.guideStep) return '查看这一步的接入方法';
  if (data.npc === 'next') return '继续引导';
  if (element.matches('summary')) return element.parentElement.open ? '收起这部分说明' : '展开这部分说明';
  if (element.matches('.brand')) return '返回工作台';
  if (element.matches('[href="/guide"]')) return '打开系统使用说明';
  if (element.matches('a[href^="#"]')) return pages[element.hash.slice(1)];
  return '';
 }
 let control, timer, visible = false;
 function place() {
  if (!visible) return;
  if (!control.isConnected || !control.getClientRects().length) { clear(); return; }
  const rect = control.getBoundingClientRect();
  const {width,height} = tip.getBoundingClientRect();
  const left = Math.max(8,Math.min(innerWidth-width-8,rect.left+(rect.width-width)/2));
  const top = rect.top-height-9 >= 8 ? rect.top-height-9 : Math.min(innerHeight-height-8,rect.bottom+9);
  tip.style.left = left+'px';
  tip.style.top = top+'px';
 }
 function clear() {
  clearTimeout(timer);
  if (visible && control) {
   const ids = (control.getAttribute('aria-describedby') || '').split(/\s+/).filter(id=>id && id!==tip.id);
   if (ids.length) control.setAttribute('aria-describedby',ids.join(' '));
   else control.removeAttribute('aria-describedby');
  }
  control = null;
  visible = false;
  hideFloating(tip);
 }
 function suggest(element) {
  if (element === control) return;
  clear();
  const text = element && description(element);
  if (!text) return;
  control = element;
  timer = setTimeout(() => {
   if (!control?.isConnected) { clear(); return; }
   tip.textContent = text;
   showFloating(tip,control.closest('dialog[open]') || document.body);
   const ids = control.getAttribute('aria-describedby');
   control.setAttribute('aria-describedby',ids ? ids+' '+tip.id : tip.id);
   visible = true;
   place();
  }, 230);
 }
 document.addEventListener('pointerover', event => {
  if (event.pointerType === 'mouse' || event.pointerType === 'pen') suggest(event.target.closest(clickControls));
 });
 document.addEventListener('pointerout', event => {
  if (control?.contains(event.target) && !control.contains(event.relatedTarget)) clear();
 });
 document.addEventListener('focusin', event => {
  if (event.target.matches(':focus-visible')) suggest(event.target.closest(clickControls));
 });
 document.addEventListener('focusout', event => {
  if (control === event.target) clear();
 });
 document.addEventListener('click',clear,true);
 document.addEventListener('scroll',place,true);
 window.addEventListener('resize',place);
 return {clear,place};
})();

const FirstRun = (() => {
 const layer = $('#npc-tour');
 const card = $('.npc-card',layer);
 const avatar = $('.npc-avatar',layer);
 const next = $('[data-npc="next"]',layer);
 const exit = $('[data-npc="exit"]',layer);
 const dialog = $('#detail-dialog');
 const reduced = matchMedia('(prefers-reduced-motion: reduce)');
 const steps = {
  sample:{number:1, page:'overview', next:'先看执行电脑'},
  result:{number:2, next:'看执行电脑'},
  nodes:{number:3, page:'nodes', next:'查看任务记录'},
  tasks:{number:4, page:'tasks', next:'完成引导'}
 };
 let step, target, phrase = '', speechTimer = 0, frame = 0, followUntil = 0, exampleFailure = '', focusNext = false;
 function speak(message) {
  if (message === phrase) return;
  phrase = message;
  clearInterval(speechTimer);
  $('#npc-measure').textContent = message;
  $('#npc-announcement').textContent = message;
  const output = $('#npc-text');
  const letters = Array.from(message);
  if (reduced.matches || !letters.length) {
   output.textContent = message;
   avatar.removeAttribute('data-speaking');
   if (letters.length) AudioFeedback.syllable(0);
   return;
  }
  let count = 1, voiced = 0;
  output.textContent = letters[0];
  avatar.dataset.speaking = 'true';
  AudioFeedback.syllable(voiced++);
  speechTimer = setInterval(() => {
   count++;
   output.textContent = letters.slice(0,count).join('');
   if (count < letters.length && count % 3 === 0 && !/[，。！？、：\s]/.test(letters[count-1])) {
    AudioFeedback.syllable(voiced++);
   }
   if (count >= letters.length) {
    clearInterval(speechTimer);
    avatar.removeAttribute('data-speaking');
   }
  }, 28);
 }
 function draw(now) {
  frame = 0;
  if (!step) return;
  const w = innerWidth, h = innerHeight;
  const rect = target?.isConnected ? target.getBoundingClientRect() : null;
  const clamp = (value, min, max) => Math.min(max,Math.max(min,value));
  const left = rect ? clamp(rect.left-5,0,w) : 0;
  const right = rect ? clamp(rect.right+5,0,w) : 0;
  const top = rect ? clamp(rect.top-5,0,h) : h;
  const bottom = rect ? clamp(rect.bottom+5,0,h) : h;
  const visible = right>left && bottom>top;
  layer.dataset.spotlight = String(visible);
  for (const [side,value] of Object.entries({left,right,top:visible?top:h,bottom})) {
   layer.style.setProperty('--spot-'+side,value+'px');
  }
  const cw = card.offsetWidth, ch = card.offsetHeight, gap = 13;
  let x = (w-cw)/2, y = h-ch-gap;
  if (visible) {
   x = (left+right-cw)/2;
   if (bottom+gap+ch <= h-gap) y = bottom+gap;
   else if (top-gap-ch >= gap) y = top-gap-ch;
   else if (right+gap+cw <= w-gap) { x = right+gap; y = (top+bottom-ch)/2; }
   else if (left-gap-cw >= gap) { x = left-gap-cw; y = (top+bottom-ch)/2; }
   else y = top>h-bottom ? gap : h-ch-gap;
  }
  card.style.left = clamp(x,gap,w-cw-gap)+'px';
  card.style.top = clamp(y,gap,Math.max(gap,h-ch-gap))+'px';
  if (now < followUntil) queueDraw();
 }
 function queueDraw() {
  if (step && !frame) frame = requestAnimationFrame(draw);
 }
 function content() {
  if (step === 'sample') {
   const example = $('#tour-example');
   const canRun = example && exampleNodes().length && !lastError;
   if (canRun) return [example, exampleFailure || '你好，我是小邻！点亮着的按钮，试试给一句巡检文字分类。'];
   if (!state) return [$('#connection-notice:not([hidden]) button'), '工作台正在等连接。连接后可以试跑示例。'];
   return [$('[data-page="nodes"]'), exampleFailure || '暂时没有可接任务的电脑。点「算力节点」看看连接状态。'];
  }
  if (step === 'result') {
   const result = $('.task-outcome',dialog) || $('.dialog-head',dialog);
   const message = activeTask?.status === 'completed'
    ? '做完了！这里能看到分类结果、哪台电脑做的，以及用了多久。'
    : ['failed','cancelled'].includes(activeTask?.status)
     ? '这条任务结束了。你可以看上方的状态，再去检查执行电脑。'
     : '结果会在这里自动出现。你可以等它做完，也可以先去看执行电脑。';
   return [result,message];
  }
  if (step === 'nodes') return [$('.node-card .node-header') || $('.node-grid .empty'), '这里能看到哪些电脑在线。点下一步，就能找到之前提交的任务。'];
  return [$('.heading-actions [data-action="create"]'), '任务中心保存了进度和结果。想试自己的内容，点亮着的「创建任务」。'];
 }
 function sync() {
  if (!step || (steps[step].page && steps[step].page !== current)) return;
  if ($('#login-dialog').open) { stop(false); return; }
  const parent = step === 'result' && dialog.open ? dialog : document.body;
  showFloating(layer,parent);
  const [nextTarget, message] = content();
  speak(message);
  if (nextTarget !== target) {
   target = nextTarget;
   target?.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
   followUntil = performance.now()+380;
  }
  queueDraw();
  if (focusNext) { focusNext = false; next.focus({preventScroll:true}); }
 }
 function show(key) {
  ButtonHints.clear();
  step = key;
  target = null;
  exampleFailure = '';
  const info = steps[key];
  if (key !== 'result' && dialog.open) dialog.close();
  $('#npc-count').textContent = info.number+' / 4';
  next.textContent = info.next;
  focusNext = true;
  if (info.page && current !== info.page) {
   location.hash = info.page;
   navigate();
  } else sync();
 }
 function stop(restore = true) {
  step = null;
  target = null;
  phrase = '';
  clearInterval(speechTimer);
  cancelAnimationFrame(frame);
  frame = 0;
  avatar.removeAttribute('data-speaking');
  ButtonHints.clear();
  hideFloating(layer);
  document.body.append(layer);
  if (restore) (dialog.open ? $('.icon-button',dialog) : $('#npc-start')).focus({preventScroll:true});
 }
 layer.addEventListener('click',event => {
  const action = event.target.closest('[data-npc]')?.dataset.npc;
  if (action === 'exit') stop();
  if (action === 'next') {
   if (step === 'sample' || step === 'result') show('nodes');
   else if (step === 'nodes') show('tasks');
   else stop();
  }
 });
 document.addEventListener('keydown',event => {
  if (!step) return;
  if (event.key === 'Escape') {
   event.preventDefault();
   event.stopPropagation();
   stop();
  } else if (event.key === 'Tab') {
   const controls = [target?.matches(clickControls) ? target : null, exit, next]
    .filter(element=>element?.isConnected && !element.disabled);
   const index = controls.indexOf(document.activeElement);
   const nextIndex = index<0 ? (event.shiftKey ? controls.length-1 : 0)
    : (index+(event.shiftKey ? -1 : 1)+controls.length)%controls.length;
   event.preventDefault();
   controls[nextIndex].focus({preventScroll:true});
  }
 },true);
 document.addEventListener('scroll',queueDraw,true);
 window.addEventListener('resize',queueDraw);
 return {
  start:()=>show('sample'), is:key=>step===key, show, stop, sync,
  navigated() {
   if (step === 'sample' && current === 'nodes') show('nodes');
   else if (step && steps[step].page && steps[step].page !== current) stop(false);
   else sync();
  },
  exampleError(message) {
   if (step === 'sample') { exampleFailure = message; sync(); }
  }
 };
})();

navigate();refresh(true);setInterval(refresh,1500);
