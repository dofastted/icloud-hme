(function(){
  const S = window.HME;

  function params(){
    const raw = (location.hash.split('?')[1] || '');
    return new URLSearchParams(raw);
  }

  S.renderMailboxes = async function(){
    S.setTitle('邮箱列表');
    S.view(S.loading(6));
    const p = params();
    const q = p.get('q') || '';
    const account = p.get('account') || '';
    const status = p.get('status') || '';
    try {
      const data = await S.api('/api/mailboxes?q=' + encodeURIComponent(q) + '&account_id=' + encodeURIComponent(account) + '&status=' + encodeURIComponent(status) + '&limit=200');
      S.mailboxes = data.mailboxes || [];
      const accountOptions = ['<option value="">全部账号</option>'].concat(S.accounts.map(a => '<option value="' + S.esc(a.id) + '"' + (a.id === account ? ' selected' : '') + '>' + S.esc(a.name || a.id) + '</option>')).join('');
      const filter = '<div class="filter-row"><input id="mailboxQ" style="min-width:280px" placeholder="搜索邮箱、标签、账号" value="' + S.esc(q) + '"><select id="mailboxAccount">' + accountOptions + '</select><select id="mailboxStatus"><option value="">全部状态</option><option value="active"' + (status === 'active' ? ' selected' : '') + '>活跃</option><option value="inactive"' + (status === 'inactive' ? ' selected' : '') + '>停用</option></select><button class="btn btn-outline btn-sm" onclick="HME.renderMailboxes()">云端同步</button><button class="btn btn-outline btn-sm" onclick="HME.copyMailboxes()">复制全部</button><button class="btn btn-outline btn-sm" onclick="HME.exportMailboxes()">CSV</button></div>';
      if (!S.mailboxes.length) {
        S.view('<div class="panel"><div class="panel-head">隐私邮箱</div>' + filter + S.empty('暂无邮箱 - 去仪表盘或批量创建生成', '<button class="btn btn-sm" onclick="HME.navigate(\'#/batch\')">去批量创建</button>') + '</div>');
        bindFilters();
        return;
      }
      const rows = S.mailboxes.map((m, i) => {
        const aliasArg = JSON.stringify(m.alias_email);
        const routeArg = JSON.stringify('#/mailbox/' + encodeURIComponent(m.alias_email));
        return '<tr><td>' + (i + 1) + '</td><td><a class="link" onclick="HME.navigate(' + routeArg + ')">' + S.esc(m.alias_email) + '</a></td><td>' + S.esc(m.account_name || m.account_id) + '</td><td>' + S.esc(m.label || '') + '</td><td>' + (m.is_active ? '<span class="badge ok">ACTIVE</span>' : '<span class="badge err">OFF</span>') + '</td><td>' + (m.shared ? '<span class="badge shared">SHARED ' + S.esc(m.shared.prefix) + '</span>' : '--') + '</td><td><button class="btn btn-outline btn-sm" onclick="HME.navigate(' + routeArg + ')">详情</button> <button class="btn btn-outline btn-sm" onclick="navigator.clipboard.writeText(' + aliasArg + ')">复制</button> <button class="btn btn-outline btn-sm" onclick="HME.createShare(' + aliasArg + ')">共享</button></td></tr>';
      }).join('');
      S.view('<div class="panel"><div class="panel-head"><span>隐私邮箱</span><span>' + data.total + ' total</span></div>' + filter + '<div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>邮箱地址</th><th>所属账号</th><th>标签</th><th>状态</th><th>共享</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table></div></div>');
      bindFilters();
    } catch (err) {
      S.view(S.error(err, 'HME.renderMailboxes()'));
    }
  };

  function bindFilters(){
    const q = S.E('mailboxQ');
    const account = S.E('mailboxAccount');
    const status = S.E('mailboxStatus');
    if (q) q.addEventListener('input', () => S.debounce('mailboxSearch', applyFilters, 300));
    if (account) account.addEventListener('change', applyFilters);
    if (status) status.addEventListener('change', applyFilters);
  }
  function applyFilters(){
    const qp = new URLSearchParams();
    const q = S.E('mailboxQ')?.value.trim();
    const account = S.E('mailboxAccount')?.value;
    const status = S.E('mailboxStatus')?.value;
    if (q) qp.set('q', q);
    if (account) qp.set('account', account);
    if (status) qp.set('status', status);
    S.navigate('#/mailboxes' + (qp.toString() ? '?' + qp.toString() : ''));
  }

  S.renderMailboxDetail = async function(alias){
    S.setTitle('单邮箱详情', '邮箱列表 / ' + alias);
    S.view(S.loading(4));
    try {
      const [box, msgs] = await Promise.all([
        S.api('/api/mailboxes/' + encodeURIComponent(alias)),
        S.api('/api/mailboxes/' + encodeURIComponent(alias) + '/messages?limit=1')
      ]);
      const m = box.mailbox;
      const msg = (msgs.messages || [])[0];
      const shared = m.shared;
      let latest = '';
      if (!msg) latest = S.empty('暂无邮件');
      else latest = '<div class="message-body"><div class="label">主题</div><h3>' + S.esc(msg.subject || '(无主题)') + '</h3><p class="muted mono">From: ' + S.esc(msg.from) + '<br>To: ' + S.esc(msg.to) + '<br>Date: ' + S.esc(msg.date) + '</p><pre id="detailBody">' + S.esc(msg.body_preview || '') + '</pre><button class="btn btn-outline btn-sm" onclick="HME.loadMessageBody(' + JSON.stringify(alias) + ',' + JSON.stringify(msg.message_id) + ')">展开正文</button></div>';
      const sharePanel = shared ? '<p class="mono">prefix ' + S.esc(shared.prefix) + '</p><p class="muted mono">created ' + S.esc(shared.created_at || '') + '<br>last ' + S.esc(shared.last_accessed_at || '') + '<br>access ' + (shared.access_count || 0) + '</p><button class="btn btn-danger btn-sm" onclick="HME.revokeShare(' + JSON.stringify(shared.id) + ',' + JSON.stringify(alias) + ')">吊销</button>' : '<p class="muted">持有链接者可查看该邮箱最新一封邮件。</p><button class="btn btn-sm" onclick="HME.createShare(' + JSON.stringify(alias) + ')">生成共享链接</button>';
      S.view('<div class="panel"><div class="panel-body"><button class="btn btn-outline btn-sm" onclick="HME.navigate(\'#/mailboxes\')">返回列表</button><div class="detail-title">' + S.esc(m.alias_email) + '</div><p class="muted mono">' + S.esc(m.account_name || m.account_id) + ' · ' + S.esc(m.label || '') + ' · ' + (m.is_active ? 'ACTIVE' : 'OFF') + '</p></div></div><div class="panel"><div class="panel-head"><span>最新邮件</span><span><button class="btn btn-outline btn-sm" onclick="HME.renderMailboxDetail(' + JSON.stringify(alias) + ')">刷新</button> <button class="btn btn-outline btn-sm" onclick="HME.forceMailbox(' + JSON.stringify(alias) + ')">强制刷新</button></span></div><div class="panel-body">' + latest + '</div></div><div class="panel"><div class="panel-head">共享访问</div><div class="panel-body">' + sharePanel + '</div></div>');
    } catch (err) {
      S.view(S.error(err, 'HME.renderMailboxDetail(\'' + S.esc(alias) + '\')'));
    }
  };

  S.forceMailbox = async function(alias){
    try {
      await S.api('/api/mailboxes/' + encodeURIComponent(alias) + '/messages?limit=1&force=1');
      S.renderMailboxDetail(alias);
    } catch (err) { S.toast(err.message, true); }
  };
  S.loadMessageBody = async function(alias, id){
    try {
      const data = await S.api('/api/mailboxes/' + encodeURIComponent(alias) + '/messages/' + encodeURIComponent(id));
      S.E('detailBody').textContent = data.message.body || '(无正文)';
    } catch (err) { S.toast(err.message, true); }
  };
  S.copyMailboxes = function(){
    navigator.clipboard.writeText(S.mailboxes.map(m => m.alias_email).join('\n'));
    S.toast('已复制 ' + S.mailboxes.length + ' 个邮箱');
  };
  S.exportMailboxes = function(){
    const csv = 'alias_email,account,label,active,shared\n' + S.mailboxes.map(m => [m.alias_email, m.account_name || m.account_id, m.label || '', m.is_active ? 'yes' : 'no', m.shared ? m.shared.prefix : ''].map(v => '"' + String(v).replace(/"/g,'""') + '"').join(',')).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['\uFEFF' + csv], {type:'text/csv'}));
    a.download = 'icloud_mailboxes.csv';
    a.click();
  };
})();
