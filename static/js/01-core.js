(function(){
  const S = window.HME = {
    accounts: [],
    groups: [],
    imapConfigs: [],
    state: {},
    mailboxes: [],
    logs: [],
    route: '#/dashboard',
    timers: {},
    E(id){ return document.getElementById(id); },
    esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
    inlineArg(value){ return S.esc(JSON.stringify(value)); },
    async api(path, opts){
      const res = await fetch(path, opts || {});
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + res.status));
      return data;
    },
    toast(msg, err){
      const t = S.E('toast');
      t.textContent = msg;
      t.style.background = err ? 'var(--red)' : 'var(--ink)';
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2200);
    },
    setTitle(title, crumb){
      S.E('pageTitle').textContent = title;
      S.E('breadcrumb').textContent = crumb || title;
    },
    loading(rows){
      const count = rows || 5;
      return '<div class="panel"><div class="panel-body">' + Array.from({length:count}).map(() => '<div class="skeleton-row"></div>').join('') + '</div></div>';
    },
    empty(text, cta){
      return '<div class="empty">' + S.esc(text) + (cta ? '<div style="margin-top:14px">' + cta + '</div>' : '') + '</div>';
    },
    error(err, retry){
      return '<div class="error-box">' + S.esc(err.message || err || '加载失败') + (retry ? '<div style="margin-top:14px"><button class="btn btn-outline btn-sm" onclick="' + retry + '">重试</button></div>' : '') + '</div>';
    },
    view(html){ S.E('appView').innerHTML = html; },
    navigate(hash){ location.hash = hash; },
    debounce(key, fn, ms){
      clearTimeout(S.timers[key]);
      S.timers[key] = setTimeout(fn, ms || 250);
    }
  };

  async function refreshBase(){
    const [state, accounts] = await Promise.all([S.api('/api/state'), S.api('/api/accounts')]);
    S.state = state;
    S.accounts = accounts.accounts || [];
    S.groups = accounts.groups || [];
    S.imapConfigs = accounts.imap_configs || [];
    renderSidebar();
  }

  function renderSidebar(){
    S.E('schedDot').className = 'status-dot ' + (S.state.running ? 'online' : '');
    S.E('schedLabel').textContent = '调度器: ' + (S.state.running ? '运行中' : '就绪');
    S.E('btnSched').textContent = S.state.running ? '停止调度器' : '启动调度器';
    S.E('sidebarAccounts').innerHTML = S.accounts.map(a => '<div class="account-row ' + (a.status === 'active' ? 'active' : '') + '"><span class="status-dot ' + (a.status === 'active' ? 'online' : '') + '"></span><span title="' + S.esc(a.real_email || '') + '">' + S.esc(a.name || a.id) + '</span></div>').join('') || '<div class="muted mono" style="font-size:12px;padding:8px 0">暂无账号</div>';
  }

  async function refreshAll(){
    try {
      await refreshBase();
      await route();
    } catch (err) {
      S.view(S.error(err, 'HME.refreshAll()'));
    }
  }
  S.refreshAll = refreshAll;

  async function route(){
    S.route = location.hash || '#/dashboard';
    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.toggle('active', S.route.startsWith(btn.dataset.route)));
    if (S.route.startsWith('#/mailbox/')) return HME.renderMailboxDetail(decodeURIComponent(S.route.slice('#/mailbox/'.length)));
    if (S.route.startsWith('#/mailboxes')) return HME.renderMailboxes();
    if (S.route.startsWith('#/shared')) return HME.renderShared();
    if (S.route.startsWith('#/groups')) return HME.renderGroups();
    if (S.route.startsWith('#/imap-configs')) return HME.renderImapConfigs();
    if (S.route.startsWith('#/batch')) return HME.renderBatch();
    if (S.route.startsWith('#/inbox')) return HME.renderInbox();
    if (S.route.startsWith('#/docs')) return HME.renderDocs();
    if (S.route.startsWith('#/api-keys')) return HME.renderApiKeys();
    if (S.route.startsWith('#/logs')) return HME.renderLogs();
    return HME.renderDashboard();
  }
  S.routeView = route;

  document.addEventListener('click', async (event) => {
    const nav = event.target.closest('[data-route]');
    if (nav) { S.navigate(nav.dataset.route); return; }
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (!action) return;
    if (action === 'refresh') refreshAll();
    if (action === 'add-account') HME.showAddAccountModal();
    if (action === 'toggle-scheduler') {
      try {
        await S.api('/api/scheduler/' + (S.state.running ? 'stop' : 'start'), {method:'POST'});
        await refreshAll();
      } catch (err) { S.toast(err.message, true); }
    }
  });
  window.addEventListener('hashchange', route);
  refreshAll();
  setInterval(refreshBase, 30000);
})();
