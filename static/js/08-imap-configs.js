(function(){
  const S = window.HME;

  function configArg(value){ return S.inlineArg(value); }

  function byId(id){
    return (S.imapConfigs || []).find(config => config.id === id) || null;
  }

  function renderTestResult(result){
    if (!result) return '';
    const mail = result.mail || result;
    const okText = result.ok || mail.ok ? '通过' : '失败';
    const detail = mail.error || mail.message || ('服务器 ' + (mail.server || '-') + ':' + (mail.port || '-'));
    return '<div class="panel" style="margin-top:14px"><div class="panel-head"><span>IMAP 测试 ' + okText + '</span></div><div class="panel-body muted mono">' + S.esc(detail) + '</div></div>';
  }

  S.renderImapConfigs = async function(){
    S.setTitle('IMAP 配置');
    S.view(S.loading(4));
    try {
      const data = await S.api('/api/imap-configs');
      S.imapConfigs = data.configs || [];
      const rows = S.imapConfigs.map((config, index) => {
        const idArg = configArg(config.id);
        const usedCount = (S.accounts || []).filter(account => account.imap_config_id === config.id).length;
        const status = config.has_password ? '已保存密码' : '缺少密码';
        return '<tr><td>' + (index + 1) + '</td><td>' + S.esc(config.name || config.id) + '</td><td class="mono">' + S.esc(config.email || '') + '</td><td class="mono">' + S.esc(config.host || '') + '</td><td>' + S.esc(config.port || 993) + '</td><td>' + status + '</td><td>' + usedCount + '</td><td><button class="btn btn-outline btn-sm" onclick="HME.testImapConfig(' + idArg + ')">测试</button> <button class="btn btn-outline btn-sm" onclick="HME.showImapConfigModal(' + idArg + ')">编辑</button> <button class="btn btn-danger btn-sm" onclick="HME.deleteImapConfig(' + idArg + ')">删除</button></td></tr>';
      }).join('');
      const body = rows || '<tr><td colspan="8" class="muted">暂无 IMAP 配置</td></tr>';
      S.view('<div class="panel"><div class="panel-head"><span>IMAP 配置中心</span><button class="btn btn-sm" onclick="HME.showImapConfigModal()">新建 IMAP</button></div><div class="panel-body muted">保存接收邮箱的 IMAP 登录；账号邮件登录可直接选择这些配置。密码只保存在本机，不在列表中显示。</div><div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>名称</th><th>邮箱</th><th>服务器</th><th>端口</th><th>状态</th><th>账号数</th><th>操作</th></tr></thead><tbody>' + body + '</tbody></table></div></div><div id="imapConfigResult"></div>');
    } catch (err) {
      S.view(S.error(err, 'HME.renderImapConfigs()'));
    }
  };

  S.showImapConfigModal = function(id){
    const config = id ? byId(id) : null;
    const title = config ? '编辑 IMAP 配置' : '新建 IMAP 配置';
    const passwordHint = config ? '留空则保留已保存密码' : '邮箱授权码或密码';
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> ' + title + '</h3><label class="label">配置名称</label><input id="imapNameInput" placeholder="例如 163 收件箱" value="' + S.esc(config && config.name || '') + '"><label class="label">登录邮箱</label><input id="imapEmailInput" placeholder="name@example.com" value="' + S.esc(config && config.email || '') + '"><label class="label">IMAP 服务器</label><input id="imapHostInput" placeholder="imap.example.com" value="' + S.esc(config && config.host || '') + '"><label class="label">端口</label><input id="imapPortInput" placeholder="993" value="' + S.esc(config && config.port || 993) + '"><label class="label">邮箱授权码或密码</label><input id="imapPasswordInput" type="password" autocomplete="new-password" placeholder="' + S.esc(passwordHint) + '"><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button class="btn" onclick="HME.saveImapConfig(' + configArg(id || '') + ')">保存</button></div><div id="modalMsg" class="warning"></div></div></div>';
  };

  S.saveImapConfig = async function(id){
    const payload = {
      name: S.E('imapNameInput').value.trim(),
      email: S.E('imapEmailInput').value.trim(),
      host: S.E('imapHostInput').value.trim(),
      port: S.E('imapPortInput').value.trim() || '993',
      password: S.E('imapPasswordInput').value.trim(),
    };
    if (!payload.email) { S.E('modalMsg').textContent = '登录邮箱不能为空'; return; }
    if (!id && !payload.password) { S.E('modalMsg').textContent = '新建配置必须填写邮箱授权码或密码'; return; }
    try {
      const path = id ? '/api/imap-configs/' + encodeURIComponent(id) : '/api/imap-configs';
      await S.api(path, {method:id ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      S.closeModal();
      S.toast('IMAP 配置已保存');
      await S.refreshAll();
    } catch (err) {
      S.E('modalMsg').textContent = err.message;
    }
  };

  S.deleteImapConfig = async function(id){
    if (!confirm('确认删除该 IMAP 配置？已绑定账号的配置不能删除。')) return;
    try {
      await S.api('/api/imap-configs/' + encodeURIComponent(id), {method:'DELETE'});
      S.toast('IMAP 配置已删除');
      await S.refreshAll();
    } catch (err) {
      S.toast(err.message, true);
    }
  };

  S.testImapConfig = async function(id){
    const box = S.E('imapConfigResult');
    if (box) box.innerHTML = '<div class="panel"><div class="panel-body muted">正在测试 IMAP...</div></div>';
    try {
      const result = await S.api('/api/imap-configs/' + encodeURIComponent(id) + '/test', {method:'POST'});
      if (box) box.innerHTML = renderTestResult(result);
    } catch (err) {
      if (box) box.innerHTML = renderTestResult({ok:false, mail:{error:err.message}});
    }
  };
})();
