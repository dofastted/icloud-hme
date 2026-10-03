(function(){
  const S = window.HME;

  function params(){
    const raw = (location.hash.split('?')[1] || '');
    return new URLSearchParams(raw);
  }

  const selection = new Set();

  function pageAliases(){
    return (S.mailboxes || []).map(m => m.alias_email);
  }

  function selectionBarHtml(){
    const count = selection.size;
    const disabled = count ? '' : ' disabled';
    return '<div class="filter-row selection-bar"><label><input id="mailboxSelectAll" type="checkbox"> 全选本页</label>'
      + '<span class="muted mono" id="mailboxSelectedCount">已选 ' + count + ' 个</span>'
      + '<button class="btn btn-outline btn-sm"' + disabled + ' onclick="HME.showBatchGroupModal()">批量移动分组</button>'
      + '<button class="btn btn-outline btn-sm"' + disabled + ' onclick="HME.copySelectedMailboxes()">复制所选</button>'
      + '<button class="btn btn-danger btn-sm"' + disabled + ' onclick="HME.batchDeleteMailboxes()">批量删除</button>'
      + '<button class="btn btn-outline btn-sm"' + disabled + ' onclick="HME.clearMailboxSelection()">清空选择</button></div>';
  }

  function refreshSelectionUI(){
    const label = S.E('mailboxSelectedCount');
    if (label) label.textContent = '已选 ' + selection.size + ' 个';
    const aliases = pageAliases();
    const all = S.E('mailboxSelectAll');
    if (all) all.checked = aliases.length > 0 && aliases.every(alias => selection.has(alias));
    const bar = S.E('mailboxSelectedCount')?.parentElement;
    if (bar) bar.querySelectorAll('button').forEach(btn => { btn.disabled = selection.size === 0; });
  }

  S.toggleMailboxSelection = function(alias, checked){
    if (checked) selection.add(alias); else selection.delete(alias);
    refreshSelectionUI();
  };

  S.toggleMailboxPageSelection = function(checked){
    pageAliases().forEach(alias => { if (checked) selection.add(alias); else selection.delete(alias); });
    document.querySelectorAll('input.mailbox-select').forEach(box => { box.checked = checked; });
    refreshSelectionUI();
  };

  S.clearMailboxSelection = function(){
    selection.clear();
    document.querySelectorAll('input.mailbox-select').forEach(box => { box.checked = false; });
    refreshSelectionUI();
  };

  S.copySelectedMailboxes = function(){
    const aliases = Array.from(selection);
    if (!aliases.length) return;
    S.copyText(aliases.join('\n'), '已复制所选 ' + aliases.length + ' 个邮箱');
  };

  function mailReadError(err){
    const msg = String((err && err.message) || err || '邮件读取暂不可用');
    if (msg.includes('邮件登录' + '失败') || msg.includes('邮件认证' + '凭据')) return '邮件读取暂不可用';
    return msg;
  }

  function groupOptions(selected, firstLabel){
    const first = firstLabel == null ? '' : '<option value="">' + S.esc(firstLabel) + '</option>';
    return first + (S.groups || []).map(g => '<option value="' + S.esc(g.id) + '"' + (g.id === selected ? ' selected' : '') + '>' + S.esc(g.name || g.id) + '</option>').join('');
  }

  function groupBadge(mailbox){
    if (!mailbox.group_name) return '--';
    return '<span class="badge" style="border-color:' + S.esc(mailbox.group_color || '#ddd') + '">' + S.esc(mailbox.group_name) + '</span>';
  }
  function formatCreatedAt(value){
    if (value == null || value === '') return '--';
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) {
      const ms = n < 1e12 ? n * 1000 : n;
      const d = new Date(ms);
      if (!Number.isNaN(d.getTime())) {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        return y + '-' + m + '-' + day + ' ' + hh + ':' + mm;
      }
    }
    const parsed = Date.parse(String(value));
    if (!Number.isNaN(parsed)) return formatCreatedAt(parsed);
    return String(value);
  }
  function providerBadges(mailbox){
    const badges = [];
    if (mailbox.has_claude) badges.push('<span class="badge provider claude" title="缓存邮件命中 Claude">Claude</span>');
    if (mailbox.has_openai) badges.push('<span class="badge provider openai" title="缓存邮件命中 OpenAI">OpenAI</span>');
    return badges.length ? badges.join(' ') + ' ' : '';
  }


  function mailboxRows(items, offset){
    const base = Number(offset) || 0;
    return (items || []).map((m, i) => {
      const aliasArg = S.inlineArg(m.alias_email);
      const routeArg = S.inlineArg('#/mailbox/' + encodeURIComponent(m.alias_email));
      const groupArg = S.inlineArg(m.group_id || 'grp_default');
      const subject = String(m.latest_subject || '').trim();
      const latest = providerBadges(m) + (subject ? S.esc(subject) : '<span class="muted">空</span>');
      const shareAction = m.shared
        ? '<button class="btn btn-outline btn-sm" onclick="HME.revokeShare(' + S.inlineArg(m.shared.id) + ',' + aliasArg + ')">吊销共享</button>'
        : '<button class="btn btn-outline btn-sm" onclick="HME.createShare(' + aliasArg + ')">共享</button>';
      const checkbox = '<input class="mailbox-select" type="checkbox"' + (selection.has(m.alias_email) ? ' checked' : '') + ' onchange="HME.toggleMailboxSelection(' + aliasArg + ', this.checked)">';
      return '<tr><td class="select-cell">' + checkbox + '</td><td>' + (base + i + 1) + '</td><td><span class="cell-copy"><a class="link" onclick="HME.navigate(' + routeArg + ')">' + S.esc(m.alias_email) + '</a><button class="btn btn-outline btn-xs" title="复制邮箱地址" onclick="HME.copyText(' + aliasArg + ')">复制</button></span></td><td>' + S.esc(m.account_name || m.account_id) + '</td><td>' + groupBadge(m) + '</td><td>' + latest + '</td><td class="mono">' + S.esc(formatCreatedAt(m.created_at)) + '</td><td>' + (m.is_active ? '<span class="badge ok">ACTIVE</span>' : '<span class="badge err">OFF</span>') + '</td><td>' + (m.shared ? '<span class="badge shared">CODE ' + S.esc(m.shared.prefix) + '</span>' : '--') + '</td><td><button class="btn btn-outline btn-sm" onclick="HME.navigate(' + routeArg + ')">详情</button> <button class="btn btn-outline btn-sm" onclick="HME.showMailboxGroupModal(' + aliasArg + ',' + groupArg + ')">移动分组</button> ' + shareAction + ' <button class="btn btn-danger btn-sm" onclick="HME.deleteMailbox(' + aliasArg + ')">删除</button></td></tr>';
    }).join('');
  }

  function mailboxTable(items, offset){
    if (!items || !items.length) return S.empty('暂无邮箱 - 去仪表盘或批量创建生成', '<button class="btn btn-sm" onclick="HME.navigate(\'#/batch\')">去批量创建</button>');
    return '<div class="table-wrap"><table class="table"><thead><tr><th class="select-cell"></th><th>#</th><th>邮箱地址</th><th>所属账号</th><th>分组</th><th>最新邮件</th><th>创建时间</th><th>状态</th><th>共享</th><th>操作</th></tr></thead><tbody>' + mailboxRows(items, offset) + '</tbody></table></div>';
  }

  const PAGE_SIZES = [20, 50, 100];

  function pageSize(value){
    const size = Number(value) || 50;
    return PAGE_SIZES.includes(size) ? size : 50;
  }

  function pagerHtml(total, limit, page, pages){
    if (!total) return '';
    const sizes = PAGE_SIZES.map(size => '<option value="' + size + '"' + (size === limit ? ' selected' : '') + '>' + size + ' 条/页</option>').join('');
    const btn = (label, target, disabled) => '<button class="btn btn-outline btn-sm"' + (disabled ? ' disabled' : '') + ' onclick="HME.gotoMailboxPage(' + target + ')">' + label + '</button>';
    return '<div class="pager">' + btn('首页', 1, page <= 1) + btn('上一页', page - 1, page <= 1)
      + '<span class="muted mono">第 ' + page + ' / ' + pages + ' 页 · 共 ' + total + ' 个</span>'
      + btn('下一页', page + 1, page >= pages) + btn('末页', pages, page >= pages)
      + '<select id="mailboxPageSize">' + sizes + '</select>'
      + '<input id="mailboxPageJump" type="number" min="1" max="' + pages + '" value="' + page + '" style="width:80px">'
      + '<button class="btn btn-sm" onclick="HME.jumpMailboxPage()">跳转</button></div>';
  }

  function mailboxSource(data){
    if (!data) return '<span class="muted mono">读取本地列表...</span>';
    const source = data.source === 'local' ? '本地列表' : (data.source || '列表');
    const synced = data.index_updated_at ? ' · synced ' + data.index_updated_at : '';
    return '<span class="muted mono">' + S.esc(source + synced) + '</span>';
  }

  function probeAccountOptions(selected){
    const chosen = new Set(selected || []);
    return S.accounts.map(a => '<option value="' + S.esc(a.id) + '"' + (chosen.has(a.id) ? ' selected' : '') + '>' + S.esc(a.name || a.id) + '</option>').join('');
  }

  function probeStatusHtml(config, state){
    const parts = [];
    parts.push(state.probing ? '探测中' : (config.enabled === true ? (state.thread_alive === false ? '已启用（守护线程未运行）' : '已启用') : '未启用'));
    if (state.next_trigger) parts.push('下次 ' + new Date(state.next_trigger * 1000).toLocaleString());
    if (state.last_run) parts.push('上次 ' + state.last_run);
    if (state.last_updated) parts.push('更新 ' + state.last_updated + ' 个邮箱');
    if (state.last_duration) parts.push('耗时 ' + state.last_duration + 's');
    return '<span class="muted mono">' + S.esc(parts.join(' · ')) + '</span>';
  }

  function mailProbeHtml(data){
    const config = (data && data.config) || {};
    const state = (data && data.state) || {};
    const scope = config.account_ids || [];
    const errorHtml = state.last_error ? '<div class="filter-row"><span class="badge err">上次错误</span><span class="muted mono">' + S.esc(state.last_error) + '</span></div>' : '';
    return '<div class="panel probe-panel"><div class="panel-head"><span>自动探测邮件</span>' + probeStatusHtml(config, state) + '</div>'
      + '<div class="filter-row">'
      + '<label><input id="mailProbeEnabled" type="checkbox"' + (config.enabled === true ? ' checked' : '') + '> 启用</label>'
      + '<label>间隔 <input id="mailProbeInterval" type="number" min="1" max="1440" value="' + S.esc(config.interval_minutes || 30) + '" style="width:88px"> 分钟</label>'
      + '<label>时间窗 <input id="mailProbeStart" type="time" value="' + S.esc(config.start_time || '08:00') + '"> - <input id="mailProbeEnd" type="time" value="' + S.esc(config.end_time || '23:00') + '"></label>'
      + '<label>每箱封数 <input id="mailProbeLimit" type="number" min="1" max="10" value="' + S.esc(config.limit_per || 1) + '" style="width:72px"></label>'
      + '<label>回溯 <input id="mailProbeDays" type="number" min="1" max="30" value="' + S.esc(config.days || 30) + '" style="width:72px"> 天</label>'
      + '<label><input id="mailProbeForce" type="checkbox"' + (config.force === false ? '' : ' checked') + '> 忽略缓存</label>'
      + '<label>账号范围 <select id="mailProbeAccounts" multiple size="1" style="min-width:150px">' + probeAccountOptions(scope) + '</select></label>'
      + '<span class="muted mono">' + (scope.length ? '已选 ' + scope.length + ' 个账号' : '全部活跃账号') + '</span>'
      + '<button class="btn btn-sm" onclick="HME.saveMailProbeConfig()">保存设置</button>'
      + '<button class="btn btn-outline btn-sm" onclick="HME.runMailProbeNow()">立即探测</button>'
      + '</div>' + errorHtml + '</div>';
  }

  function filterHtml(q, account, group, status, mailKind, sort, refresh){
    const accountOptions = ['<option value="">全部账号</option>'].concat(S.accounts.map(a => '<option value="' + S.esc(a.id) + '"' + (a.id === account ? ' selected' : '') + '>' + S.esc(a.name || a.id) + '</option>')).join('');
    const groupOptionsHtml = groupOptions(group, '全部分组');
    const syncLabel = refresh ? '同步中...' : '云端同步';
    const sortValue = sort || 'created_at';
    const sortHtml = '<select id="mailboxSort"><option value="created_at"' + (sortValue === 'created_at' ? ' selected' : '') + '>时间新→旧</option><option value="created_at_asc"' + (sortValue === 'created_at_asc' ? ' selected' : '') + '>时间旧→新</option><option value="alias"' + (sortValue === 'alias' ? ' selected' : '') + '>邮箱地址</option></select>';
    const kindHtml = '<select id="mailboxKind"><option value=""' + (!mailKind ? ' selected' : '') + '>全部邮件属性</option><option value="claude"' + (mailKind === 'claude' ? ' selected' : '') + '>Claude</option><option value="openai"' + (mailKind === 'openai' ? ' selected' : '') + '>OpenAI</option><option value="empty"' + (mailKind === 'empty' ? ' selected' : '') + '>空邮箱</option></select>';
    return '<div class="filter-row"><input id="mailboxQ" style="min-width:280px" placeholder="搜索邮箱、标签、账号" value="' + S.esc(q) + '"><select id="mailboxAccount">' + accountOptions + '</select><select id="mailboxGroup">' + groupOptionsHtml + '</select><select id="mailboxStatus"><option value="">全部状态</option><option value="active"' + (status === 'active' ? ' selected' : '') + '>活跃</option><option value="inactive"' + (status === 'inactive' ? ' selected' : '') + '>停用</option></select>' + kindHtml + sortHtml + '<button class="btn btn-outline btn-sm" onclick="HME.renderMailboxes(true)">' + syncLabel + '</button><button class="btn btn-outline btn-sm" onclick="HME.copyMailboxes()">复制本页</button><button class="btn btn-outline btn-sm" onclick="HME.exportMailboxes()">CSV</button></div>';
  }

  S.renderMailboxes = async function(refresh){
    S.setTitle('邮箱列表');
    const p = params();
    const q = p.get('q') || '';
    const account = p.get('account') || '';
    const group = p.get('group_id') || p.get('group') || '';
    const status = p.get('status') || '';
    const mailKind = p.get('mail_kind') || '';
    const sort = p.get('sort') || 'created_at';
    const limit = pageSize(p.get('limit'));
    const page = Math.max(1, Number(p.get('page')) || 1);
    const offset = (page - 1) * limit;
    const current = S.mailboxes && S.mailboxes.length ? mailboxTable(S.mailboxes, offset) : S.empty('读取本地邮箱列表...');
    S.view(mailProbeHtml(null) + '<div class="panel"><div class="panel-head"><span>隐私邮箱</span><span id="mailboxMeta">' + mailboxSource(null) + '</span></div>' + filterHtml(q, account, group, status, mailKind, sort, refresh) + selectionBarHtml() + '<div id="mailboxListBody">' + current + '</div></div>');
    bindFilters();
    try {
      const query = 'q=' + encodeURIComponent(q) + '&account_id=' + encodeURIComponent(account) + '&group_id=' + encodeURIComponent(group) + '&status=' + encodeURIComponent(status) + '&mail_kind=' + encodeURIComponent(mailKind) + '&sort=' + encodeURIComponent(sort) + '&limit=' + limit + '&offset=' + offset + (refresh ? '&refresh=1' : '');
      const data = await S.api('/api/mailboxes?' + query);
      const probe = data.mail_probe || {config:{}, state:{}};
      S.mailboxes = data.mailboxes || [];
      const total = data.total || 0;
      const effectiveLimit = pageSize(data.limit || limit);
      const pages = Math.max(1, Math.ceil(total / effectiveLimit));
      if (page > pages && total) return S.gotoMailboxPage(pages);
      const shown = data.offset != null ? data.offset : offset;
      const finalHtml = mailProbeHtml(probe) + '<div class="panel"><div class="panel-head"><span>隐私邮箱</span><span id="mailboxMeta">' + mailboxSource(data) + ' · ' + total + ' total</span></div>' + filterHtml(q, account, group, status, mailKind, sort, false) + selectionBarHtml() + '<div id="mailboxListBody">' + mailboxTable(S.mailboxes, shown) + '</div>' + pagerHtml(total, effectiveLimit, page, pages) + '</div>';
      S.view(finalHtml);
      bindFilters();
    } catch (err) {
      const body = S.E('mailboxListBody');
      if (body) body.innerHTML = S.error(err, 'HME.renderMailboxes()');
      else S.view(S.error(err, 'HME.renderMailboxes()'));
    }
  };

  function pageHash(page, limit){
    const qp = new URLSearchParams(params().toString());
    if (page > 1) qp.set('page', page); else qp.delete('page');
    if (limit && limit !== 50) qp.set('limit', limit); else qp.delete('limit');
    return '#/mailboxes' + (qp.toString() ? '?' + qp.toString() : '');
  }

  S.gotoMailboxPage = function(page){
    const target = Math.max(1, Number(page) || 1);
    const limit = pageSize(params().get('limit'));
    S.navigate(pageHash(target, limit));
  };

  S.jumpMailboxPage = function(){
    S.gotoMailboxPage(S.E('mailboxPageJump')?.value);
  };

  S.setMailboxPageSize = function(value){
    S.navigate(pageHash(1, pageSize(value)));
  };

  function bindFilters(){
    const q = S.E('mailboxQ');
    const account = S.E('mailboxAccount');
    const group = S.E('mailboxGroup');
    const status = S.E('mailboxStatus');
    const mailKind = S.E('mailboxKind');
    const sort = S.E('mailboxSort');
    if (q) q.addEventListener('input', () => S.debounce('mailboxSearch', applyFilters, 300));
    if (account) account.addEventListener('change', applyFilters);
    if (group) group.addEventListener('change', applyFilters);
    if (status) status.addEventListener('change', applyFilters);
    if (mailKind) mailKind.addEventListener('change', applyFilters);
    if (sort) sort.addEventListener('change', applyFilters);
    const perPage = S.E('mailboxPageSize');
    const jump = S.E('mailboxPageJump');
    if (perPage) perPage.addEventListener('change', () => S.setMailboxPageSize(perPage.value));
    if (jump) jump.addEventListener('keydown', (event) => { if (event.key === 'Enter') S.jumpMailboxPage(); });
    const selectAll = S.E('mailboxSelectAll');
    if (selectAll) selectAll.addEventListener('change', () => S.toggleMailboxPageSelection(selectAll.checked));
    refreshSelectionUI();
  }

  function applyFilters(){
    const qp = new URLSearchParams();
    const q = S.E('mailboxQ')?.value.trim();
    const account = S.E('mailboxAccount')?.value;
    const group = S.E('mailboxGroup')?.value;
    const status = S.E('mailboxStatus')?.value;
    const mailKind = S.E('mailboxKind')?.value;
    const sort = S.E('mailboxSort')?.value;
    if (q) qp.set('q', q);
    if (account) qp.set('account', account);
    if (group) qp.set('group_id', group);
    if (status) qp.set('status', status);
    if (mailKind) qp.set('mail_kind', mailKind);
    if (sort && sort !== 'created_at') qp.set('sort', sort);
    const limit = pageSize(params().get('limit'));
    if (limit !== 50) qp.set('limit', limit);
    S.navigate('#/mailboxes' + (qp.toString() ? '?' + qp.toString() : ''));
  }
  function latestMailHtml(alias, msg, mailError){
    if (mailError) return S.empty(mailError);
    if (!msg) return S.empty('暂无邮件');
    return '<div class="message-body"><div class="label">主题</div><h3>' + S.esc(msg.subject || '(无主题)') + '</h3><p class="muted mono">From: ' + S.esc(msg.from) + '<br>To: ' + S.esc(msg.to) + '<br>Date: ' + S.esc(msg.date) + '</p><pre id="detailBody">' + S.esc(msg.body_preview || '') + '</pre><button class="btn btn-outline btn-sm" onclick="HME.loadMessageBody(' + S.inlineArg(alias) + ',' + S.inlineArg(msg.message_id) + ')">展开正文</button></div>';
  }

  function mailboxDetailHtml(alias, m, latest){
    const shared = m.shared;
    const sharePanel = shared ? '<p class="mono">兑换码前缀 ' + S.esc(shared.prefix) + '</p><p class="muted mono">created ' + S.esc(shared.created_at || '') + '<br>last ' + S.esc(shared.last_accessed_at || '') + '<br>access ' + (shared.access_count || 0) + '</p><button class="btn btn-danger btn-sm" onclick="HME.revokeShare(' + S.inlineArg(shared.id) + ',' + S.inlineArg(alias) + ')">吊销</button>' : '<p class="muted">用户通过共享主入口输入兑换码，可查看该邮箱最新一封邮件。</p><button class="btn btn-sm" onclick="HME.createShare(' + S.inlineArg(alias) + ')">生成兑换码</button>';
    return '<div class="panel"><div class="panel-body"><button class="btn btn-outline btn-sm" onclick="HME.navigate(\'#/mailboxes\')">返回列表</button><div class="detail-title"><span class="cell-copy">' + S.esc(m.alias_email) + '<button class="btn btn-outline btn-xs" title="复制邮箱地址" onclick="HME.copyText(' + S.inlineArg(m.alias_email) + ')">复制</button></span></div><p class="muted mono">' + S.esc(m.account_name || m.account_id) + ' · ' + S.esc(m.label || '') + ' · ' + (m.is_active ? 'ACTIVE' : 'OFF') + ' · ' + groupBadge(m) + '</p><button class="btn btn-outline btn-sm" onclick="HME.showMailboxGroupModal(' + S.inlineArg(alias) + ',' + S.inlineArg(m.group_id || 'grp_default') + ')">移动分组</button></div></div><div class="panel"><div class="panel-head"><span>最新邮件</span><span><button class="btn btn-outline btn-sm" onclick="HME.loadMailboxLatest(' + S.inlineArg(alias) + ',true)">刷新此邮箱邮件</button></span></div><div class="panel-body" id="mailboxLatest">' + latest + '</div></div><div class="panel"><div class="panel-head">共享访问</div><div class="panel-body">' + sharePanel + '</div></div>';
  }

  S.renderMailboxDetail = async function(alias){
    S.setTitle('单邮箱详情', '邮箱列表 / ' + alias);
    S.view(mailboxDetailHtml(alias, {alias_email: alias, account_id: '', account_name: '', label: '', is_active: true, shared: null}, S.empty('正在读取本地邮箱详情...')));
    const latestPromise = S.loadMailboxLatest(alias, false);
    try {
      const box = await S.api('/api/mailboxes/' + encodeURIComponent(alias));
      const loading = S.empty('正在读取该 HME 最新邮件...');
      S.view(mailboxDetailHtml(alias, box.mailbox, loading));
      const latest = await latestPromise;
      if (latest) S.view(mailboxDetailHtml(alias, box.mailbox, latest));
    } catch (err) {
      S.view(S.error(err, 'HME.renderMailboxDetail(\'' + S.esc(alias) + '\')'));
    }
  };

  S.loadMailboxLatest = async function(alias, force){
    const target = S.E('mailboxLatest');
    const loading = S.empty(force ? '正在刷新该 HME 邮件...' : '正在读取该 HME 最新邮件...');
    if (target) target.innerHTML = loading;
    try {
      const data = await S.api('/api/mailboxes/' + encodeURIComponent(alias) + '/messages?limit=1' + (force ? '&force=1' : ''));
      const msg = (data.messages || [])[0];
      const html = latestMailHtml(alias, msg, '');
      if (target) target.innerHTML = html;
      return html;
    } catch (err) {
      const text = mailReadError(err);
      const html = latestMailHtml(alias, null, text);
      if (target) target.innerHTML = html;
      if (force) S.toast(text, true);
      return html;
    }
  };

  function selectedAccountIds(){
    const select = S.E('mailProbeAccounts');
    if (!select) return [];
    return Array.from(select.selectedOptions || []).map(option => option.value).filter(Boolean);
  }

  S.saveMailProbeConfig = async function(){
    const payload = {
      enabled: Boolean(S.E('mailProbeEnabled')?.checked),
      force: Boolean(S.E('mailProbeForce')?.checked),
      interval_minutes: Number(S.E('mailProbeInterval')?.value || 30),
      limit_per: Number(S.E('mailProbeLimit')?.value || 1),
      days: Number(S.E('mailProbeDays')?.value || 30),
      start_time: S.E('mailProbeStart')?.value || '08:00',
      end_time: S.E('mailProbeEnd')?.value || '23:00',
      account_ids: selectedAccountIds(),
    };
    const ranges = [['interval_minutes', 1, 1440, '探测间隔'], ['limit_per', 1, 10, '每箱封数'], ['days', 1, 30, '回溯天数']];
    for (const [field, low, high, label] of ranges) {
      if (!Number.isInteger(payload[field]) || payload[field] < low || payload[field] > high) {
        S.toast(label + '必须是 ' + low + '-' + high + ' 的整数', true);
        return;
      }
    }
    try {
      const saved = await S.api('/api/mail-probe/config', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      const alive = saved.state && saved.state.thread_alive;
      if (payload.enabled && alive === false) S.toast('配置已保存，但探测守护线程未运行，请重启服务', true);
      else S.toast(payload.enabled ? '自动探测已启用' : '自动探测已停用');
      await S.renderMailboxes(false);
    } catch (err) { S.toast(err.message, true); }
  };

  S.runMailProbeNow = async function(){
    try {
      await S.api('/api/mailboxes/probe', {method:'POST'});
      S.toast('邮件探测已加入后台队列');
    } catch (err) { S.toast(err.message, true); }
  };

  S.deleteMailbox = async function(alias){
    if (!confirm('确认删除邮箱 ' + alias + '？此操作会删除 Apple 别名及本地记录。')) return;
    try {
      const data = await S.api('/api/mailboxes/' + encodeURIComponent(alias), {method:'DELETE'});
      const warning = data.mailbox && data.mailbox.warning;
      selection.delete(alias);
      S.toast(warning || '邮箱已删除', Boolean(warning));
      await S.refreshAll();
      return;
    } catch (err) {
      if (err.code !== 'alias_id_unresolved') { S.toast(err.message, true); return; }
      if (!confirm(err.message + '\n\n点击确定仅清理本地记录（Apple 别名保留）。')) return;
    }
    try {
      const data = await S.api('/api/mailboxes/' + encodeURIComponent(alias) + '?local_only=1', {method:'DELETE'});
      selection.delete(alias);
      S.toast((data.mailbox && data.mailbox.warning) || '已清理本地记录', true);
      await S.refreshAll();
    } catch (err) { S.toast(err.message, true); }
  };

  S.showMailboxGroupModal = function(alias, selectedGroup){
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> 移动邮箱分组</h3><p class="muted mono">' + S.esc(alias) + '</p><label class="label">目标分组</label><select id="mailboxGroupTarget">' + groupOptions(selectedGroup || 'grp_default') + '</select><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button class="btn" onclick="HME.moveMailboxToGroup(' + S.inlineArg(alias) + ')">保存</button></div><div id="modalMsg" class="warning"></div></div></div>';
  };

  S.moveMailboxToGroup = async function(alias){
    try {
      const group_id = S.E('mailboxGroupTarget').value;
      await S.api('/api/mailboxes/batch-update-group', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({alias_emails:[alias], group_id})});
      S.closeModal();
      S.toast('邮箱分组已更新');
      await S.refreshAll();
    } catch (err) {
      S.E('modalMsg').textContent = err.message;
    }
  };

  S.showBatchGroupModal = function(){
    const aliases = Array.from(selection);
    if (!aliases.length) { S.toast('请先选择邮箱', true); return; }
    const preview = aliases.slice(0, 5).join('、') + (aliases.length > 5 ? ' 等 ' + aliases.length + ' 个' : '');
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> 批量移动分组</h3><p class="muted mono">已选 ' + aliases.length + ' 个：' + S.esc(preview) + '</p><label class="label">目标分组</label><select id="batchGroupTarget">' + groupOptions('grp_default') + '</select><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button class="btn" onclick="HME.batchMoveGroup()">移动</button></div><div id="modalMsg" class="warning"></div></div></div>';
  };

  S.batchMoveGroup = async function(){
    const aliases = Array.from(selection);
    if (!aliases.length) { S.closeModal(); return; }
    try {
      const group_id = S.E('batchGroupTarget').value;
      const data = await S.api('/api/mailboxes/batch-update-group', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({alias_emails:aliases, group_id})});
      S.closeModal();
      selection.clear();
      S.toast('已移动 ' + (data.moved || 0) + '/' + aliases.length + ' 个邮箱');
      await S.refreshAll();
    } catch (err) {
      S.E('modalMsg').textContent = err.message;
    }
  };

  async function postBatchDelete(aliases, localOnly){
    return S.api('/api/mailboxes/batch-delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(localOnly ? {alias_emails: aliases, local_only: true} : {alias_emails: aliases}),
    });
  }

  function unresolvedAliases(summary){
    return (summary.results || []).filter(item => item.code === 'alias_id_unresolved').map(item => item.alias_email);
  }

  S.batchDeleteMailboxes = async function(){
    const aliases = Array.from(selection);
    if (!aliases.length) { S.toast('请先选择邮箱', true); return; }
    if (!confirm('确认删除所选 ' + aliases.length + ' 个邮箱？此操作会删除 Apple 别名及本地记录。')) return;
    let summary;
    try {
      summary = await postBatchDelete(aliases, false);
    } catch (err) { S.toast(err.message, true); return; }

    const pending = unresolvedAliases(summary);
    let localSummary = null;
    if (pending.length && confirm('有 ' + pending.length + ' 个邮箱无法从 Apple 获取别名标识。\n\n点击确定仅清理它们的本地记录（Apple 别名保留）。')) {
      try {
        localSummary = await postBatchDelete(pending, true);
      } catch (err) { S.toast(err.message, true); }
    }

    const deleted = summary.deleted + (localSummary ? localSummary.deleted : 0);
    const failed = summary.failed - (localSummary ? localSummary.deleted : 0);
    (summary.results || []).forEach(item => { if (item.ok) selection.delete(item.alias_email); });
    (localSummary && localSummary.results || []).forEach(item => { if (item.ok) selection.delete(item.alias_email); });
    S.toast('已删除 ' + deleted + '/' + aliases.length + ' 个邮箱' + (failed > 0 ? '，失败 ' + failed + ' 个' : ''), failed > 0);
    await S.refreshAll();
  };

  S.forceMailbox = async function(alias){
    return S.loadMailboxLatest(alias, true);
  };

  S.loadMessageBody = async function(alias, id){
    try {
      const data = await S.api('/api/mailboxes/' + encodeURIComponent(alias) + '/messages/' + encodeURIComponent(id));
      S.E('detailBody').textContent = data.message.body || '(无正文)';
    } catch (err) { S.toast(mailReadError(err), true); }
  };

  S.copyMailboxes = function(){
    navigator.clipboard.writeText(S.mailboxes.map(m => m.alias_email).join('\n'));
    S.toast('已复制 ' + S.mailboxes.length + ' 个邮箱');
  };

  S.exportMailboxes = function(){
    const csv = 'alias_email,account,group,label,created_at,active,shared\n' + S.mailboxes.map(m => [m.alias_email, m.account_name || m.account_id, m.group_name || '', m.label || '', formatCreatedAt(m.created_at), m.is_active ? 'yes' : 'no', m.shared ? m.shared.prefix : ''].map(v => '"' + String(v).replace(/"/g,'""') + '"').join(',')).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['\uFEFF' + csv], {type:'text/csv'}));
    a.download = 'icloud_mailboxes.csv';
    a.click();
  };
})();
