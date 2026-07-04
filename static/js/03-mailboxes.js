(function(){
  const S = window.HME;

  function params(){
    const raw = (location.hash.split('?')[1] || '');
    return new URLSearchParams(raw);
  }

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

  S.renderMailboxes = async function(refresh){
    S.setTitle('邮箱列表');
    S.view(S.loading(6));
    const p = params();
    const q = p.get('q') || '';
    const account = p.get('account') || '';
    const group = p.get('group_id') || p.get('group') || '';
    const status = p.get('status') || '';
    try {
      const query = 'q=' + encodeURIComponent(q) + '&account_id=' + encodeURIComponent(account) + '&group_id=' + encodeURIComponent(group) + '&status=' + encodeURIComponent(status) + '&limit=200' + (refresh ? '&refresh=1' : '');
      const data = await S.api('/api/mailboxes?' + query);
      S.mailboxes = data.mailboxes || [];
      const accountOptions = ['<option value="">全部账号</option>'].concat(S.accounts.map(a => '<option value="' + S.esc(a.id) + '"' + (a.id === account ? ' selected' : '') + '>' + S.esc(a.name || a.id) + '</option>')).join('');
      const groupOptionsHtml = groupOptions(group, '全部分组');
      const syncLabel = refresh ? '同步完成' : '云端同步';
      const filter = '<div class="filter-row"><input id="mailboxQ" style="min-width:280px" placeholder="搜索邮箱、标签、账号" value="' + S.esc(q) + '"><select id="mailboxAccount">' + accountOptions + '</select><select id="mailboxGroup">' + groupOptionsHtml + '</select><select id="mailboxStatus"><option value="">全部状态</option><option value="active"' + (status === 'active' ? ' selected' : '') + '>活跃</option><option value="inactive"' + (status === 'inactive' ? ' selected' : '') + '>停用</option></select><button class="btn btn-outline btn-sm" onclick="HME.renderMailboxes(true)">' + syncLabel + '</button><button class="btn btn-outline btn-sm" onclick="HME.copyMailboxes()">复制全部</button><button class="btn btn-outline btn-sm" onclick="HME.exportMailboxes()">CSV</button></div>';
      if (!S.mailboxes.length) {
        S.view('<div class="panel"><div class="panel-head">隐私邮箱</div>' + filter + S.empty('暂无邮箱 - 去仪表盘或批量创建生成', '<button class="btn btn-sm" onclick="HME.navigate(\'#/batch\')">去批量创建</button>') + '</div>');
        bindFilters();
        return;
      }
      const rows = S.mailboxes.map((m, i) => {
        const aliasArg = S.inlineArg(m.alias_email);
        const routeArg = S.inlineArg('#/mailbox/' + encodeURIComponent(m.alias_email));
        const groupArg = S.inlineArg(m.group_id || 'grp_default');
        return '<tr><td>' + (i + 1) + '</td><td><a class="link" onclick="HME.navigate(' + routeArg + ')">' + S.esc(m.alias_email) + '</a></td><td>' + S.esc(m.account_name || m.account_id) + '</td><td>' + groupBadge(m) + '</td><td>' + S.esc(m.label || '') + '</td><td>' + (m.is_active ? '<span class="badge ok">ACTIVE</span>' : '<span class="badge err">OFF</span>') + '</td><td>' + (m.shared ? '<span class="badge shared">CODE ' + S.esc(m.shared.prefix) + '</span>' : '--') + '</td><td><button class="btn btn-outline btn-sm" onclick="HME.navigate(' + routeArg + ')">详情</button> <button class="btn btn-outline btn-sm" onclick="HME.showMailboxGroupModal(' + aliasArg + ',' + groupArg + ')">移动分组</button> <button class="btn btn-outline btn-sm" onclick="navigator.clipboard.writeText(' + aliasArg + ')">复制</button> <button class="btn btn-outline btn-sm" onclick="HME.createShare(' + aliasArg + ')">兑换码</button></td></tr>';
      }).join('');
      S.view('<div class="panel"><div class="panel-head"><span>隐私邮箱</span><span>' + data.total + ' total</span></div>' + filter + '<div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>邮箱地址</th><th>所属账号</th><th>分组</th><th>标签</th><th>状态</th><th>共享</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table></div></div>');
      bindFilters();
    } catch (err) {
      S.view(S.error(err, 'HME.renderMailboxes()'));
    }
  };

  function bindFilters(){
    const q = S.E('mailboxQ');
    const account = S.E('mailboxAccount');
    const group = S.E('mailboxGroup');
    const status = S.E('mailboxStatus');
    if (q) q.addEventListener('input', () => S.debounce('mailboxSearch', applyFilters, 300));
    if (account) account.addEventListener('change', applyFilters);
    if (group) group.addEventListener('change', applyFilters);
    if (status) status.addEventListener('change', applyFilters);
  }
  function applyFilters(){
    const qp = new URLSearchParams();
    const q = S.E('mailboxQ')?.value.trim();
    const account = S.E('mailboxAccount')?.value;
    const group = S.E('mailboxGroup')?.value;
    const status = S.E('mailboxStatus')?.value;
    if (q) qp.set('q', q);
    if (account) qp.set('account', account);
    if (group) qp.set('group_id', group);
    if (status) qp.set('status', status);
    S.navigate('#/mailboxes' + (qp.toString() ? '?' + qp.toString() : ''));
  }

  S.renderMailboxDetail = async function(alias){
    S.setTitle('单邮箱详情', '邮箱列表 / ' + alias);
    S.view(S.loading(4));
    try {
      const box = await S.api('/api/mailboxes/' + encodeURIComponent(alias));
      let msgs = {messages: []};
      let mailError = '';
      try {
        msgs = await S.api('/api/mailboxes/' + encodeURIComponent(alias) + '/messages?limit=1');
      } catch (err) {
        mailError = mailReadError(err);
      }
      const m = box.mailbox;
      const msg = (msgs.messages || [])[0];
      const shared = m.shared;
      let latest = '';
      if (mailError) latest = S.empty(mailError);
      else if (!msg) latest = S.empty('暂无邮件');
      else latest = '<div class="message-body"><div class="label">主题</div><h3>' + S.esc(msg.subject || '(无主题)') + '</h3><p class="muted mono">From: ' + S.esc(msg.from) + '<br>To: ' + S.esc(msg.to) + '<br>Date: ' + S.esc(msg.date) + '</p><pre id="detailBody">' + S.esc(msg.body_preview || '') + '</pre><button class="btn btn-outline btn-sm" onclick="HME.loadMessageBody(' + S.inlineArg(alias) + ',' + S.inlineArg(msg.message_id) + ')">展开正文</button></div>';
      const sharePanel = shared ? '<p class="mono">兑换码前缀 ' + S.esc(shared.prefix) + '</p><p class="muted mono">created ' + S.esc(shared.created_at || '') + '<br>last ' + S.esc(shared.last_accessed_at || '') + '<br>access ' + (shared.access_count || 0) + '</p><button class="btn btn-danger btn-sm" onclick="HME.revokeShare(' + S.inlineArg(shared.id) + ',' + S.inlineArg(alias) + ')">吊销</button>' : '<p class="muted">用户通过共享主入口输入兑换码，可查看该邮箱最新一封邮件。</p><button class="btn btn-sm" onclick="HME.createShare(' + S.inlineArg(alias) + ')">生成兑换码</button>';
      S.view('<div class="panel"><div class="panel-body"><button class="btn btn-outline btn-sm" onclick="HME.navigate(\'#/mailboxes\')">返回列表</button><div class="detail-title">' + S.esc(m.alias_email) + '</div><p class="muted mono">' + S.esc(m.account_name || m.account_id) + ' · ' + S.esc(m.label || '') + ' · ' + (m.is_active ? 'ACTIVE' : 'OFF') + ' · ' + groupBadge(m) + '</p><button class="btn btn-outline btn-sm" onclick="HME.showMailboxGroupModal(' + S.inlineArg(alias) + ',' + S.inlineArg(m.group_id || 'grp_default') + ')">移动分组</button></div></div><div class="panel"><div class="panel-head"><span>最新邮件</span><span><button class="btn btn-outline btn-sm" onclick="HME.renderMailboxDetail(' + S.inlineArg(alias) + ')">刷新</button> <button class="btn btn-outline btn-sm" onclick="HME.forceMailbox(' + S.inlineArg(alias) + ')">强制刷新</button></span></div><div class="panel-body">' + latest + '</div></div><div class="panel"><div class="panel-head">共享访问</div><div class="panel-body">' + sharePanel + '</div></div>');
    } catch (err) {
      S.view(S.error(err, 'HME.renderMailboxDetail(\'' + S.esc(alias) + '\')'));
    }
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

  S.forceMailbox = async function(alias){
    try {
      await S.api('/api/mailboxes/' + encodeURIComponent(alias) + '/messages?limit=1&force=1');
      S.renderMailboxDetail(alias);
    } catch (err) { S.toast(mailReadError(err), true); S.renderMailboxDetail(alias); }
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
    const csv = 'alias_email,account,group,label,active,shared\n' + S.mailboxes.map(m => [m.alias_email, m.account_name || m.account_id, m.group_name || '', m.label || '', m.is_active ? 'yes' : 'no', m.shared ? m.shared.prefix : ''].map(v => '"' + String(v).replace(/"/g,'""') + '"').join(',')).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['\uFEFF' + csv], {type:'text/csv'}));
    a.download = 'icloud_mailboxes.csv';
    a.click();
  };
})();
