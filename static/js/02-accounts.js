(function(){
  const S = window.HME;

  S.renderDashboard = async function(){
    S.setTitle('仪表盘');
    const st = S.state;
    const cards = [
      ['ACCOUNTS', st.account_count || 0, ''],
      ['ACTIVE', st.active_accounts || 0, 'ok'],
      ['ALIASES', st.total_aliases || st.alias_count || 0, ''],
      ['TODAY', st.today_created || 0, '']
    ].map(c => '<div class="card"><div class="label">' + c[0] + '</div><div class="value ' + c[2] + '">' + c[1] + '</div></div>').join('');
    const accountCards = S.accounts.map(a => {
      const imap = a.has_app_password ? '<span class="badge ok">IMAP 已配置</span>' : '<span class="badge err">IMAP 未配置</span>';
      return '<div class="card"><div class="label">' + S.esc(a.status || '') + '</div><h3 class="mono">' + S.esc(a.name || a.id) + '</h3><p class="muted mono">' + S.esc(a.real_email || '') + '</p><p>' + imap + '</p><p class="muted mono">aliases ' + (a.alias_total || 0) + ' / active ' + (a.alias_active || 0) + '</p><div class="btn-row"><button class="btn btn-outline btn-sm" onclick="HME.navigate(\'#/mailboxes?account=' + encodeURIComponent(a.id) + '\')">查看邮箱</button><button class="btn btn-outline btn-sm" onclick="HME.showAppPasswordModal(\'' + S.esc(a.id) + '\')">IMAP</button><button class="btn btn-outline btn-sm" onclick="HME.validateAccount(\'' + S.esc(a.id) + '\')">校验</button></div></div>';
    }).join('');
    S.view('<div class="grid cards">' + cards + '</div><div style="height:18px"></div><div class="grid cards">' + (accountCards || '<div class="card muted">暂无账号</div>') + '</div>');
  };

  S.showAddAccountModal = function(){
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> 导入 iCloud Cookie</h3><p class="muted">支持 Cookie Editor 的 Header String 或 JSON。</p><input id="accNameInput" placeholder="账号名称"><textarea id="cookieInput" placeholder="name=value; name2=value2"></textarea><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button class="btn" onclick="HME.addAccount()">添加并校验</button></div><div id="modalMsg" class="warning"></div></div></div>';
  };
  S.closeModal = function(){ S.E('modalRoot').innerHTML = ''; };
  S.addAccount = async function(){
    try {
      const name = S.E('accNameInput').value.trim() || '未命名账号';
      const cookie_input = S.E('cookieInput').value.trim();
      if (!cookie_input) throw new Error('请粘贴 Cookie');
      await S.api('/api/accounts/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, cookie_input})});
      S.closeModal();
      S.toast('账号已添加');
      S.refreshAll();
    } catch (err) { S.E('modalMsg').textContent = err.message; }
  };
  S.validateAccount = async function(id){
    try {
      await S.api('/api/accounts/' + encodeURIComponent(id) + '/validate', {method:'POST'});
      S.toast('校验完成');
      S.refreshAll();
    } catch (err) { S.toast(err.message, true); }
  };
  S.showAppPasswordModal = function(id){
    const acc = S.accounts.find(a => a.id === id) || {};
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> 设置 IMAP</h3><p class="muted mono">' + S.esc(acc.name || id) + '</p><input id="icloudEmailInput" placeholder="xxx@icloud.com" value="' + S.esc(acc.icloud_email || '') + '"><input id="appPwdInput" type="password" placeholder="App 专用密码"><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button class="btn" onclick="HME.saveAppPassword(\'' + S.esc(id) + '\')">保存并测试</button></div><div id="modalMsg" class="warning"></div></div></div>';
  };
  S.saveAppPassword = async function(id){
    try {
      await S.api('/api/accounts/' + encodeURIComponent(id) + '/app-password', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({icloud_email:S.E('icloudEmailInput').value.trim(), app_password:S.E('appPwdInput').value.trim()})});
      S.closeModal();
      S.toast('IMAP 已保存');
      S.refreshAll();
    } catch (err) { S.E('modalMsg').textContent = err.message; }
  };
})();
