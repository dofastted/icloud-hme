(function(){
  const S = window.HME;
  S.logPaused = false;
  S.logDate = '';
  let batchRunning = false;

  S.renderBatch = function(){
    S.setTitle('批量创建');
    const checks = S.accounts.map(a => '<label><input type="checkbox" name="batchAcc" value="' + S.esc(a.id) + '"' + (a.status === 'active' ? ' checked' : ' disabled') + '> ' + S.esc(a.name || a.id) + (a.status === 'active' ? '' : ' <span class="badge err">' + S.esc(a.status || '不可用') + '</span>') + '</label>').join('<br>');
    S.view('<div class="panel"><div class="panel-head">批量创建</div><div class="panel-body"><form id="batchForm">' + (checks || S.empty('暂无账号')) + '<div style="height:14px"></div><input id="batchCount" type="number" min="1" max="20" value="1"> <input id="batchLabel" placeholder="标签，可选"> <button id="batchRun" type="submit" class="btn">开始创建</button><div id="batchResult" class="muted mono" style="margin-top:14px"></div></form></div></div><div class="panel"><div class="panel-head"><span>运行日志</span><span class="muted mono" id="batchProgress"></span></div><div class="panel-body mono" id="batchFeed" style="max-height:340px;overflow:auto">' + S.empty('开始创建后在此实时显示') + '</div></div>');
    const form = S.E('batchForm');
    if (form) form.addEventListener('submit', event => { event.preventDefault(); S.runBatch(); });
  };

  function batchAppend(entry){
    const feed = S.E('batchFeed');
    if (!feed) return;
    if (feed.querySelector('.empty')) feed.innerHTML = '';
    feed.insertAdjacentHTML('beforeend', logLine(entry));
    feed.scrollTop = feed.scrollHeight;
  }

  S.runBatch = async function(){
    if (batchRunning) return;
    const btn = S.E('batchRun');
    const result = S.E('batchResult');
    try {
      const account_ids = Array.from(document.querySelectorAll('input[name=batchAcc]:checked')).map(x => x.value);
      if (!account_ids.length) throw new Error('请选择账号');
      const count = Number(S.E('batchCount').value || 1);
      batchRunning = true;
      if (btn) { btn.disabled = true; btn.textContent = '创建中...'; }
      S.E('batchFeed').innerHTML = '';
      result.textContent = '';
      const summary = await streamBatch({account_ids, count_per_account:count, label:S.E('batchLabel').value || ''});
      result.textContent = '成功 ' + summary.total_created + '，失败 ' + summary.total_errors;
      S.toast(summary.ok ? ('创建 ' + summary.total_created + ' 个') : '创建失败，详见日志', !summary.ok);
      S.refreshAll();
    } catch (err) {
      result.textContent = err.message;
      S.toast(err.message, true);
    } finally {
      batchRunning = false;
      if (btn) { btn.disabled = false; btn.textContent = '开始创建'; }
    }
  };

  async function streamBatch(payload){
    const res = await fetch('/api/create-batch-stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let done = null;
    let failed = null;
    let created = 0;
    const planned = payload.account_ids.length * payload.count_per_account;
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, {stream:true});
      const parts = buffer.split('\n\n');
      buffer = parts.pop() || '';
      for (const part of parts) {
        const line = part.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let evt;
        try { evt = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
        if (evt.type === 'error') { failed = evt.error; continue; }
        if (evt.type === 'done') { done = evt; continue; }
        if (evt.log) batchAppend(evt.log);
        if (evt.type === 'item') {
          if (evt.ok) created += 1;
          const prog = S.E('batchProgress');
          if (prog) prog.textContent = created + ' / ' + planned;
        }
      }
    }
    if (failed) throw new Error(failed);
    if (!done) throw new Error('连接中断，请查看运行日志');
    return done;
  }

  function inboxTable(msgs){
    return '<table class="table"><tbody>' + msgs.map(m => '<tr><td><strong>' + S.esc(m.subject || '(无主题)') + '</strong><br><span class="muted">' + S.esc(m.from || '') + '</span><br><span class="muted mono">To: ' + S.esc(m.to || '') + '</span></td><td>' + S.esc(m.date || '') + '</td></tr>').join('') + '</tbody></table>';
  }

  function renderImapTest(mail){
    const messages = mail.messages || [];
    const status = mail.ok ? '<span class="badge ok">OK</span>' : '<span class="badge err">FAIL</span>';
    const rows = [
      '邮箱: ' + (mail.email || ''),
      '服务器: ' + (mail.server || '') + ':' + (mail.port || ''),
      'INBOX 总数: ' + (mail.inbox_count == null ? '-' : mail.inbox_count),
      '最近读取: ' + (mail.recent_count || 0),
      '范围: 最近 ' + (mail.days || '-') + ' 天'
    ].map(S.esc).join('<br>');
    const detail = mail.error ? '<div class="warning">' + S.esc(mail.error) + '</div>' : '<p class="muted">' + S.esc(mail.message || '') + '</p>';
    return '<div class="card"><h3>IMAP 测试 ' + status + '</h3><p class="muted mono">' + rows + '</p>' + detail + (messages.length ? inboxTable(messages) : '') + '</div>';
  }

  S.renderInbox = function(){
    S.setTitle('收件箱');
    const options = '<option value="">选择账号</option>' + S.accounts.map(a => '<option value="' + S.esc(a.id) + '">' + S.esc(a.name || a.id) + '</option>').join('');
    S.view('<div class="panel"><div class="panel-head">收件箱</div><div class="panel-body"><select id="inboxAccount">' + options + '</select> <input id="inboxAlias" placeholder="可选：指定 alias@icloud.com"> <button class="btn btn-outline btn-sm" onclick="HME.testInboxImap()">测试 IMAP</button> <button class="btn btn-outline btn-sm" onclick="HME.loadInbox()">读取</button><div id="inboxResult" style="margin-top:16px">' + S.empty('请选择账号后读取') + '</div></div></div>');
  };

  S.loadInbox = async function(){
    const acc = S.E('inboxAccount').value;
    const alias = S.E('inboxAlias').value.trim();
    if (!acc) { S.E('inboxResult').innerHTML = S.empty('请先选择账号'); return; }
    S.E('inboxResult').innerHTML = S.loading(3);
    try {
      const path = alias ? '/api/accounts/' + encodeURIComponent(acc) + '/mail/' + encodeURIComponent(alias) + '?limit=20&force=1' : '/api/accounts/' + encodeURIComponent(acc) + '/inbox?limit=20&force=1';
      const data = await S.api(path);
      const msgs = data.emails || [];
      if (!msgs.length) { S.E('inboxResult').innerHTML = data.error ? S.error(data.error) : S.empty('暂无邮件，可点击“测试 IMAP”检查连接'); return; }
      S.E('inboxResult').innerHTML = inboxTable(msgs);
    } catch (err) { S.E('inboxResult').innerHTML = S.error(err); }
  };

  S.testInboxImap = async function(){
    const acc = S.E('inboxAccount').value;
    const alias = S.E('inboxAlias').value.trim();
    if (!acc) { S.E('inboxResult').innerHTML = S.empty('请先选择账号'); return; }
    S.E('inboxResult').innerHTML = S.loading(2);
    try {
      const res = await fetch('/api/accounts/' + encodeURIComponent(acc) + '/mail-settings/test', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({alias, limit:5, days:alias ? 30 : 7})});
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
      S.E('inboxResult').innerHTML = renderImapTest(data.mail || {ok:false,error:data.error || '测试失败'});
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

  function logLine(entry){
    const level = S.esc(entry.level || 'info');
    return '<div class="log-line log-' + level + '"><span class="muted">' + S.esc(entry.time || '') + '</span> <span class="badge ' + (level === 'warn' || level === 'error' ? 'err' : 'ok') + '">' + level + '</span> ' + S.esc(entry.msg || '') + '</div>';
  }

  function appendLog(entry){
    S.logs.push(entry);
    if (S.logs.length > 300) S.logs = S.logs.slice(-300);
    const feed = S.E('logFeed');
    if (!feed || !(location.hash || '').startsWith('#/logs') || S.logPaused) return;
    if (feed.innerHTML && feed.innerHTML.includes('empty')) feed.innerHTML = '';
    feed.insertAdjacentHTML('beforeend', logLine(entry));
    feed.scrollTop = feed.scrollHeight;
  }

  function logDateOptions(){
    const dates = S.logDates || [];
    if (!dates.length) return '';
    const opts = dates.map(d => '<option value="' + S.esc(d) + '"' + (S.logDate === d ? ' selected' : '') + '>' + S.esc(d.slice(0,4) + '-' + d.slice(4,6) + '-' + d.slice(6,8)) + '</option>').join('');
    return '<select id="logDate" onchange="HME.loadLogHistory(this.value)">' + opts + '</select> ';
  }

  S.renderLogs = function(){
    S.setTitle('运行日志');
    const rows = S.logs.length ? S.logs.map(logLine).join('') : S.empty('等待日志');
    const pauseLabel = S.logPaused ? '继续' : '暂停';
    const retention = S.logRetentionDays ? '<span class="muted mono" style="font-size:12px">保留 ' + S.logRetentionDays + ' 天</span> ' : '';
    S.view('<div class="panel"><div class="panel-head"><span>运行日志</span><span>' + retention + logDateOptions() + '<button class="btn btn-outline btn-sm" onclick="HME.loadLogHistory()">刷新</button> <button class="btn btn-outline btn-sm" onclick="HME.toggleLogPause()">' + pauseLabel + '</button> <button class="btn btn-outline btn-sm" onclick="HME.clearLogs()">清空</button></span></div><div class="panel-body mono" id="logFeed">' + rows + '</div></div>');
    const feed = S.E('logFeed');
    if (feed) feed.scrollTop = feed.scrollHeight;
  };

  S.clearLogs = function(){
    S.logs = [];
    S.renderLogs();
  };

  S.toggleLogPause = function(){
    S.logPaused = !S.logPaused;
    S.renderLogs();
  };

  S.loadLogHistory = async function(date){
    if (typeof fetch !== 'function') return;
    try {
      const target = typeof date === 'string' ? date : S.logDate;
      const res = await fetch('/api/logs?limit=500' + (target ? '&date=' + encodeURIComponent(target) : ''));
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + res.status));
      S.logs = data.logs || [];
      S.logDates = data.dates || [];
      S.logDate = data.date || '';
      S.logRetentionDays = data.retention_days || 0;
      if ((location.hash || '').startsWith('#/logs')) S.renderLogs();
    } catch (err) { S.toast && S.toast(err.message, true); }
  };

  function connectLogs(){
    try {
      const es = new EventSource('/api/log-stream');
      es.onmessage = (event) => {
        try { appendLog(JSON.parse(event.data)); } catch (_) {}
      };
      es.onerror = () => { es.close(); setTimeout(connectLogs, 5000); };
    } catch (_) {}
  }

  connectLogs();
  S.loadLogHistory();
})();
