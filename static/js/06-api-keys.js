(function(){
  const S = window.HME;
  const STORAGE_KEY = 'icloud_hme_api_key';

  function storedKey(){
    try { return localStorage.getItem(STORAGE_KEY) || ''; }
    catch (_) { return ''; }
  }

  function storeKey(value){
    try {
      if (value) localStorage.setItem(STORAGE_KEY, value);
      else localStorage.removeItem(STORAGE_KEY);
    } catch (_) {}
  }

  function authHeaders(){
    const key = storedKey();
    return key ? {'Authorization':'Bearer ' + key} : {};
  }

  async function keyApi(path, opts){
    const options = Object.assign({}, opts || {});
    options.headers = Object.assign({}, authHeaders(), options.headers || {});
    const res = await fetch(path, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
  }

  function keyRows(keys){
    if (!keys.length) return '<tr><td colspan="5" class="muted">暂无 API Key 记录；如系统已有 key，请先在上方输入并保存。</td></tr>';
    return keys.map(k => '<tr><td class="mono">' + S.esc(k.id) + '</td><td>' + S.esc(k.name || '') + '</td><td class="mono">' + S.esc(k.prefix || '') + '</td><td>' + (k.active ? '<span class="badge ok">active</span>' : '<span class="badge err">revoked</span>') + '</td><td>' + (k.active ? '<button class="btn btn-outline btn-sm" onclick="HME.revokeApiKey(' + S.inlineArg(k.id) + ')">吊销</button>' : '') + '</td></tr>').join('');
  }

  S.renderApiKeys = async function(){
    S.setTitle('API Key');
    const current = storedKey();
    S.view('<div class="panel"><div class="panel-head">API Key 管理</div><div class="panel-body"><p class="muted">API Key 用于外部 <code>/api/v1/*</code> 调用。明文只在创建时返回一次；这里可临时保存到当前浏览器本机。</p><label class="label">当前 API Key</label><input id="apiKeyInput" type="password" placeholder="hme_xxx" value="' + S.esc(current) + '"><div class="btn-row"><button class="btn btn-outline btn-sm" onclick="HME.saveApiKeyInput()">保存到本机</button><button class="btn btn-outline btn-sm" onclick="HME.clearApiKeyInput()">清除本机保存</button><button class="btn btn-outline btn-sm" onclick="HME.loadApiKeys()">加载列表</button></div><hr><label class="label">新建 Key 名称</label><input id="apiKeyNameInput" placeholder="default"><div class="btn-row"><button class="btn btn-primary btn-sm" onclick="HME.createApiKey()">创建 API Key</button></div><div id="apiKeyMsg" class="warning" style="margin-top:12px"></div></div></div><div class="panel"><div class="panel-head">已有 API Key</div><div class="panel-body"><table class="table"><thead><tr><th>ID</th><th>名称</th><th>前缀</th><th>状态</th><th>操作</th></tr></thead><tbody id="apiKeyRows"><tr><td colspan="5" class="muted">点击“加载列表”查看。</td></tr></tbody></table></div></div>');
    if (current) await S.loadApiKeys(false);
  };

  S.saveApiKeyInput = function(silent){
    const value = S.E('apiKeyInput').value.trim();
    storeKey(value);
    if (!silent) S.toast(value ? 'API Key 已保存到本机浏览器' : 'API Key 已清除');
  };

  S.clearApiKeyInput = function(){
    storeKey('');
    if (S.E('apiKeyInput')) S.E('apiKeyInput').value = '';
    if (S.E('apiKeyRows')) S.E('apiKeyRows').innerHTML = '<tr><td colspan="5" class="muted">已清除本机保存。</td></tr>';
    S.toast('API Key 已清除');
  };

  S.loadApiKeys = async function(showToast){
    try {
      S.saveApiKeyInput(true);
      const data = await keyApi('/api/keys');
      S.E('apiKeyRows').innerHTML = keyRows(data.keys || []);
      if (showToast !== false) S.toast('API Key 列表已加载');
    } catch (err) {
      S.E('apiKeyRows').innerHTML = '<tr><td colspan="5">' + S.error(err) + '</td></tr>';
    }
  };

  S.createApiKey = async function(){
    try {
      const name = S.E('apiKeyNameInput').value.trim() || 'default';
      const data = await keyApi('/api/keys', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})});
      const apiKey = data.key && data.key.api_key;
      if (!apiKey) throw new Error('响应缺少 api_key');
      S.E('apiKeyInput').value = apiKey;
      storeKey(apiKey);
      S.E('apiKeyMsg').innerHTML = '<strong>请立即复制保存，明文只显示这一次：</strong><input readonly value="' + S.esc(apiKey) + '"><div class="btn-row"><button class="btn btn-outline btn-sm" onclick="navigator.clipboard.writeText(HME.E(\'apiKeyInput\').value);HME.toast(\'已复制\')">复制</button></div>';
      await S.loadApiKeys(false);
      S.toast('API Key 已创建并保存到本机浏览器');
    } catch (err) {
      S.E('apiKeyMsg').textContent = err.message;
      S.toast(err.message, true);
    }
  };

  S.revokeApiKey = async function(id){
    try {
      await keyApi('/api/keys/' + encodeURIComponent(id) + '/revoke', {method:'POST'});
      S.toast('API Key 已吊销');
      await S.loadApiKeys(false);
    } catch (err) { S.toast(err.message, true); }
  };
})();
