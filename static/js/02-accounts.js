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
      const mailText = a.has_mail_config ? ('邮件登录 ' + (a.mail_host || '已配置')) : '邮件登录未配置';
      return '<div class="card"><div class="label">' + S.esc(a.status || '') + '</div><h3 class="mono">' + S.esc(a.name || a.id) + '</h3><p class="muted mono">' + S.esc(a.real_email || '') + '</p><p class="muted mono">' + S.esc(mailText) + '</p><p class="muted mono">aliases ' + (a.alias_total || 0) + ' / active ' + (a.alias_active || 0) + '</p><div class="btn-row"><button class="btn btn-outline btn-sm" onclick="HME.navigate(\'#/mailboxes?account=' + encodeURIComponent(a.id) + '\')">查看邮箱</button><button class="btn btn-outline btn-sm" onclick="HME.showMailSettingsModal(' + S.inlineArg(a.id) + ')">邮件登录</button><button class="btn btn-outline btn-sm" onclick="HME.validateAccount(' + S.inlineArg(a.id) + ')">校验</button></div></div>';
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
  S.showMailSettingsModal = function(id){
    const account = S.accounts.find(a => a.id === id) || {};
    const email = account.mail_email || account.real_email || '';
    const host = account.mail_host || '';
    const port = account.mail_port || 993;
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> 邮件登录</h3><p class="muted">HME 邮件会转发到接收邮箱。这里配置接收邮箱的 IMAP 登录，用于读取邮件内容。</p><label class="label">接收邮箱</label><input id="mailEmailInput" placeholder="name@example.com" value="' + S.esc(email) + '"><label class="label">IMAP 服务器</label><input id="mailHostInput" placeholder="imap.example.com" value="' + S.esc(host) + '"><label class="label">端口</label><input id="mailPortInput" placeholder="993" value="' + S.esc(port) + '"><label class="label">邮箱授权码或密码</label><input id="mailPasswordInput" type="password" autocomplete="new-password" placeholder="只保存到本机 accounts.json"><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button class="btn" onclick="HME.saveMailSettings(' + S.inlineArg(id) + ')">保存并测试</button></div><div id="modalMsg" class="warning"></div></div></div>';
  };
  S.saveMailSettings = async function(id){
    try {
      const email = S.E('mailEmailInput').value.trim();
      const host = S.E('mailHostInput').value.trim();
      const port = S.E('mailPortInput').value.trim() || '993';
      const password = S.E('mailPasswordInput').value.trim();
      const res = await S.api('/api/accounts/' + encodeURIComponent(id) + '/mail-settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email, host, port, password})});
      if (!res.ok) throw new Error((res.mail && res.mail.error) || res.error || '邮件登录未通过');
      S.closeModal();
      S.toast('邮件登录已通过');
      S.refreshAll();
    } catch (err) { S.E('modalMsg').textContent = err.message; }
  };
})();
