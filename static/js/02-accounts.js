(function(){
  const S = window.HME;
  let addAccountPending = false;

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
      const mailLabel = a.imap_config_name || a.mail_host || '已配置';
      const mailText = a.has_mail_config ? ('邮件登录 ' + mailLabel) : '邮件登录未配置';
      const label = a.name || a.real_email || a.id;
      const mailboxRoute = '#/mailboxes?account=' + encodeURIComponent(a.id);
      return '<div class="card"><div class="label">' + S.esc(a.status || '') + '</div><h3 class="mono">' + S.esc(label) + '</h3><p class="muted mono">' + S.esc(a.real_email || '') + '</p><p class="muted mono">' + S.esc(mailText) + '</p><p class="muted mono">aliases ' + (a.alias_total || 0) + ' / active ' + (a.alias_active || 0) + '</p><div class="btn-row"><button class="btn btn-outline btn-sm" onclick="HME.navigate(' + S.inlineArg(mailboxRoute) + ')">查看邮箱</button><button class="btn btn-outline btn-sm" onclick="HME.showEditAccountModal(' + S.inlineArg(a.id) + ')">编辑</button><button class="btn btn-outline btn-sm" onclick="HME.showMailSettingsModal(' + S.inlineArg(a.id) + ')">邮件登录</button><button class="btn btn-outline btn-sm" onclick="HME.validateAccount(' + S.inlineArg(a.id) + ')">校验</button><button class="btn btn-danger btn-sm" onclick="HME.removeAccount(' + S.inlineArg(a.id) + ',' + S.inlineArg(label) + ')">删除</button></div></div>';
    }).join('');
    S.view('<div class="grid cards">' + cards + '</div><div style="height:18px"></div><div class="grid cards">' + (accountCards || '<div class="card muted">暂无账号</div>') + '</div>');
  };

  S.showAddAccountModal = function(){
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> 导入 iCloud Cookie</h3><p class="muted">支持 Cookie Editor 的 Header String 或 JSON。</p><label class="label">账号名称</label><input id="accNameInput" placeholder="账号名称"><label class="label">Cookie / session 数据</label><textarea id="cookieInput" placeholder="name=value; name2=value2"></textarea><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button id="addAccountSubmit" class="btn" onclick="HME.addAccount()">添加并校验</button></div><div id="modalMsg" class="warning"></div></div></div>';
  };
  S.closeModal = function(){ S.E('modalRoot').innerHTML = ''; };
  S.addAccount = async function(){
    if (addAccountPending) return;
    const submit = S.E('addAccountSubmit');
    const msg = S.E('modalMsg');
    addAccountPending = true;
    if (submit) {
      submit.disabled = true;
      submit.textContent = '添加中...';
    }
    if (msg) msg.textContent = '';
    try {
      const name = S.E('accNameInput').value.trim() || '未命名账号';
      const cookie_input = S.E('cookieInput').value.trim();
      if (!cookie_input) throw new Error('请粘贴 Cookie');
      await S.api('/api/accounts/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, cookie_input})});
      S.closeModal();
      S.toast('账号已添加');
      S.refreshAll();
    } catch (err) {
      if (msg) msg.textContent = err.message;
      if (submit) {
        submit.disabled = false;
        submit.textContent = '添加并校验';
      }
    } finally {
      addAccountPending = false;
    }
  };
  S.validateAccount = async function(id){
    try {
      await S.api('/api/accounts/' + encodeURIComponent(id) + '/validate', {method:'POST'});
      S.toast('校验完成');
      S.refreshAll();
    } catch (err) { S.toast(err.message, true); }
  };
  S.removeAccount = async function(id, label){
    if (!confirm('确认删除账号 ' + (label || id) + '？此操作只删除本地账号配置。')) return;
    try {
      await S.api('/api/accounts/' + encodeURIComponent(id) + '/remove', {method:'POST'});
      S.toast('账号已删除');
      S.refreshAll();
    } catch (err) { S.toast(err.message, true); }
  };
  S.showEditAccountModal = async function(id){
    try {
      const data = await S.api('/api/accounts/' + encodeURIComponent(id) + '/session');
      const account = data.account || {};
      S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> 编辑账号 Session</h3><p class="muted">这里会显示并更新本机保存的 Cookie / session 数据。内容敏感，不要分享。</p><label class="label">账号名称</label><input id="editAccountNameInput" placeholder="账号名称" value="' + S.esc(account.name || '') + '"><label class="label">iCloud Host</label><input id="editAccountHostInput" placeholder="icloud.com" value="' + S.esc(account.host || 'icloud.com') + '"><label class="label">Cookie / session 数据</label><textarea id="editAccountSessionInput" placeholder="name=value; name2=value2">' + S.esc(account.cookie_input || '') + '</textarea><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button id="editAccountSubmit" class="btn" onclick="HME.saveAccountSession(' + S.inlineArg(id) + ')">保存并校验</button></div><div id="modalMsg" class="warning"></div></div></div>';
    } catch (err) { S.toast(err.message, true); }
  };
  S.saveAccountSession = async function(id){
    const submit = S.E('editAccountSubmit');
    const msg = S.E('modalMsg');
    if (submit) {
      submit.disabled = true;
      submit.textContent = '保存中...';
    }
    if (msg) msg.textContent = '';
    try {
      const name = S.E('editAccountNameInput').value.trim() || '未命名账号';
      const host = S.E('editAccountHostInput').value.trim() || 'icloud.com';
      const cookie_input = S.E('editAccountSessionInput').value.trim();
      if (!cookie_input) throw new Error('请填写 Cookie / session 数据');
      const res = await S.api('/api/accounts/' + encodeURIComponent(id) + '/session', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, host, cookie_input})});
      S.closeModal();
      S.toast(res.account && res.account.status === 'error' ? '账号已更新，校验失败' : '账号已更新');
      S.refreshAll();
    } catch (err) {
      if (msg) msg.textContent = err.message;
      if (submit) {
        submit.disabled = false;
        submit.textContent = '保存并校验';
      }
    }
  };
  S.showMailSettingsModal = function(id){
    const account = S.accounts.find(a => a.id === id) || {};
    const configs = S.imapConfigs || [];
    const selectedId = account.imap_config_id || '';
    const email = account.mail_email || account.real_email || '';
    const host = account.mail_host || '';
    const port = account.mail_port || 993;
    const options = ['<option value="">新建 IMAP 配置</option>'].concat(configs.map(config => '<option value="' + S.esc(config.id) + '"' + (config.id === selectedId ? ' selected' : '') + '>' + S.esc(config.name || config.email || config.id) + ' · ' + S.esc(config.host || '') + '</option>')).join('');
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> 邮件登录</h3><p class="muted">HME 邮件会转发到接收邮箱。可新建 IMAP 配置，或选择配置中心里已保存的 IMAP。</p><label class="label">IMAP 配置</label><select id="mailConfigSelect" onchange="HME.applyMailConfigSelection()">' + options + '</select><label class="label">配置名称</label><input id="mailConfigNameInput" placeholder="例如 QQ 收件箱" value="' + S.esc(account.imap_config_name || email) + '"><label class="label">接收邮箱</label><input id="mailEmailInput" placeholder="name@example.com" value="' + S.esc(email) + '"><label class="label">IMAP 服务器</label><input id="mailHostInput" placeholder="imap.example.com" value="' + S.esc(host) + '"><label class="label">端口</label><input id="mailPortInput" placeholder="993" value="' + S.esc(port) + '"><label class="label">邮箱授权码或密码</label><input id="mailPasswordInput" type="password" autocomplete="new-password" placeholder="新配置需要填写授权码或密码"><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button class="btn btn-outline" onclick="HME.navigate(\'#/imap-configs\');HME.closeModal()">配置中心</button><button class="btn" onclick="HME.saveMailSettings(' + S.inlineArg(id) + ')">保存并测试</button></div><div id="modalMsg" class="warning"></div></div></div>';
    S.applyMailConfigSelection();
  };
  S.applyMailConfigSelection = function(){
    const select = S.E('mailConfigSelect');
    if (!select) return;
    const config = (S.imapConfigs || []).find(item => item.id === select.value);
    const fields = [S.E('mailConfigNameInput'), S.E('mailEmailInput'), S.E('mailHostInput'), S.E('mailPortInput')];
    if (config) {
      if (fields[0]) fields[0].value = config.name || '';
      if (fields[1]) fields[1].value = config.email || '';
      if (fields[2]) fields[2].value = config.host || '';
      if (fields[3]) fields[3].value = config.port || 993;
    }
    fields.forEach(field => { if (field) field.disabled = Boolean(config); });
    const password = S.E('mailPasswordInput');
    if (password) {
      password.value = '';
      password.disabled = Boolean(config);
      password.placeholder = config ? '使用已保存密码' : '新配置需要填写授权码或密码';
    }
  };
  S.saveMailSettings = async function(id){
    try {
      let imap_config_id = S.E('mailConfigSelect') ? S.E('mailConfigSelect').value : '';
      if (!imap_config_id) {
        const email = S.E('mailEmailInput').value.trim();
        const host = S.E('mailHostInput').value.trim();
        const port = S.E('mailPortInput').value.trim() || '993';
        const password = S.E('mailPasswordInput').value.trim();
        const name = S.E('mailConfigNameInput').value.trim() || email;
        if (!email) throw new Error('请填写接收邮箱');
        if (!password) throw new Error('请填写邮箱授权码或密码');
        const created = await S.api('/api/imap-configs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, email, host, port, password})});
        imap_config_id = created.config && created.config.id;
      }
      if (!imap_config_id) throw new Error('IMAP 配置保存失败');
      const res = await S.api('/api/accounts/' + encodeURIComponent(id) + '/mail-settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({imap_config_id})});
      if (!res.ok) throw new Error((res.mail && res.mail.error) || res.error || '邮件登录未通过');
      S.closeModal();
      S.toast('邮件登录已通过');
      S.refreshAll();
    } catch (err) { S.E('modalMsg').textContent = err.message; }
  };
})();
