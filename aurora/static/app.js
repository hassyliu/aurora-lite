'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const state = {user:null, page:'overview', serverId:null, servers:[], rules:[], tasks:[], destinations:[], overview:null, search:'', method:'all', refreshing:false, sending:{}, inspectionId:null, reordering:false, drag:null};
const titles = {
  overview:['NETWORK OVERVIEW','网络总览','让每一条连接，都清晰可见。','总览'],
  servers:['YOUR INFRASTRUCTURE','服务器','选择一台服务器，进入转发管理。','服务器'],
  rules:['SERVER FORWARDING','转发管理','管理当前服务器的转发规则。','转发管理'],
  destinations:['DESTINATION LIBRARY','落地管理','保存常用目标地址，创建转发时快速选用。','落地管理'],
  tasks:['ACTIVITY & LOGS','任务日志','每一次下发、启停和检查，都有迹可循。','任务日志'],
  settings:['WORKSPACE SETTINGS','工作空间设置','保持轻量，也保持掌控。','设置']
};
const labels = {online:'连接正常',unchecked:'待检查',running:'运行中',stopped:'已停止',error:'异常',unknown:'待确认',pending:'等待执行',succeeded:'已完成',failed:'失败',interrupted:'已中断'};
const actions = {check:'检查连接',prepare:'安装中转组件',apply:'下发规则',stop:'停止规则',remove:'删除规则',logs:'读取服务日志',diagnose:'状态检测'};
const badge = value => `<span class="badge ${['online','running','succeeded'].includes(value)?'good':['failed','error','interrupted'].includes(value)?'bad':['pending','unknown'].includes(value)?'pending':''}">${esc(labels[value]||value)}</span>`;
const methodBadge = method => `<span class="method ${method==='gost'?'gost':''}">${method==='gost'?'GOST':'iptables'}</span>`;
const date = timestamp => timestamp ? new Date(timestamp*1000).toLocaleString('zh-CN',{hour12:false}) : '尚未检查';
function bytes(value, split=false) {
  let number=Number(value)||0, index=0;
  while(number>=1024 && index<4){number/=1024;index++;}
  const amount=number.toFixed(index && number<100 ? 1:0), unit=['B','KB','MB','GB','TB'][index];
  return split?`${amount}<small>${unit}</small>`:`${amount} ${unit}`;
}
function toast(message,error=false){const node=document.createElement('div');node.className='toast'+(error?' error':'');node.textContent=message;$('#toasts').append(node);setTimeout(()=>node.remove(),5000);}
function showLogin(){state.user=null;$('#login-view').hidden=false;$('#console').hidden=true;document.querySelectorAll('dialog[open]').forEach(d=>d.close());}
function showConsole(){ $('#login-view').hidden=true;$('#console').hidden=false;$('#admin-name').textContent=state.user; }
async function api(path, options={}) {
  const response=await fetch('/api'+path,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json','X-Aurora-Request':'1',...(options.headers||{})}});
  let data;try{data=await response.json();}catch{throw new Error(`服务器响应异常（${response.status}）`);}
  if(!response.ok){
    if(response.status===401 && path!='/login')showLogin();
    const detail=Array.isArray(data.detail)?data.detail.map(item=>`${item.loc.at(-1)}：${item.msg}`).join('\n'):(data.detail||'操作失败');
    throw new Error(detail);
  }
  return data;
}
function empty(title,description,button='',action='server',symbol='server') {
  return `<div class="empty"><div class="empty-icon">${icon(symbol)}</div><h3>${title}</h3><p>${description}</p>${button?`<button class="btn primary tiny" data-new="${action}">${icon('plus')}${button}</button>`:''}</div>`;
}
function statCard(title,value,foot,symbol) {
  return `<div class="stat"><div class="stat-top"><span>${title}</span><span class="stat-icon">${icon(symbol)}</span></div><div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`;
}
const endpoint = (host,port) => `${host.includes(':')?'['+host+']':host}:${port}`;
const serverBusy = id => !!state.sending[id] || state.tasks.some(t=>t.server_id===id&&['pending','running'].includes(t.state));
function checking(serverId,action,ruleId=null){
  const sending=state.sending[serverId];
  return (sending?.action===action&&(!ruleId||sending.rule_id===ruleId))||state.tasks.some(t=>t.server_id===serverId&&t.action===action&&(!ruleId||t.rule_id===ruleId)&&['pending','running'].includes(t.state));
}
function checkBadge(status){
  const names={passed:'检查通过',failed:'发现异常',partial:'部分待验证',unknown:'未确认',inactive:'未启用',checking:'检测中',unchecked:'未检测'};
  return `<span class="badge ${status==='passed'?'good':status==='failed'?'bad':['partial','unknown','checking'].includes(status)?'pending':''}">${names[status]||names.unchecked}</span>`;
}
function connectionButton(server){return `<button class="btn secondary tiny" data-server-action="check" data-id="${server.id}" ${serverBusy(server.id)?'disabled':''}>${checking(server.id,'check')?'检查中…':'检查连接'}</button>`;}
function connectionFeedback(server){
  if(checking(server.id,'check'))return '<div class="connection-feedback pending" role="status"><span class="spinner"></span><div><strong>正在检查 SSH 连接与管理权限…</strong><small>结果会直接显示在这里。</small></div></div>';
  const result=server.check_result;
  if(!result)return '<div class="connection-feedback muted"><small>点击“检查连接”，直接查看检查结果。</small></div>';
  const good=result.status==='passed';
  return `<div class="connection-feedback ${good?'good':'bad'}"><div>${checkBadge(result.status)} <strong>${esc(result.summary)}</strong><small>${esc(date(result.checked_at))}${result.latency_ms!==undefined?` · 检查耗时 ${result.latency_ms} ms`:''}</small>${result.components?`<div class="component-list">${Object.entries(result.components).map(([name,installed])=>`<span>${name==='gost'?'GOST':esc(name)} · ${installed?'已就绪':'未安装'}</span>`).join('')}</div>`:''}${result.detail?`<p class="check-error">${esc(result.detail)}</p>`:''}</div></div>`;
}
function ruleInspectionCell(rule){
  const pending=checking(rule.server_id,'diagnose',rule.id),result=rule.check_result;
  return `<div class="inspection-summary">${checkBadge(pending?'checking':result?.status||'unchecked')}<small>${result?esc(date(result.checked_at)):'尚未检测'}</small><div class="row-actions"><button class="accent" data-rule-action="diagnose" data-id="${rule.id}" ${serverBusy(rule.server_id)?'disabled':''}>${pending?'检测中…':'检测'}</button><button data-inspect="${rule.id}" ${!result&&!pending?'disabled':''}>详情</button></div></div>`;
}
function openInspection(id){state.inspectionId=id;renderInspection();if(!$('#inspector').open)$('#inspector').showModal();}
function renderInspection(){
  const rule=state.rules.find(r=>r.id===state.inspectionId);if(!rule){if($('#inspector').open)$('#inspector').close();return;}
  $('#inspection-title').textContent=rule.name+' · 状态检测';
  const pending=checking(rule.server_id,'diagnose',rule.id),result=rule.check_result;
  $('#inspection-content').innerHTML=pending?'<div class="check-wait"><span class="spinner"></span><strong>正在检查服务、规则和网络连通性…</strong><p>这项操作不会启停或修改转发规则。</p></div>':result?`<div class="inspection-heading">${checkBadge(result.status)}<strong>${esc(result.summary)}</strong><small>${esc(date(result.checked_at))}</small></div>${result.detail?`<div class="notice danger">${esc(result.detail)}</div>`:''}<div class="check-list">${(result.checks||[]).map(item=>`<div class="check-item"><div><strong>${esc(item.title)}</strong>${checkBadge(item.status)}</div><p>${esc(item.detail||'')}${item.latency_ms!==undefined?` · ${item.latency_ms} ms`:''}${item.peer?` · ${esc(item.peer)}`:''}</p></div>`).join('')}</div>${result.note?`<p class="form-hint">${esc(result.note)}</p>`:''}`:'<p class="form-hint">点击“重新检测”开始检查当前规则。</p>';
  $('#inspection-repeat').dataset.id=rule.id;$('#inspection-repeat').disabled=serverBusy(rule.server_id);
}
function destinationsPage(){
  if(!state.destinations.length)return `<div class="panel">${empty('添加常用落地地址','保存 IP / 域名和端口，创建转发时一键填入。','添加落地','destination','destination')}</div>`;
  return `<div class="panel"><div class="panel-head"><div><h2>常用落地</h2><p>拖动左侧手柄排序，也可使用上移 / 下移按钮。</p></div><span class="badge">${state.destinations.length} 个地址</span></div><div class="destination-list" role="list" aria-label="落地地址列表">${state.destinations.map((d,index)=>`<article class="destination-row" role="listitem" data-destination="${d.id}"><button class="drag-handle" data-drag-destination="${d.id}" aria-label="拖动排序 ${esc(d.name)}" title="按住拖动排序" ${state.reordering?'disabled':''}>${icon('grip')}</button><div class="destination-copy"><h3>${esc(d.name)}</h3><p class="mono">${esc(endpoint(d.host,d.port))}</p>${d.notes?`<small>${esc(d.notes)}</small>`:''}</div><div class="destination-actions"><button class="icon-btn" data-move-destination="${d.id}" data-offset="-1" aria-label="上移 ${esc(d.name)}" ${!index||state.reordering?'disabled':''}>${icon('up')}</button><button class="icon-btn" data-move-destination="${d.id}" data-offset="1" aria-label="下移 ${esc(d.name)}" ${index===state.destinations.length-1||state.reordering?'disabled':''}>${icon('down')}</button><button class="btn secondary tiny" data-edit-destination="${d.id}">编辑</button><button class="btn secondary tiny" data-delete-destination="${d.id}">删除</button></div></article>`).join('')}</div><p class="destination-note">${state.reordering?'正在保存排序…':'排序已保存于服务器，创建转发时按此顺序显示。'}</p></div>`;
}
function openDestination(id){
  const d=state.destinations.find(d=>d.id===id)||{};editorType='destination';editorId=id||null;$('#editor-title').textContent=id?'编辑落地':'添加落地';
  $('#editor-body').innerHTML=`<div class="form-grid"><label class="full">落地名称<input name="name" value="${esc(d.name)}" placeholder="例如：香港落地 · Web" maxlength="80" required></label><label>IP / 域名<input name="host" value="${esc(d.host)}" placeholder="203.0.113.10 或 example.com" required></label><label>目标端口<input name="port" value="${d.port||''}" type="number" min="1" max="65535" placeholder="例如 443" required></label><label class="full">备注<textarea name="notes" maxlength="1000">${esc(d.notes)}</textarea></label><p class="form-hint full">用于快速填入转发目标。修改或删除落地地址不会改动已有转发规则。</p></div>`;
  $('#editor-error').textContent='';$('#editor').showModal();
}
function trafficChart(samples) {
  const now=Math.floor(Date.now()/3600000)*3600, hours=Array.from({length:24},(_,i)=>now-(23-i)*3600);
  const lookup=new Map(samples.map(s=>[s.hour,s]));
  const maximum=Math.max(1,...samples.flatMap(s=>[s.bytes_in,s.bytes_out]));
  const hasData=samples.some(s=>s.bytes_in||s.bytes_out);
  return `<div class="chart"><span class="chart-axis">${hasData?bytes(maximum):'流量'}</span>${hours.map(hour=>{
    const s=lookup.get(hour)||{bytes_in:0,bytes_out:0};
    return `<div class="chart-col" title="${esc(date(hour))} · 入站 ${bytes(s.bytes_in)} / 出站 ${bytes(s.bytes_out)}"><span class="chart-bar" style="height:${s.bytes_in/maximum*94}%"></span><span class="chart-bar out" style="height:${s.bytes_out/maximum*94}%"></span></div>`;
  }).join('')}${hasData?'':'<div class="chart-empty"><span>等待第一条流量记录</span><small>启用中转规则后，采集数据将在这里显示</small></div>'}</div><div class="chart-labels">${[0,6,12,18,23].map(i=>`<span>${new Date(hours[i]*1000).getHours().toString().padStart(2,'0')}:00</span>`).join('')}</div>`;
}
const serverRulesLink = id => `#servers/${encodeURIComponent(id)}/rules`;
const currentServer = () => state.servers.find(server=>server.id===state.serverId);
const serverRules = () => state.rules.filter(rule=>rule.server_id===state.serverId);
function ruleRows(rules,compact=false) {
  return rules.map(rule=>`<tr><td><strong>${compact?`<a class="rule-link" href="${serverRulesLink(rule.server_id)}">${esc(rule.name)}</a>`:esc(rule.name)}</strong>${compact?`<small>${esc(rule.server_name)}</small>`:''}</td><td>${methodBadge(rule.method)}<small>${rule.protocol==='both'?'TCP + UDP':esc(rule.protocol.toUpperCase())}</small></td><td class="mono">${esc(rule.listen_ip)}:${rule.listen_port}<small>→ ${esc(rule.target_host)}:${rule.target_port}</small></td><td>${badge(rule.state)}${rule.last_error?`<small title="${esc(rule.last_error)}">${esc(rule.last_error.slice(-45))}</small>`:''}</td>${compact?'':`<td class="inspection-cell">${ruleInspectionCell(rule)}</td>`}<td class="mono">${bytes(rule.bytes_in+rule.bytes_out)}<small>${rule.sampled_at?'采集于 '+esc(new Date(rule.sampled_at*1000).toLocaleTimeString('zh-CN')):'等待采集'}</small></td>${compact?'':`<td><div class="row-actions"><button class="accent" data-rule-action="apply" data-id="${rule.id}">下发</button><button data-rule-action="stop" data-id="${rule.id}">停止</button><button data-edit-rule="${rule.id}">编辑</button><button data-preview="${rule.id}">配置</button><button data-rule-action="logs" data-id="${rule.id}">日志</button><button class="red" data-rule-action="remove" data-id="${rule.id}">删除</button></div></td>`}</tr>`).join('');
}
function ruleTable(rules,compact=false){return `<div class="table-wrap"><table><thead><tr><th>${compact?'规则 / 服务器':'转发规则'}</th><th>中转方式</th><th>监听 → 目标</th><th>运行状态</th>${compact?'':'<th>状态检测</th>'}<th>累计流量</th>${compact?'':'<th>操作</th>'}</tr></thead><tbody>${ruleRows(rules,compact)}</tbody></table></div>`;}
function overviewPage(){
  const o=state.overview, s=o.servers,r=o.rules;
  return `<div class="stats-grid">${statCard('服务器',`${s.total}<small>台</small>`,`<b>${s.online||0}</b> 台已验证连接`,'server')}${statCard('运行中的规则',`${r.running||0}<small>/ ${r.total||0}</small>`,'仅显示已确认的运行状态','route')}${statCard('累计入站流量',bytes(r.bytes_in,true),'从首次采集开始累计','down')}${statCard('累计出站流量',bytes(r.bytes_out,true),'按规则统计返回流量','up')}</div>
  <div class="overview-middle"><div class="panel"><div class="panel-head"><div><h2>流量趋势</h2><p>过去 24 小时 · 每小时汇总</p></div><div class="legend"><span><i></i>入站</span><span><i class="out"></i>出站</span></div></div>${trafficChart(o.samples)}</div>
  <div class="panel"><div class="panel-head"><div><h2>中转引擎</h2><p>专注于你需要的两种方式</p></div></div><div class="engine-list"><div class="engine"><div class="engine-logo">↗</div><div><div class="engine-name">iptables</div><div class="engine-description">内核端口转发</div></div><div class="engine-count">${state.rules.filter(r=>r.method==='iptables').length}<small> 条</small></div></div><div class="engine"><div class="engine-logo warm">G</div><div><div class="engine-name">GOST</div><div class="engine-description">TCP / UDP 转发</div></div><div class="engine-count">${state.rules.filter(r=>r.method==='gost').length}<small> 条</small></div></div><div class="engine-note">配置保存与远程下发分开执行。<br>规则启用后由中转机持续运行。</div></div></div></div>
  <div class="panel"><div class="panel-head"><div><h2>最近创建的规则</h2><p>点击规则，进入所属服务器的转发管理</p></div><a class="panel-link" href="#servers">管理服务器 ${icon('arrow')}</a></div>${state.rules.length?ruleTable(state.rules.slice(0,5),true):empty('还没有中转规则','进入服务器的转发管理，创建你的第一条规则。',state.servers.length?'选择服务器':'添加服务器',state.servers.length?'select-server':'server','route')}</div>`;
}
function serversPage(){
  if(!state.servers.length)return `<div class="panel">${empty('连接你的第一台服务器','使用 SSH 密码或私钥连接 Debian / Ubuntu 中转机。添加后可检查连接并安装中转组件。','添加服务器')}</div>`;
  return `<div class="server-grid">${state.servers.map(server=>`<article class="panel server-card"><div class="server-card-top"><div class="stat-icon">${icon('server')}</div><div class="server-identity"><h3><a href="${serverRulesLink(server.id)}">${esc(server.name)}</a></h3><p class="mono">${esc(server.host)}:${server.port}</p></div>${badge(server.state)}</div><div class="server-info"><div><span>SSH 账号</span>${esc(server.username)} · ${server.auth_type==='key'?'私钥':'密码'}</div><div><span>转发规则</span><a class="rule-link" href="${serverRulesLink(server.id)}">${server.rule_count} 条 ${icon('arrow')}</a></div><div><span>最近检查</span>${esc(date(server.last_checked))}</div><div><span>主机指纹</span><div class="mono" title="${esc(server.fingerprint)}">${esc(server.fingerprint.slice(0,21))}…</div></div></div><div class="server-buttons"><a class="btn primary tiny" href="${serverRulesLink(server.id)}">${icon('route')}转发管理</a>${connectionButton(server)}<button class="btn secondary tiny" data-server-action="prepare" data-id="${server.id}">安装中转组件</button><button class="btn secondary tiny" data-edit-server="${server.id}">编辑</button><button class="icon-btn" data-delete-server="${server.id}" aria-label="删除服务器 ${esc(server.name)}">×</button></div>${connectionFeedback(server)}${server.notes?`<p class="server-notes">${esc(server.notes)}</p>`:''}${server.last_error&&!server.check_result?`<div class="error-line">${esc(server.last_error)}</div>`:''}</article>`).join('')}</div>`;
}
function filteredRules(){const q=state.search.toLowerCase();return serverRules().filter(r=>(state.method==='all'||r.method===state.method)&&[r.name,r.listen_ip,r.target_host,String(r.listen_port),String(r.target_port)].some(v=>v.toLowerCase().includes(q)));}
function rulesPage(){
  const server=currentServer();
  if(!server)return `<div class="panel">${empty('服务器不存在或已删除','返回服务器列表，选择一台可用的服务器。','返回服务器列表','select-server')}</div>`;
  const rules=serverRules(),incoming=rules.reduce((sum,r)=>sum+r.bytes_in,0),outgoing=rules.reduce((sum,r)=>sum+r.bytes_out,0);
  return `<div class="panel server-context"><div class="server-context-heading"><div class="stat-icon">${icon('server')}</div><div class="server-identity"><h2>${esc(server.name)}</h2><p class="mono">${esc(server.host)}:${server.port} · ${esc(server.username)}</p></div>${badge(server.state)}</div><div class="server-context-actions">${connectionButton(server)}<button class="btn secondary tiny" data-edit-server="${server.id}">服务器设置</button></div>${connectionFeedback(server)}</div>
  <div class="stats-grid">${statCard('转发规则',`${rules.length}<small>条</small>`,'当前服务器的全部规则','route')}${statCard('运行中的规则',`${rules.filter(r=>r.state==='running').length}<small>条</small>`,'已确认正在运行','server')}${statCard('累计入站流量',bytes(incoming,true),'当前服务器的规则合计','down')}${statCard('累计出站流量',bytes(outgoing,true),'当前服务器的规则合计','up')}</div>
  <div class="panel"><div class="toolbar"><div class="search-field">${icon('search')}<input id="rule-search" value="${esc(state.search)}" placeholder="搜索当前服务器的规则、地址或端口" aria-label="搜索转发规则"></div><select id="method-filter" aria-label="筛选中转方式"><option value="all">全部中转方式</option><option value="iptables" ${state.method==='iptables'?'selected':''}>iptables</option><option value="gost" ${state.method==='gost'?'selected':''}>GOST</option></select><span class="count">共 ${rules.length} 条规则</span></div><div id="rule-list">${ruleListContent()}</div></div>`;
}
function ruleListContent(){const rules=serverRules(),filtered=filteredRules();return filtered.length?ruleTable(filtered):empty(rules.length?'没有匹配的规则':'这台服务器还没有转发规则',rules.length?'试试其他关键词或筛选条件。':'填写监听端口和目标地址，保存后点击“下发”启用。',rules.length?'':'添加转发','rule','route');}
function tasksPage(){return `<div class="panel"><div class="panel-head"><div><h2>执行记录</h2><p>显示最近 200 个任务 · 保留 30 天</p></div><span class="badge">${state.tasks.filter(t=>['pending','running'].includes(t.state)).length} 个待完成</span></div>${state.tasks.length?`<div class="table-wrap"><table><thead><tr><th>任务</th><th>服务器</th><th>状态</th><th>创建时间</th><th>耗时</th><th></th></tr></thead><tbody>${state.tasks.map(task=>`<tr><td><strong>${esc(actions[task.action]||task.action)}</strong><small class="mono">${task.id}</small></td><td>${esc(state.servers.find(s=>s.id===task.server_id)?.name||'已删除的服务器')}</td><td>${badge(task.state)}</td><td class="mono">${esc(date(task.created))}</td><td>${task.finished&&task.started?Math.max(0,task.finished-task.started).toFixed(1)+'s':'—'}</td><td><button class="btn secondary tiny" data-task-log="${task.id}">查看日志</button></td></tr>`).join('')}</tbody></table></div>`:empty('所有操作，都有记录','下发规则、检查连接或安装组件后，可在这里查看执行结果。','','','task')}</div>`;}
function settingsPage(){return `<div class="settings-grid"><div class="panel"><div class="panel-head"><div><h2>运行环境</h2><p>简单、独立的个人控制台</p></div></div><div class="settings-body"><div class="setting-row"><span>版本</span><strong>v${esc(state.overview.version)}</strong></div><div class="setting-row"><span>中转方式</span><strong>iptables / GOST v3</strong></div><div class="setting-row"><span>数据存储</span><strong>SQLite 本地文件</strong></div><div class="setting-row"><span>采集间隔</span><strong>${state.overview.poll_seconds} 秒</strong></div><button id="export-config" class="btn secondary">${icon('down')}导出配置清单</button><button id="settings-logout" type="button" class="btn secondary mobile-logout">退出登录</button><p>配置清单不包含 SSH 凭据。完整备份请在主控机使用备份命令，同时保存数据库和加密密钥。</p><div class="notice">iptables 统计 IP 层字节；GOST 统计转发数据字节。两者口径不同。主控离线或中转服务重启之间尚未采集的流量可能无法补回。</div></div></div><div class="panel"><div class="panel-head"><div><h2>修改登录密码</h2><p>修改后所有登录会话将失效</p></div></div><form id="password-form" class="settings-body"><label>当前密码<input name="current_password" type="password" autocomplete="current-password" required></label><label>新密码<input name="new_password" type="password" autocomplete="new-password" minlength="12" maxlength="256" placeholder="至少 12 个字符" required></label><label>再次输入新密码<input name="confirm_password" type="password" autocomplete="new-password" minlength="12" required></label><p id="password-error" class="form-error" role="alert"></p><button type="submit" class="btn primary">更新密码</button></form></div></div>`;}
function render(){
  if(!state.overview)return;
  const meta=titles[state.page],scoped=state.page==='rules',server=currentServer();
  $('#page-kicker').textContent=meta[0];$('#page-title').textContent=meta[1];$('#page-description').textContent=scoped&&server?`${server.name} · 在这里管理这台服务器的转发规则。`:meta[2];
  $('#breadcrumb-current').innerHTML=scoped?`<a href="#servers">服务器</a><span class="slash">/</span><span>${esc(server?.name||'服务器不存在')}</span><span class="slash">/</span><span>转发管理</span>`:esc(meta[3]);
  document.querySelectorAll('[data-page]').forEach(a=>a.classList.toggle('active',a.dataset.page===(scoped?'servers':state.page)));
  $('#primary-action').hidden=['tasks','settings'].includes(state.page)||(scoped&&!server);
  $('#primary-action span').textContent=scoped?'添加转发':state.page==='destinations'?'添加落地':'添加服务器';
  $('#back-to-servers').hidden=!scoped;
  $('#nav-server-count').textContent=state.servers.length;
  renderInspection();
  if(state.drag)return;
  const focused=document.activeElement;
  if(scoped && server && focused?.id==='rule-search'){$('#rule-list').innerHTML=ruleListContent();return;}
  if(state.page==='settings' && $('#password-form')?.contains(focused))return;
  $('#page-content').innerHTML=({overview:overviewPage,servers:serversPage,rules:rulesPage,destinations:destinationsPage,tasks:tasksPage,settings:settingsPage})[state.page]();
}
async function refresh(){
  if(!state.user||state.refreshing)return;state.refreshing=true;
  try{const [overview,servers,rules,tasks,destinations]=await Promise.all(['/overview','/servers','/rules','/tasks','/destinations'].map(p=>api(p)));Object.assign(state,{overview,servers,rules,tasks});if(!state.drag&&!state.reordering)state.destinations=destinations;$('#load-error').hidden=true;$('#connection-state').innerHTML='<span class="status-dot"></span> 面板已连接';render();}
  catch(error){$('#load-error').textContent=error.message;$('#load-error').hidden=false;$('#connection-state').textContent='连接中断 · 正在重试';}
  finally{state.refreshing=false;}
}
function navigate(){
  let page=location.hash.slice(1);
  if(page==='rules'){history.replaceState(null,'','#servers');page='servers';}
  const match=/^servers\/([a-f0-9]{12})\/rules$/.exec(page),serverId=match?match[1]:null;
  const nextPage=match?'rules':(titles[page]?page:'overview');
  if(state.page!==nextPage||state.serverId!==serverId){
    cancelDestinationDrag();
    state.search='';state.method='all';
    document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
  }
  state.page=nextPage;state.serverId=serverId;render();
}
let editorType=null, editorId=null;
const option=(value,label,current)=>`<option value="${esc(value)}" ${value===current?'selected':''}>${esc(label)}</option>`;
function openServer(id){
  const s=state.servers.find(s=>s.id===id)||{port:22,username:'root',auth_type:'password'};editorType='server';editorId=id||null;
  $('#editor-title').textContent=id?'编辑服务器':'添加服务器';
  $('#editor-body').innerHTML=`<div class="form-grid"><label class="full">服务器名称<input name="name" value="${esc(s.name)}" placeholder="例如：新加坡中转" required maxlength="80"></label><label>IP / 域名<input name="host" value="${esc(s.host)}" placeholder="203.0.113.10" required></label><label>SSH 端口<input name="port" type="number" min="1" max="65535" value="${s.port}" required></label><label>SSH 用户<input name="username" value="${esc(s.username)}" required></label><label>认证方式<select name="auth_type" id="auth-type">${option('password','密码',s.auth_type)}${option('key','私钥',s.auth_type)}</select></label><label class="full" id="credential-label">${s.auth_type==='key'?'SSH 私钥':'SSH 密码'}${s.auth_type==='key'?`<textarea name="credential" placeholder="${id?'留空保留原凭据':'粘贴完整私钥'}" ${id?'':'required'}></textarea>`:`<input name="credential" type="password" autocomplete="new-password" placeholder="${id?'留空保留原凭据':'输入 SSH 密码'}" ${id?'':'required'}>`}</label><label class="full" id="passphrase-label" ${s.auth_type==='key'?'':'hidden'}>私钥口令（如有）<input name="passphrase" type="password" autocomplete="new-password"></label><label class="full">SSH 主机指纹<div class="input-row"><input name="fingerprint" value="${esc(s.fingerprint)}" placeholder="SHA256:…" required><button class="btn secondary tiny" type="button" id="probe-host">读取指纹</button></div><span class="form-hint">请与服务器控制台的 SSH 主机指纹核对。非 root 用户需要免密 sudo；中转机需预装 Python 3。</span></label><label class="full">备注<textarea name="notes" maxlength="1000" placeholder="用途、位置或其他备注">${esc(s.notes)}</textarea></label></div>`;
  $('#editor-error').textContent='';$('#editor').showModal();
}
function openRule(id){
  if(!state.servers.length){toast('请先添加一台服务器');openServer();return;}
  const server=currentServer();
  if(!server){location.hash='#servers';toast('请先进入一台服务器的转发管理');return;}
  const r=id?serverRules().find(r=>r.id===id):{server_id:server.id,method:'iptables',protocol:'tcp',listen_ip:'0.0.0.0'};
  if(!r){toast('规则不存在，请刷新后重试',true);return;}
  if(id && r.state!=='stopped'){toast('请先成功停止规则，再修改配置',true);return;}
  editorType='rule';editorId=id||null;$('#editor-title').textContent=id?'编辑转发规则':'添加转发';
  $('#editor-body').innerHTML=`<div class="form-grid"><label class="full">规则名称<input name="name" value="${esc(r.name)}" placeholder="例如：游戏服务器中转" required maxlength="80"></label><label class="full">中转服务器<select name="server_id" disabled>${option(server.id,server.name+' · '+server.host,server.id)}</select><span class="form-hint">此规则属于当前服务器。</span></label><label>中转方式<select name="method">${option('iptables','iptables · 内核转发',r.method)}${option('gost','GOST · 应用转发',r.method)}</select></label><label>传输协议<select name="protocol">${option('tcp','TCP',r.protocol)}${option('udp','UDP',r.protocol)}${option('both','TCP + UDP',r.protocol)}</select></label><label>监听 IP<input name="listen_ip" value="${esc(r.listen_ip)}" required></label><label>监听端口<input name="listen_port" value="${r.listen_port||''}" placeholder="例如 10080" type="number" min="1" max="65535" required></label><label class="full">快速选择落地<select id="destination-picker"><option value="">手动填写目标</option>${state.destinations.map(d=>option(d.id,d.name+' · '+endpoint(d.host,d.port),'')).join('')}</select><span class="form-hint">${state.destinations.length?'选择后填入目标地址与端口，仍可手动调整。':'可先到“落地管理”保存常用地址。'}</span></label><label>目标 IP / 域名<input name="target_host" value="${esc(r.target_host)}" placeholder="目标服务器地址" required></label><label>目标端口<input name="target_port" value="${r.target_port||''}" placeholder="例如 443" type="number" min="1" max="65535" required></label><div class="form-hint full">0.0.0.0 监听全部 IPv4 地址，:: 监听 IPv6。iptables 目标需为同一 IP 版本的固定地址；域名或跨 IPv4/IPv6 使用 GOST。保存后点击“下发”才会启用。</div><label class="full">备注<textarea name="notes" maxlength="1000" placeholder="这条规则的用途">${esc(r.notes)}</textarea></label></div>`;
  $('#editor-error').textContent='';$('#editor').showModal();
}
function view(title,content){$('#viewer-title').textContent=title;$('#viewer-content').textContent=content;$('#viewer').showModal();}
let confirmationCallback=null;
function confirmAction(title,message,callback){$('#confirm-title').textContent=title;$('#confirm-message').textContent=message;confirmationCallback=callback;$('#confirmation').showModal();}
async function queue(path){
  const [,kind,id,action]=/^\/(servers|rules)\/([^/]+)\/([^/]+)$/.exec(path);
  const serverId=kind==='servers'?id:state.rules.find(r=>r.id===id)?.server_id;
  if(!serverId||serverBusy(serverId)){toast('这台服务器有任务正在执行，请稍后重试');return;}
  state.sending[serverId]={action,rule_id:kind==='rules'?id:null};render();
  if(action==='diagnose')openInspection(id);
  try{
    const result=await api(path,{method:'POST'});
    if(result.task_id)state.tasks.unshift({id:result.task_id,action,server_id:serverId,rule_id:kind==='rules'?id:null,state:'pending',created:Date.now()/1000});
    toast(action==='check'?'正在检查，结果将在服务器卡片显示':action==='diagnose'?'正在检测当前转发规则':'操作已提交，请在任务日志查看结果');
  }finally{delete state.sending[serverId];await refresh();render();}
}
async function handleClick(event){
  const button=event.target.closest('button');if(!button)return;
  const d=button.dataset;
  if(d.close){$('#'+d.close).close();return;}
  if(d.new==='select-server'){location.hash='#servers';return;}
  if(d.new){d.new==='server'?openServer():d.new==='destination'?openDestination():openRule();return;}
  if(d.editDestination){openDestination(d.editDestination);return;}
  if(d.inspect){openInspection(d.inspect);return;}
  if(d.moveDestination){await moveDestination(d.moveDestination,Number(d.offset));return;}
  if(d.deleteDestination){const item=state.destinations.find(item=>item.id===d.deleteDestination);confirmAction('删除落地',`删除「${item.name}」？已有转发规则保留当前目标地址。`,async()=>{await api('/destinations/'+item.id,{method:'DELETE'});toast('落地已删除');await refresh();});return;}
  if(d.editServer){openServer(d.editServer);return;}
  if(d.editRule){openRule(d.editRule);return;}
  if(d.taskLog){const task=state.tasks.find(t=>t.id===d.taskLog);view(actions[task.action]+' · '+labels[task.state],task.log||'任务尚未返回日志，请稍后刷新。');return;}
  if(d.preview){const preview=await api(`/rules/${d.preview}/preview`);view('生成的规则配置',JSON.stringify(preview.config,null,2)+'\n\n'+preview.service);return;}
  if(d.deleteServer){const server=state.servers.find(s=>s.id===d.deleteServer);confirmAction('删除服务器',`将删除「${server.name}」的连接信息。服务器仍有规则时无法删除。`,async()=>{await api('/servers/'+server.id,{method:'DELETE'});toast('服务器已删除');await refresh();});return;}
  if(d.serverAction==='prepare'){const server=state.servers.find(s=>s.id===d.id);confirmAction('安装中转组件',`将在「${server.name}」上通过 apt 安装 iptables、conntrack、curl，并下载校验 GOST v3.3.0。安装完成后再下发规则。`,()=>queue(`/servers/${d.id}/prepare`));return;}
  if(d.serverAction){await queue(`/servers/${d.id}/${d.serverAction}`);return;}
  if(d.ruleAction==='remove'){confirmAction('删除中转规则','此操作会停止并移除中转机上的对应服务，成功后删除本地规则及其统计记录。',()=>queue(`/rules/${d.id}/remove`));return;}
  if(d.ruleAction){await queue(`/rules/${d.id}/${d.ruleAction}`);return;}
  if(button.id==='primary-action'){state.page==='rules'?openRule():state.page==='destinations'?openDestination():openServer();return;}
  if(button.id==='refresh'){await refresh();return;}
  if(button.id==='logout'||button.id==='settings-logout'){await api('/logout',{method:'POST'});showLogin();return;}
  if(button.id==='confirm-yes'){const callback=confirmationCallback;$('#confirmation').close();confirmationCallback=null;if(callback)await callback();return;}
  if(button.id==='probe-host'){
    const form=$('#editor-form'),host=form.elements.host.value,port=Number(form.elements.port.value);button.disabled=true;
    try{const result=await api('/servers/probe',{method:'POST',body:JSON.stringify({host,port})});form.elements.fingerprint.value=result.fingerprint;toast('已读取指纹，请核对后保存');}finally{button.disabled=false;}return;
  }
  if(button.id==='export-config'){const content=await api('/export'),url=URL.createObjectURL(new Blob([JSON.stringify(content,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='aurora-inventory.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出配置清单（不含凭据）');}
}
async function saveDestinationOrder(ids){
  if(state.reordering)return;
  const items=new Map(state.destinations.map(d=>[d.id,d]));
  if(ids.length!==items.size||ids.some(id=>!items.has(id)))return;
  state.destinations=ids.map((id,index)=>({...items.get(id),position:index}));state.reordering=true;render();
  try{await api('/destinations/order',{method:'PUT',body:JSON.stringify({ids})});toast('落地排序已保存');}
  catch(error){toast(error.message,true);try{state.destinations=await api('/destinations');}catch{toast('排序未确认，请刷新页面',true);}}
  finally{state.reordering=false;render();}
}
async function moveDestination(id,offset){
  if(state.reordering||state.drag)return;
  const ids=state.destinations.map(d=>d.id),index=ids.indexOf(id),next=index+offset;
  if(index<0||next<0||next>=ids.length)return;
  [ids[index],ids[next]]=[ids[next],ids[index]];await saveDestinationOrder(ids);
}
function positionDestinationDrag(){
  const drag=state.drag;if(!drag||!drag.moved)return;
  const rows=[...drag.list.querySelectorAll('[data-destination]')].filter(row=>row!==drag.row);
  const before=rows.find(row=>{const rect=row.getBoundingClientRect();return drag.clientY<rect.top+rect.height/2;});
  if(before)drag.list.insertBefore(drag.row,before);else drag.list.append(drag.row);
}
function dragScroll(){
  const drag=state.drag;if(!drag)return;
  if(drag.moved){const step=drag.clientY<85?-12:drag.clientY>innerHeight-95?12:0;if(step){window.scrollBy(0,step);positionDestinationDrag();}}
  drag.frame=requestAnimationFrame(dragScroll);
}
function cancelDestinationDrag(){
  const drag=state.drag;if(!drag)return;state.drag=null;cancelAnimationFrame(drag.frame);
  if(drag.handle.hasPointerCapture(drag.pointerId))drag.handle.releasePointerCapture(drag.pointerId);
  drag.row.classList.remove('dragging');
}
document.addEventListener('pointerdown',event=>{
  const handle=event.target.closest('[data-drag-destination]');
  if(!handle||state.reordering||state.drag||event.button!==0)return;
  const row=handle.closest('[data-destination]');event.preventDefault();handle.focus();
  state.drag={handle,row,list:row.parentElement,pointerId:event.pointerId,startY:event.clientY,clientY:event.clientY,moved:false};
  handle.setPointerCapture(event.pointerId);dragScroll();
});
document.addEventListener('pointermove',event=>{
  const drag=state.drag;if(!drag||event.pointerId!==drag.pointerId)return;
  drag.clientY=event.clientY;
  if(Math.abs(drag.clientY-drag.startY)>6){drag.moved=true;drag.row.classList.add('dragging');}
  if(drag.moved){event.preventDefault();positionDestinationDrag();}
},{passive:false});
document.addEventListener('pointerup',event=>{
  const drag=state.drag;if(!drag||event.pointerId!==drag.pointerId)return;
  const ids=[...drag.list.querySelectorAll('[data-destination]')].map(row=>row.dataset.destination);
  const changed=ids.join()!==state.destinations.map(d=>d.id).join();cancelDestinationDrag();
  if(changed)saveDestinationOrder(ids);else render();
});
document.addEventListener('pointercancel',()=>{cancelDestinationDrag();render();});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&state.drag){cancelDestinationDrag();render();}
  const handle=event.target.closest('[data-drag-destination]');
  if(handle&&['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();moveDestination(handle.dataset.dragDestination,event.key==='ArrowUp'?-1:1);}
});
document.addEventListener('click',event=>handleClick(event).catch(error=>toast(error.message,true)));
document.addEventListener('input',event=>{if(event.target.id==='rule-search'){state.search=event.target.value;$('#rule-list').innerHTML=ruleListContent();}if(['target_host','target_port'].includes(event.target.name)&&$('#destination-picker'))$('#destination-picker').value='';});
document.addEventListener('change',event=>{
  if(event.target.id==='method-filter'){state.method=event.target.value;$('#rule-list').innerHTML=ruleListContent();}
  if(event.target.id==='destination-picker'){const item=state.destinations.find(d=>d.id===event.target.value);if(item){const form=$('#editor-form');form.elements.target_host.value=item.host;form.elements.target_port.value=item.port;if(form.elements.method.value==='iptables'&&!/^[0-9a-f:.]+$/i.test(item.host))toast('域名落地请使用 GOST；iptables 需要固定 IP。');}}
  if(event.target.id==='auth-type'){const key=event.target.value==='key';$('#credential-label').innerHTML=key?`SSH 私钥<textarea name="credential" placeholder="粘贴完整私钥" ${editorId?'':'required'}></textarea>`:`SSH 密码<input name="credential" type="password" autocomplete="new-password" ${editorId?'':'required'}>`;$('#passphrase-label').hidden=!key;}
});
$('#login-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target,button=$('button',form);button.disabled=true;$('#login-error').textContent='';
  try{const result=await api('/login',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(form)))});state.user=result.username;form.elements.password.value='';showConsole();navigate();await refresh();}catch(error){$('#login-error').textContent=error.message;}finally{button.disabled=false;}
});
$('#editor-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target,button=$('button[type=submit]',form),data=Object.fromEntries(new FormData(form));button.disabled=true;$('#editor-error').textContent='';
  if(editorType==='server'||editorType==='destination')data.port=Number(data.port);else{data.listen_port=Number(data.listen_port);data.target_port=Number(data.target_port);data.server_id=form.elements.server_id.value;}
  try{await api(`/${editorType==='server'?'servers':editorType==='destination'?'destinations':'rules'}${editorId?'/'+editorId:''}`,{method:editorId?'PUT':'POST',body:JSON.stringify(data)});$('#editor').close();toast(editorType==='rule'?'规则已保存，点击“下发”启用':editorType==='destination'?'落地已保存':'服务器已保存');await refresh();}catch(error){$('#editor-error').textContent=error.message;}finally{button.disabled=false;}
});
document.addEventListener('submit',async event=>{
  if(event.target.id!=='password-form')return;event.preventDefault();const form=event.target,data=Object.fromEntries(new FormData(form)),button=$('button',form);$('#password-error').textContent='';
  if(data.new_password!==data.confirm_password){$('#password-error').textContent='两次输入的新密码不一致';return;}
  button.disabled=true;try{await api('/password',{method:'POST',body:JSON.stringify(data)});showLogin();toast('密码已更新，请重新登录');}catch(error){$('#password-error').textContent=error.message;}finally{button.disabled=false;}
});
window.addEventListener('hashchange',navigate);
let lastAutoRefresh=0;
setInterval(()=>{
  if(!state.user||document.hidden)return;
  const pending=Object.keys(state.sending).length||state.tasks.some(task=>['pending','running'].includes(task.state));
  if(pending||Date.now()-lastAutoRefresh>=5000){lastAutoRefresh=Date.now();refresh();}
},1000);
function clock(){const time=new Date();$('#clock').textContent=time.toLocaleDateString('zh-CN',{month:'long',day:'numeric'})+' · '+time.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});}clock();setInterval(clock,30000);
(async()=>{try{const status=await api('/status');$('#setup-note').hidden=status.initialized;if(!status.initialized)return;const user=await api('/me');state.user=user.username;showConsole();navigate();await refresh();}catch{showLogin();}})();
