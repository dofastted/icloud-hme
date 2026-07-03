(function(){
  const S = window.HME;

  S.renderBatch = function(){
    S.setTitle('批量创建');
    const checks = S.accounts.map(a => '<label><input type="checkbox" name="batchAcc" value="' + S.esc(a.id) + '"> ' + S.esc(a.name || a.id) + '</label>').join('<br>');
    S.view('<div class="panel"><div class="panel-head">批量创建</div><div class="panel-body">' + (checks || S.empty('暂无账号')) + '<div style="height:14px"></div><input id="batchCount" type="number" min="1" max="20" value="1"> <input id="batchLabel" placeholder="标签，可选"> <button class="btn" onclick="HME.runBatch()">开始创建</button><div id="batchResult" class="muted mono" style="margin-top:14px"></div></div></div>');
  };
  S.runBatch = async function(){
    try {
      const account_ids = Array.from(document.querySelectorAll('input[name=batchAcc]:checked')).map(x => x.value);
      if (!account_ids.length) throw new Error('请选择账号');
      const data = await S.api('/api/create-batch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({account_ids, count_per_account:Number(S.E('batchCount').value || 1), label:S.E('batchLabel').value || ''})});
      S.E('batchResult').textContent = '成功 ' + data.total_created + '，失败 ' + data.total_errors;
      S.refreshAll();
    } catch (err) { S.E('batchResult').textContent = err.message; }
  };

  S.renderInbox = function(){
    S.setTitle('收件箱');
    const options = '<option value="">选择账号</option>' + S.accounts.map(a => '<option value="' + S.esc(a.id) + '">' + S.esc(a.name || a.id) + '</option>').join('');
    S.view('<div class="panel"><div class="panel-head">收件箱</div><div class="panel-body"><select id="inboxAccount">' + options + '</select> <input id="inboxAlias" placeholder="可选：指定 alias@icloud.com"> <button class="btn btn-outline btn-sm" onclick="HME.loadInbox()">读取</button><div id="inboxResult" style="margin-top:16px">' + S.empty('请选择账号后读取') + '</div></div></div>');
  };
  S.loadInbox = async function(){
    const acc = S.E('inboxAccount').value;
    const alias = S.E('inboxAlias').value.trim();
    if (!acc) { S.E('inboxResult').innerHTML = S.empty('请先选择账号'); return; }
    S.E('inboxResult').innerHTML = S.loading(3);
    try {
      const path = alias ? '/api/accounts/' + encodeURIComponent(acc) + '/mail/' + encodeURIComponent(alias) + '?limit=20' : '/api/accounts/' + encodeURIComponent(acc) + '/inbox?limit=20';
      const data = await S.api(path);
      const msgs = data.emails || [];
      if (!msgs.length) { S.E('inboxResult').innerHTML = S.empty(data.error || '暂无邮件'); return; }
      S.E('inboxResult').innerHTML = '<table class="table"><tbody>' + msgs.map(m => '<tr><td><strong>' + S.esc(m.subject || '(无主题)') + '</strong><br><span class="muted">' + S.esc(m.from || '') + '</span></td><td>' + S.esc(m.date || '') + '</td></tr>').join('') + '</tbody></table>';
    } catch (err) { S.E('inboxResult').innerHTML = S.error(err); }
  };

  S.renderDocs = function(){
    S.setTitle('API 文档');
    const sections = [
      ['管理端 API（本地 UI，无 API Key）', [
        ['GET','/api/mailboxes','邮箱列表，支持 q/account_id/status'],
        ['GET','/api/mailboxes/{alias}/messages','指定邮箱邮件，默认最新一封'],
        ['POST','/api/mailboxes/{alias}/share','生成兑换码，明文仅返回一次'],
        ['GET','/api/shared','共享列表'],
        ['POST','/api/shared/{id}/revoke','吊销兑换码']
      ]],
      ['外部 v1 API（必须 API Key）', [
        ['UI','#/api-keys','创建、保存、查看、吊销 API Key'],
        ['GET','/api/v1/config','主配置入口'],
        ['GET','/api/v1/hme/available','全局可用 HME'],
        ['GET','/api/v1/hme/available/next','取一个可用 HME'],
        ['GET','/api/v1/hme/{alias}/latest','读取指定 HME 最新邮件'],
        ['POST','/api/v1/shared-mailboxes','创建兑换码，body: {"alias_email":"..."}'],
        ['POST','/api/v1/shared-mailboxes/{id}/revoke','吊销兑换码']
      ]],
      ['公网 shared（仅兑换码）', [
        ['GET','/shared','共享主入口'],
        ['POST','/api/shared/latest','按兑换码只返回最新一封，限流，脱敏']
      ]]
    ];
    const html = sections.map(sec => '<div class="panel"><div class="panel-head">' + S.esc(sec[0]) + '</div><div class="panel-body">' + sec[1].map(i => '<p><span class="badge ok">' + i[0] + '</span> <code>' + S.esc(i[1]) + '</code><br><span class="muted">' + S.esc(i[2]) + '</span></p>').join('') + '</div></div>').join('');
    S.view('<p class="muted">管理端口不应直接暴露公网；外部自动化请使用 API Key；shared 入口使用兑换码。</p>' + html);
  };

  S.renderLogs = function(){
    S.setTitle('运行日志');
    S.view('<div class="panel"><div class="panel-head"><span>运行日志</span><button class="btn btn-outline btn-sm" onclick="HME.logs=[];HME.renderLogs()">清空</button></div><div class="panel-body mono" id="logFeed">' + (S.logs.length ? S.logs.map(l => '<div>' + S.esc(l.time) + ' ' + S.esc(l.level) + ' ' + S.esc(l.msg) + '</div>').join('') : S.empty('等待日志')) + '</div></div>');
  };

  function connectLogs(){
    try {
      const es = new EventSource('/api/log-stream');
      es.onmessage = (event) => {
        try {
          S.logs.push(JSON.parse(event.data));
          if (S.logs.length > 300) S.logs = S.logs.slice(-300);
          if ((location.hash || '').startsWith('#/logs')) S.renderLogs();
        } catch (_) {}
      };
      es.onerror = () => { es.close(); setTimeout(connectLogs, 5000); };
    } catch (_) {}
  }
  connectLogs();
})();
